"""Curriculum store + deck ingestion.

WHAT CHANGED AND WHY. The Google Sheet used to BE the curriculum: every visit meant
pasting the link again, re-reading the sheet, and — the expensive part — re-downloading
every deck the course has ever had, just to discover that none of them had changed.
Google's Slides export endpoint offers no ETag, no Last-Modified and sends
`Cache-Control: no-store`, so "has this deck changed?" cannot be asked cheaply: the only
way to compute a content hash is to fetch the whole file. Measured on this course, that
is ~4.7 MB and ~3.4 s per deck, 29 decks, single-threaded — about a hundred seconds of
downloading per sync to learn nothing.

So the model is inverted:

  * The SHEET IS AN IMPORT FORMAT. Paste it once (or explicitly re-import later) and its
    rows are loaded into the `curriculum` table. After that the agent owns the
    curriculum and it is edited in the app.
  * A DECK IS FETCHED ONCE. The row records the content hash of the deck extracted from
    its link. A row whose link is unchanged and whose deck is already extracted is
    skipped entirely — no request at all. Only a NEW or CHANGED link costs a download,
    which is exactly the work that has to happen.
  * Editing a takeaway never touches a deck; replacing a link marks that one row pending.

`ingest_decks(force=True)` re-fetches everything, for the one case the cheap path cannot
cover: somebody edited the SLIDES behind a link that did not change.

The knowledge base on disk (extracted deck JSON + the course-structure cache) is still
written exactly as before, so pptx_ingest's retrieval, the taught index and the offline
loaders are unaffected.
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


def _course() -> str:
    from . import app_settings
    return app_settings.course_name() or "default"


# --------------------------------------------------------------------------- #
# 1. IMPORT — read a sheet into the curriculum table. One-time, or on request.
# --------------------------------------------------------------------------- #
def import_sheet(course_link: str, course: str | None = None, *,
                 replace: bool = False, verbose: bool = True, on_event=None) -> dict:
    """Load the sheet's rows into the curriculum table. Returns the import counts.

    MERGE by default: a re-import refreshes names/takeaways/links but keeps rows the
    sheet no longer has, and — because a row whose link is unchanged keeps its deck
    hash — re-importing does not re-download a single deck. `replace=True` also drops
    sessions missing from the sheet.
    """
    from . import db
    course = course or _course()

    def emit(msg: str):
        if verbose:
            print(f"[SYNC] {msg}", flush=True)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    emit("Reading the Course Curriculum Structure sheet…")
    sheet = sheets.load_sheet(course_link, "course_structure")
    ppt_col = config.harness()["sheet_templates"]["course_structure"].get(
        "ppt_link_column", "PPT Links")

    rows = []
    for row in sheet.rows:
        no_raw = _col(row, "Session")
        try:
            number = int(float(no_raw))
        except (ValueError, TypeError):
            continue
        rows.append({
            "session_no": number,
            "topic": _col(row, "Topic Name"),
            "session_name": _col(row, "Session Name"),
            "key_takeaways": _split_takeaways(_col(row, "Key Takeaways")),
            "ppt_link": _col(row, ppt_col),
        })
    res = db.curriculum_import(course, rows, replace=replace)
    emit(f"Imported {len(rows)} session(s) into the agent: "
         f"{res['added']} added, {res['updated']} updated"
         + (f", {res['removed']} removed" if res.get("removed") else ""))
    adopted = adopt_existing_decks(course)
    if adopted:
        emit(f"Recognised {adopted} deck(s) this agent had already extracted — "
             f"they will not be downloaded again.")
    write_course_cache(course)
    state = _load_state()
    state["course_link"] = course_link
    state["last_import"] = _now()
    state.pop("details_link", None)
    _save_state(state)
    return res


def prune_orphan_decks(course: str | None = None) -> list[int]:
    """Drop extracted decks the curriculum no longer claims. Returns the sessions cleared.

    Removing a session's PPT link used to leave its extracted text behind in the
    knowledge base, which is wrong twice over: the session stayed out of the
    "needs a TR doc" list even though its row said `no deck`, and the agent went on
    feeding that deck to the writer as material "already taught". The curriculum is the
    source of truth, so course memory has to follow it — a row with no link has no deck.

    Guarded against the empty case: with no curriculum rows this does nothing at all,
    so a process that has not loaded a course (or a database that has not been restored
    yet) can never wipe the knowledge base.
    """
    from . import db
    course = course or _course()
    rows = db.curriculum(course)
    if not rows:
        return []
    linked = {r["session_no"] for r in rows if (r.get("ppt_link") or "").strip()}
    known = {r["session_no"] for r in rows}
    cleared = []
    # THIS course's decks only. Globbing the whole store meant a row edited in one
    # course pruned another course's deck that happened to share a session number.
    for no in sorted(pptx_ingest.deck_session_numbers(course)):
        # Only sessions this curriculum actually covers: a deck belonging to a session
        # the course does not list is left alone rather than assumed to be rubbish.
        if no in known and no not in linked:
            pptx_ingest.drop_deck(course, no)
            cleared.append(no)
    if cleared:
        state = _load_state()
        state["decks"] = {k: v for k, v in (state.get("decks") or {}).items()
                          if v.get("session_no") not in cleared}
        _save_state(state)
    return cleared


def adopt_existing_decks(course: str | None = None) -> int:
    """Recognise decks this instance ALREADY extracted, so an upgrade costs nothing.

    Before the curriculum table existed, what-was-extracted lived in
    knowledge_base/sync_state.json (per deck: link + content hash) with the extracted
    text beside it in knowledge_base/decks/. A fresh table knows none of that, so on the
    first import every one of those decks would look new and be downloaded again —
    which is precisely the waste this change exists to remove.

    So each row with a link is matched against that older record: same link, extracted
    file present → adopt the stored hash and mark it extracted. Only an exact link match
    counts, because the hash describes the deck at THAT link.
    """
    from . import db
    course = course or _course()
    decks = _load_state().get("decks", {})
    by_link = {}
    for rec in decks.values():
        if isinstance(rec, dict) and rec.get("link"):
            by_link[rec["link"]] = rec
    adopted = 0
    for r in db.curriculum(course):
        link, no = (r.get("ppt_link") or "").strip(), r["session_no"]
        if not link or r.get("deck_hash"):
            continue
        rec = by_link.get(link)
        if not rec:
            continue
        if not pptx_ingest.has_deck(course, no):
            continue
        if db.curriculum_mark_deck(course, no, rec.get("content_hash") or "adopted"):
            adopted += 1
    return adopted


# --------------------------------------------------------------------------- #
# 2. CACHE — mirror the table to the JSON the offline loaders/RAG already read.
# --------------------------------------------------------------------------- #
def write_course_cache(course: str | None = None, rows: list | None = None) -> int:
    """Write knowledge_base/course_structure.json from the curriculum table.

    Kept because pptx_ingest, the offline loaders and the eval harness read this file;
    the table is authoritative and this is its projection, so nothing downstream had to
    learn about the database.

    `rows` lets a caller that has just read the curriculum pass it in rather than have
    this read it again — against Turso each read is its own connection, and the insert
    and delete endpoints were reading the same table two and three times on one request.
    """
    from . import db
    course = course or _course()
    out = {}
    for r in (db.curriculum(course) if rows is None else rows):
        out[str(r["session_no"])] = {
            "number": r["session_no"],
            "name": r["session_name"],
            "topic": r["topic"],
            "key_takeaways": r["key_takeaways"],
        }
    KB.mkdir(parents=True, exist_ok=True)
    COURSE_CACHE.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    return len(out)


def clear_course_cache() -> None:
    """Empty the on-disk projection.

    Needed when the LAST course is deleted. This file is keyed by session number alone
    and records no course name, so nothing downstream can tell whose projection it is —
    and the offline session loader falls back to it whenever the database holds no
    curriculum at all. Left behind, the deleted course's 34 sessions went on being
    offered in the generate dropdown of an instance that no longer had a course.
    """
    KB.mkdir(parents=True, exist_ok=True)
    COURSE_CACHE.write_text("{}", encoding="utf-8")


# --------------------------------------------------------------------------- #
# 3. INGEST — fetch ONLY the decks that are new, changed, or explicitly forced.
# --------------------------------------------------------------------------- #
def ingest_decks(course: str | None = None, *, force: bool = False,
                 only_sessions: list[int] | None = None,
                 verbose: bool = True, on_event=None) -> SyncResult:
    """Extract the decks this course still needs. Nothing else is touched.

    A row is fetched when its link is set AND (it has never been extracted, or its
    extracted text is missing from the knowledge base, or `force`). A row already
    extracted from the same link costs NO request — that is the whole point.
    """
    from . import db
    course = course or _course()
    res = SyncResult(ok=True)

    def emit(msg: str):
        if verbose:
            print(f"[SYNC] {msg}", flush=True)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    rows = db.curriculum(course)
    res.sessions = len(rows)
    state = _load_state()
    decks = state.get("decks", {})

    tasks, skipped = [], 0
    for r in rows:
        link, no = (r.get("ppt_link") or "").strip(), r["session_no"]
        if only_sessions and no not in only_sessions:
            continue
        if not link:
            continue
        have_file = pptx_ingest.has_deck(course, no)
        if not force and r.get("deck_hash") and have_file:
            skipped += 1
            res.decks_cached += 1
            continue
        tasks.append((no, r.get("session_name") or "", link))

    if skipped:
        emit(f"{skipped} deck(s) already extracted — skipped without downloading "
             f"anything (that is ~{skipped * 3.4:.0f}s of transfer avoided).")
    if not tasks:
        emit("No new or changed decks to fetch.")
        state["decks"], state["last_sync"] = decks, _now()
        _save_state(state)
        _extraction_report(res, course)
        return res

    emit(f"Fetching {len(tasks)} deck(s) that are new or changed…")

    def _fetch(task):
        no, name, link = task
        try:
            chash, data = gslides.content_hash(link)
        except Exception as e:
            return (task, None, "error", str(e))
        try:
            deck = gslides.extract_from_bytes(data, no, name, link)
        except Exception as e:
            return (task, chash, "error", f"extract failed: {e}")
        data = None                      # free the .pptx bytes before writing
        # Written through the store, which is what puts it in THIS course's folder and
        # keeps its manifest in step. Building the path here is how the course scoping
        # went missing in the first place.
        pptx_ingest.put_deck(course, no, deck)
        return (task, chash, "ingested", None)

    workers = config.harness()["context"].get("sync_max_workers", 1)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {ex.submit(_fetch, t): t for t in tasks}
        done_n = 0
        for fut in as_completed(futures):
            done_n += 1
            task, chash, status, err = fut.result()
            no, name, link = task
            if status == "error":
                emit(f"[{done_n}/{len(tasks)}] ⚠ unreadable — Session {no}: {name}")
                res.errors.append(f"Session {no} ('{name}'): {err}")
                db.curriculum_mark_deck(course, no, "", status="error")
                continue
            emit(f"[{done_n}/{len(tasks)}] ✓ extracted — Session {no}: {name}")
            res.decks_ingested += 1
            res.changelog.append(f"+ Extracted the deck for session {no}: {name}")
            db.curriculum_mark_deck(course, no, chash, status="extracted")
            decks[_norm_name(name) or str(no)] = {
                "link": link, "content_hash": chash,
                "session_no": no, "deck_key": f"session_{no:02d}"}

    state["decks"] = decks
    state["last_sync"] = _now()
    _save_state(state)
    _extraction_report(res, course)

    try:
        from . import db as _db
        saved = _db.kb_backup()
        if saved:
            emit(f"Saved {saved} knowledge-base file(s) to cloud storage.")
    except Exception as e:
        emit(f"(knowledge-base cloud backup skipped: {e})")
    return res


def _extraction_report(res: SyncResult, course: str) -> None:
    try:
        rep = pptx_ingest.completeness_report(course)
        for d in rep["decks"]:
            if not d["ok"]:
                res.extraction_warnings.append(
                    f"Session {d['session_no']} deck ({d['source_file']}): "
                    + "; ".join(d["issues"]))
    except Exception as e:
        res.extraction_warnings.append(f"extraction check skipped: {e}")


# --------------------------------------------------------------------------- #
# 4. The one-call path: import a sheet, then fetch whatever that added.
# --------------------------------------------------------------------------- #
def sync(course_link: str | None = None, *, course: str | None = None,
         verbose: bool = True, on_event=None) -> SyncResult:
    """Import the sheet (if a link is given) and ingest only what needs ingesting.

    Called with no link it is purely a top-up: read the curriculum the agent already
    holds and fetch any deck still missing. That is what a generation run wants — it
    must never re-read a sheet or re-download a deck to produce a document.
    """
    course = course or _course()
    res = SyncResult(ok=True)
    if course_link:
        imported = import_sheet(course_link, course, verbose=verbose, on_event=on_event)
        res.changelog.append(
            f"Imported from the sheet: {imported['added']} new session(s), "
            f"{imported['updated']} updated.")
    else:
        write_course_cache(course)
    got = ingest_decks(course, verbose=verbose, on_event=on_event)
    res.changelog += got.changelog
    res.errors += got.errors
    res.extraction_warnings += got.extraction_warnings
    res.sessions = got.sessions
    res.decks_ingested, res.decks_cached = got.decks_ingested, got.decks_cached
    if verbose:
        _print_report(res)
    return res


def _print_report(res: SyncResult):
    print(f"[SYNC] {res.sessions} sessions | decks: {res.decks_ingested} extracted, "
          f"{res.decks_cached} already held")
    for c in res.changelog:
        print(f"       {c}")
    for e in res.errors:
        print(f"[SYNC] ⚠ {e}")


def last_link() -> str | None:
    """The sheet this course was last imported from (for the re-import button)."""
    return _load_state().get("course_link")
