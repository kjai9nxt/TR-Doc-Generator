"""Parse the course-structure spreadsheet into structured Session objects.

The sheet has merged Module/Topic cells (they only appear on the first session
of a module/topic), so we forward-fill them. 'Session' is a float like 15.0.
'Key Takeaways' is a multi-line string of "- ..." bullets.
"""
from __future__ import annotations
import glob
import re
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from . import config


@dataclass
class Session:
    number: int
    name: str
    module: str
    topic: str
    key_takeaways: list[str] = field(default_factory=list)
    # The PREVIOUS session's takeaways, filled in by neighbours(). The recap must
    # reproduce all of them, and guardrails check that — but guardrails only receive
    # the target session, so the count has to travel with it.
    prev_key_takeaways: list[str] = field(default_factory=list)

    @property
    def key_takeaways_count(self) -> int:
        return len(self.key_takeaways)

    @property
    def prev_key_takeaways_count(self) -> int:
        return len(self.prev_key_takeaways)


def _split_takeaways(raw: str | None) -> list[str]:
    if not raw:
        return []
    lines = re.split(r"[\n\r]+", str(raw))
    out = []
    for ln in lines:
        ln = ln.strip().lstrip("-•").strip()
        if ln:
            out.append(ln)
    return out


def _find_course_file() -> Path:
    pattern = config.harness()["context"]["course_structure_glob"]
    matches = sorted(glob.glob(str(config.ROOT / pattern)))
    if not matches:
        raise FileNotFoundError(f"No course structure file matches {pattern}")
    return Path(matches[0])


def _cache_path() -> Path:
    return config.DATA_ROOT / config.harness()["context"]["knowledge_base_dir"] / "course_structure.json"


def load_sessions_from_cache(course: str | None = None) -> list[Session] | None:
    """The curriculum for `course`, or None if the agent holds none yet.

    Two sources, in order of authority:
      1. the CURRICULUM TABLE — what the user last saved in the app. This is the source
         of truth: the sheet is only an import format, so an edit made in the dashboard
         must be what generation sees, immediately and without a re-sync.
      2. knowledge_base/course_structure.json — the table's projection on disk, and the
         fallback for an offline/eval process running without a database.

    `course` NAMES THE CURRICULUM TO READ, and callers that know it must pass it. The
    fallback — the last course selected in this process — is one global shared by every
    signed-in user, so on the deployed server one person opening their course silently
    repointed everyone else's generation at it. Only the on-disk cache path (offline
    evals, which have no database) still relies on that global.
    """
    try:
        from . import db, app_settings
        rows = db.curriculum((course or "").strip() or app_settings.course_name() or "default")
        if rows:
            sessions = [
                Session(number=r["session_no"], name=r.get("session_name", ""),
                        module=r.get("topic", ""), topic=r.get("topic", ""),
                        key_takeaways=r.get("key_takeaways", []))
                for r in rows
            ]
            sessions.sort(key=lambda s: s.number)
            return sessions
    except Exception:
        pass                      # no DB (offline eval) — fall through to the cache

    p = _cache_path()
    if not p.exists():
        return None
    import json
    data = json.loads(p.read_text())
    sessions = [
        Session(number=v["number"], name=v.get("name", ""),
                module=v.get("topic", ""), topic=v.get("topic", ""),
                key_takeaways=v.get("key_takeaways", []))
        for v in data.values()
    ]
    sessions.sort(key=lambda s: s.number)
    return sessions


def load_sessions(course_file: str | Path | None = None,
                  course: str | None = None) -> list[Session]:
    # Prefer the live-synced sheet cache unless an explicit file is requested.
    if course_file is None:
        cached = load_sessions_from_cache(course)
        if cached:
            return cached
    path = Path(course_file) if course_file else _find_course_file()
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))

    header = [(str(c).strip() if c is not None else "") for c in rows[0]]
    cols = config.harness()["context"]["structure_columns"]

    def idx(name: str) -> int:
        return header.index(name)

    def opt_idx(name: str) -> int:
        return header.index(name) if name in header else -1

    i_mod = opt_idx(cols["module"])        # optional: sheet template has no Modules
    i_top = idx(cols["topic"])
    i_no, i_name = idx(cols["session_no"]), idx(cols["session_name"])
    i_kt = idx(cols["key_takeaways"])

    sessions: list[Session] = []
    cur_mod = cur_top = ""
    for r in rows[1:]:
        if i_mod >= 0 and r[i_mod]:
            cur_mod = str(r[i_mod]).strip()
        if r[i_top]:
            cur_top = str(r[i_top]).strip()
        no_raw = r[i_no]
        if no_raw is None or str(no_raw).strip() == "":
            continue  # e.g. the final review row with no session number
        try:
            number = int(float(no_raw))
        except (ValueError, TypeError):
            continue
        name = str(r[i_name]).strip() if r[i_name] else ""
        sessions.append(Session(
            number=number,
            name=name,
            module=cur_mod,
            topic=cur_top,
            key_takeaways=_split_takeaways(r[i_kt]),
        ))
    sessions.sort(key=lambda s: s.number)
    return sessions


def get_session(number: int, sessions: list[Session] | None = None) -> Session:
    sessions = sessions or load_sessions()
    for s in sessions:
        if s.number == number:
            return s
    raise KeyError(f"Session {number} not found in course structure")


def neighbours(number: int, sessions: list[Session] | None = None):
    """Return (prev, current, next) — prev/next may be None at course edges."""
    sessions = sessions or load_sessions()
    by_no = {s.number: s for s in sessions}
    prev, cur, nxt = by_no.get(number - 1), by_no[number], by_no.get(number + 1)
    # Carry the previous session's takeaways on `cur` so downstream graders that only
    # get the target session can still check the recap covers all of them.
    cur.prev_key_takeaways = list(prev.key_takeaways) if prev else []
    return prev, cur, nxt
