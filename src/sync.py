"""Sync engine — keeps the agent in step with both Google Sheets.

On every run (and in --watch mode) it:
  1. fetches + validates both sheets (discards any that break the template),
  2. joins them on 'Session Name' to attach a session number to each deck link,
  3. diffs against the stored state to detect adds / removes / edits,
  4. (re)ingests only changed/new Google Slides decks into the knowledge base,
  5. writes a normalized course-structure cache the rest of the pipeline reads,
  6. returns a human-readable changelog.

State lives in knowledge_base/sync_state.json so change detection persists
across runs. Nothing from the past is dropped unless it is removed from a sheet.
"""
from __future__ import annotations
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import config, sheets, gslides, pptx_ingest

KB = pptx_ingest.KB_DIR
STATE = KB / "sync_state.json"
COURSE_CACHE = KB / "course_structure.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_name(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip()).lower()


def _split_takeaways(raw: str) -> list[str]:
    out = []
    for ln in re.split(r"[\n\r]+", str(raw or "")):
        ln = ln.strip().lstrip("-•").strip()
        if ln:
            out.append(ln)
    return out


def _col(row: dict, name: str) -> str:
    """Case/space-insensitive column access."""
    target = _norm_name(name)
    for k, v in row.items():
        if _norm_name(k) == target:
            return (v or "").strip()
    return ""


@dataclass
class SyncResult:
    ok: bool
    changelog: list[str] = field(default_factory=list)
    sessions: int = 0
    decks_ingested: int = 0
    decks_cached: int = 0
    errors: list[str] = field(default_factory=list)
    extraction_warnings: list[str] = field(default_factory=list)


def _load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {"sessions": {}, "decks": {}}


def _save_state(state: dict):
    KB.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def sync(course_link: str, details_link: str, *, verbose: bool = True, on_event=None) -> SyncResult:
    res = SyncResult(ok=True)

    def emit(msg: str):
        if verbose:
            print(f"[SYNC] {msg}", flush=True)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    # 1. fetch + validate (TemplateError propagates to the caller/wizard)
    emit("Reading Course Curriculum Structure sheet…")
    course = sheets.load_sheet(course_link, "course_structure")
    emit("Reading Session Details sheet…")
    details = sheets.load_sheet(details_link, "session_details")

    # 2. normalize course structure + build name -> session_no map
    sessions_norm = {}
    name_to_no = {}
    for row in course.rows:
        no_raw = _col(row, "Session")
        try:
            number = int(float(no_raw))
        except (ValueError, TypeError):
            continue
        name = _col(row, "Session Name")
        sessions_norm[str(number)] = {
            "number": number,
            "name": name,
            "topic": _col(row, "Topic Name"),
            "key_takeaways": _split_takeaways(_col(row, "Key Takeaways")),
        }
        if name:
            name_to_no[_norm_name(name)] = number
    COURSE_CACHE.write_text(json.dumps(sessions_norm, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    # 3. diff course structure vs previous state
    state = _load_state()
    prev_sessions = state.get("sessions", {})
    for sn, cur in sessions_norm.items():
        old = prev_sessions.get(sn)
        if old is None:
            res.changelog.append(f"+ Added session {sn}: {cur['name']}")
        elif old != cur:
            if old.get("name") != cur["name"]:
                res.changelog.append(f"~ Session {sn} renamed: '{old.get('name')}' -> '{cur['name']}'")
            if old.get("key_takeaways") != cur["key_takeaways"]:
                res.changelog.append(f"~ Session {sn} key takeaways changed")
    for sn, old in prev_sessions.items():
        if sn not in sessions_norm:
            res.changelog.append(f"- Removed session {sn}: {old.get('name')}")
    state["sessions"] = sessions_norm

    # 4. process the Session Details deck links (join on Session Name)
    prev_decks = state.get("decks", {})
    new_decks = {}
    seen_names = set()

    # build the task list (skip rows that can't be joined to a session)
    tasks = []
    for row in details.rows:
        name = _col(row, "Session Name")
        link = _col(row, "PPT Link")
        if not name or not link:
            continue
        key = _norm_name(name)
        seen_names.add(key)
        session_no = name_to_no.get(key)
        if session_no is None:
            res.errors.append(f"'{name}' in Session Details has no matching Session Name "
                              f"in the Course Structure — deck skipped.")
            continue
        tasks.append((key, name, link, session_no))

    # Download AND extract each deck inside the worker, so the multi-MB .pptx
    # bytes are released the moment the worker returns (only the small extracted
    # text is written to disk — never held in memory across decks). This caps
    # peak memory to ~`workers` decks instead of ALL of them, which is what a
    # 512 MB host (e.g. Render free) can survive. Workers touch only distinct
    # per-session files + read-only prev_decks; all shared bookkeeping happens in
    # the single main-thread loop below, so no locking is needed.
    def _fetch(task):
        key, name, link, session_no = task
        deck_key = f"session_{session_no:02d}"
        try:
            chash, data = gslides.content_hash(link)
        except Exception as e:
            return (task, None, "error", str(e))
        old = prev_decks.get(key)
        unchanged = old and old.get("link") == link and old.get("content_hash") == chash \
            and (pptx_ingest.DECKS_DIR / f"{deck_key}.json").exists()
        if unchanged:
            return (task, chash, "cached", None)
        try:
            deck = gslides.extract_from_bytes(data, session_no, name, link)
        except Exception as e:
            return (task, chash, "error", f"extract failed: {e}")
        data = None  # free the raw .pptx bytes before writing the extracted JSON
        pptx_ingest.DECKS_DIR.mkdir(parents=True, exist_ok=True)
        (pptx_ingest.DECKS_DIR / f"{deck_key}.json").write_text(
            json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        return (task, chash, "ingested", None)

    workers = config.harness()["context"].get("sync_max_workers", 6)
    total = len(tasks)
    if total:
        emit(f"Syncing {total} deck(s)…")
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch, t): t for t in tasks}
        done_n = 0
        for fut in as_completed(futures):
            done_n += 1
            task, chash, status, err = fut.result()
            key, name, link, session_no = task
            deck_key = f"session_{session_no:02d}"
            if status == "error":
                emit(f"[{done_n}/{total}] ⚠ unreadable — Session {session_no}: {name}")
                res.errors.append(f"Session {session_no} ('{name}'): {err}")
                new_decks[key] = prev_decks.get(key, {})  # keep old record if any
                continue
            emit(f"[{done_n}/{total}] ✓ synced — Session {session_no}: {name}")
            if status == "cached":
                res.decks_cached += 1
            else:  # ingested
                res.decks_ingested += 1
                old = prev_decks.get(key)
                if old is None:
                    res.changelog.append(f"+ Ingested deck for session {session_no}: {name}")
                elif old.get("link") != link:
                    res.changelog.append(f"~ Session {session_no} deck link changed -> re-ingested")
                else:
                    res.changelog.append(f"~ Session {session_no} deck content edited -> re-ingested")
            new_decks[key] = {"link": link, "content_hash": chash,
                              "session_no": session_no, "deck_key": deck_key}

    # decks removed from the sheet
    for key, old in prev_decks.items():
        if key not in seen_names:
            dk = old.get("deck_key")
            if dk:
                (pptx_ingest.DECKS_DIR / f"{dk}.json").unlink(missing_ok=True)
            res.changelog.append(f"- Session {old.get('session_no')} deck removed from sheet")
    state["decks"] = new_decks
    state["last_sync"] = _now()
    state["course_link"] = course_link
    state["details_link"] = details_link
    _save_state(state)

    # extraction-completeness check across all decks now in the KB (guideline 2/3)
    try:
        rep = pptx_ingest.completeness_report()
        for d in rep["decks"]:
            if not d["ok"]:
                res.extraction_warnings.append(
                    f"Session {d['session_no']} deck ({d['source_file']}): "
                    + "; ".join(d["issues"]))
    except Exception as e:
        res.extraction_warnings.append(f"extraction check skipped: {e}")

    res.sessions = len(sessions_norm)
    if verbose:
        _print_report(res)
    return res


def _print_report(res: SyncResult):
    print(f"[SYNC] {res.sessions} sessions | decks: {res.decks_ingested} (re)ingested, "
          f"{res.decks_cached} cached")
    if res.changelog:
        print("[SYNC] changes since last sync:")
        for c in res.changelog:
            print(f"       {c}")
    else:
        print("[SYNC] no changes since last sync.")
    for e in res.errors:
        print(f"[SYNC] ⚠ {e}")


def last_links() -> tuple[str | None, str | None]:
    state = _load_state()
    return state.get("course_link"), state.get("details_link")
