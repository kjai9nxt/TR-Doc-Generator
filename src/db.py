"""Data layer for multi-user tracking: users, teams, courses (as a grouping
label), and generation runs with live status.

Two backends, chosen at runtime:
  - **stdlib sqlite3** (default) — a local file at knowledge_base/tr_app.db. Great
    for local dev and for hosts with a persistent disk.
  - **Turso / libSQL** — a free managed cloud DB, used when TURSO_DATABASE_URL is
    set. This lets the app run on a FREE host with an ephemeral filesystem while
    the data still persists across redeploys. Needs the `libsql-experimental`
    package + env vars TURSO_DATABASE_URL and TURSO_AUTH_TOKEN.

All access goes through _exec/_query, which return plain dicts, so the rest of the
module is identical for both backends. Connections are short-lived (opened per
call) so it is safe to use from the server's background generation threads.

Notes:
- "course" is a light grouping label attached to every run + team (one active
  KB/sheet-set at a time; see app_settings.course_name).
- Runs carry a live lifecycle: status (running|done|error) + a human-readable
  stage ("generating draft 2", "grading", ...), so an admin can watch progress.
"""
from __future__ import annotations
import base64
import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config

DB_PATH = config.KB_DIR / "tr_app.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_TURSO_UNAVAILABLE_WARNED = False


def _turso_driver():
    """The libSQL driver, or None if it isn't installed. A cloud URL is only usable
    with the driver present; without it we must fall back to the local file rather
    than raise on every call. That mattered: `.env` here carries the deploy's
    TURSO_DATABASE_URL, so on a machine without `libsql-experimental` every write
    raised inside a best-effort try/except and vanished silently."""
    global _TURSO_UNAVAILABLE_WARNED
    try:
        import libsql_experimental as libsql   # pip install libsql-experimental
        return libsql
    except Exception:
        if not _TURSO_UNAVAILABLE_WARNED:
            _TURSO_UNAVAILABLE_WARNED = True
            print("[db] TURSO_DATABASE_URL is set but the libsql-experimental driver "
                  "is not installed — falling back to the local SQLite file at "
                  f"{DB_PATH}. Install it (pip install -r requirements.txt) to use "
                  "the cloud DB.")
        return None


def _use_turso() -> bool:
    if not (os.environ.get("TURSO_DATABASE_URL") or "").strip():
        return False
    return _turso_driver() is not None


def _connect():
    """Open a fresh connection to whichever backend is configured."""
    if _use_turso():
        libsql = _turso_driver()
        return libsql.connect(
            database=os.environ["TURSO_DATABASE_URL"],
            auth_token=os.environ.get("TURSO_AUTH_TOKEN"))
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:
        pass


def _exec(sql: str, args: tuple = ()):
    """Run a write statement; return lastrowid (for INSERTs)."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        conn.commit()
        return cur.lastrowid
    finally:
        _close(conn)


def _query(sql: str, args: tuple = ()) -> list[dict]:
    """Run a read query; return rows as plain dicts (driver-agnostic)."""
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, args)
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description] if cur.description else []
        return [dict(zip(cols, r)) for r in rows]
    finally:
        _close(conn)


_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS users (
         email TEXT PRIMARY KEY, name TEXT, is_admin INTEGER DEFAULT 0,
         first_seen TEXT, last_seen TEXT)""",
    """CREATE TABLE IF NOT EXISTS teams (
         id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, course TEXT,
         created_by TEXT, created_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS team_members (
         team_id INTEGER, user_email TEXT, PRIMARY KEY (team_id, user_email))""",
    """CREATE TABLE IF NOT EXISTS runs (
         id TEXT PRIMARY KEY, ts TEXT, updated TEXT, user_email TEXT, course TEXT,
         team_id INTEGER, session_no INTEGER, title TEXT, status TEXT, stage TEXT,
         accepted INTEGER, rubric REAL, est_minutes REAL, est_pages REAL,
         enforce_time INTEGER,
         rounds INTEGER, slides INTEGER, cost REAL, total_tokens INTEGER,
         cost_json TEXT, calls_json TEXT, docx_path TEXT, docx_name TEXT,
         error TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_runs_user   ON runs(user_email)",
    "CREATE INDEX IF NOT EXISTS idx_runs_course ON runs(course)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_ts     ON runs(ts)",
    # Persisted knowledge base: relative KB path -> file text. Lets the synced
    # course structure + extracted decks survive an ephemeral disk (Render free),
    # so the app never has to re-sync after a restart. See kb_backup/kb_restore.
    """CREATE TABLE IF NOT EXISTS kb_files (
         path TEXT PRIMARY KEY, content TEXT, updated_at TEXT)""",
    # In-flight GUIDED runs. A guided run spans a long human review (approve /
    # regenerate each chunk), during which the app makes NO requests — long enough
    # for a free host to spin the instance down, or for a redeploy/restart to land.
    # The state used to live only in the server process, so any restart orphaned the
    # run ("Unknown guided session") and threw away every generated chunk. It is now
    # checkpointed here after every mutation and rehydrated on demand.
    """CREATE TABLE IF NOT EXISTS guided_sessions (
         id TEXT PRIMARY KEY, ts TEXT, updated TEXT, user_email TEXT,
         session_no INTEGER, state_json TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_guided_updated ON guided_sessions(updated)",
    # RENDERED OUTPUTS, keyed by run. The .docx lived only on the instance disk, so on
    # an ephemeral host it vanished when the instance spun down — and a guided run spans
    # a long human review, which is exactly that window. A reviewer who had finished
    # generating and reviewing then found Download and Create-Google-Doc both dead and
    # had to copy the document out by hand. ~40 KB per doc, base64 in `content_b64`.
    """CREATE TABLE IF NOT EXISTS run_files (
         run_id TEXT, kind TEXT, filename TEXT, content_b64 TEXT, updated_at TEXT,
         PRIMARY KEY (run_id, kind))""",
    "CREATE INDEX IF NOT EXISTS idx_run_files_name ON run_files(filename)",
]


# Columns added to `runs` after the table first shipped. CREATE TABLE IF NOT EXISTS is
# a no-op on an existing database, so a new column in _SCHEMA would appear only on a
# fresh install — every deployed instance would keep the old table and every write
# naming the column would fail. Each entry is applied with ALTER TABLE ADD COLUMN,
# which is cheap and, unlike a rebuild, cannot lose rows.
_RUNS_ADDED_COLUMNS = [
    ("est_pages", "REAL"),      # 1.29: the 16-page ceiling, shown in the dashboards
    ("docx_name", "TEXT"),      # 1.30: exact output filename, so downloads never re-derive it
]


def _add_missing_columns(conn) -> list[str]:
    """Bring an existing `runs` table up to date. Idempotent."""
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(runs)")
        have = {row[1] for row in cur.fetchall()}
    except Exception:
        return []
    added = []
    for name, decl in _RUNS_ADDED_COLUMNS:
        if name in have:
            continue
        try:
            conn.execute(f"ALTER TABLE runs ADD COLUMN {name} {decl}")
            added.append(name)
        except Exception:
            pass                 # a racing instance added it first — harmless
    return added


def init() -> None:
    """Create tables (idempotent), add any columns a pre-existing DB is missing, and
    one-time import the old JSON run log."""
    conn = _connect()
    try:
        for stmt in _SCHEMA:
            conn.execute(stmt)
        added = _add_missing_columns(conn)
        conn.commit()
    finally:
        _close(conn)
    if added:
        print(f"[db] migrated runs table: added column(s) {', '.join(added)}")
    _migrate_json_log()


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #
def upsert_user(email: str, name: str | None = None, is_admin: bool = False) -> None:
    if not email:
        return
    now = _now()
    _exec(
        """INSERT INTO users (email, name, is_admin, first_seen, last_seen)
             VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(email) DO UPDATE SET
             name=COALESCE(excluded.name, users.name),
             is_admin=excluded.is_admin,
             last_seen=excluded.last_seen""",
        (email, name, 1 if is_admin else 0, now, now))


def users() -> list[dict]:
    return _query("SELECT * FROM users ORDER BY last_seen DESC")


# --------------------------------------------------------------------------- #
# teams (admin-managed)
# --------------------------------------------------------------------------- #
def create_team(name: str, course: str | None, created_by: str) -> int:
    return _exec("INSERT INTO teams (name, course, created_by, created_at) VALUES (?,?,?,?)",
                 (name, course, created_by, _now()))


def set_team_course(team_id: int, course: str) -> None:
    _exec("UPDATE teams SET course=? WHERE id=?", (course, team_id))


def add_member(team_id: int, user_email: str) -> None:
    _exec("INSERT OR IGNORE INTO team_members (team_id, user_email) VALUES (?,?)",
          (team_id, user_email))


def remove_member(team_id: int, user_email: str) -> None:
    _exec("DELETE FROM team_members WHERE team_id=? AND user_email=?", (team_id, user_email))


def delete_team(team_id: int) -> None:
    _exec("DELETE FROM team_members WHERE team_id=?", (team_id,))
    _exec("DELETE FROM teams WHERE id=?", (team_id,))


def teams() -> list[dict]:
    rows = _query("SELECT * FROM teams ORDER BY created_at DESC")
    for t in rows:
        t["members"] = [r["user_email"] for r in _query(
            "SELECT user_email FROM team_members WHERE team_id=?", (t["id"],))]
    return rows


def teams_for_user(email: str) -> list[dict]:
    ids = {r["team_id"] for r in _query(
        "SELECT team_id FROM team_members WHERE user_email=?", (email,))}
    return [t for t in teams() if t["id"] in ids]


def team_for_user_course(email: str, course: str | None):
    """The team this user belongs to for a given course (first match), or None."""
    for t in teams_for_user(email):
        if (t.get("course") or None) == (course or None):
            return t["id"]
    return None


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #
def create_run(run_id: str, *, user_email: str | None, course: str | None,
               team_id: int | None, session_no: int, title: str,
               enforce_time: bool) -> None:
    now = _now()
    _exec(
        """INSERT OR REPLACE INTO runs
           (id, ts, updated, user_email, course, team_id, session_no, title,
            status, stage, enforce_time)
           VALUES (?,?,?,?,?,?,?,?, 'running', 'queued', ?)""",
        (run_id, now, now, user_email, course, team_id, session_no, title,
         1 if enforce_time else 0))


def update_stage(run_id: str, stage: str) -> None:
    _exec("UPDATE runs SET stage=?, updated=? WHERE id=?", (stage, _now(), run_id))


def finish_run(run_id: str, *, status: str, accepted: bool | None = None,
               rubric=None, est_minutes=None, est_pages=None, rounds=None, slides=None,
               cost: dict | None = None, calls: list | None = None,
               docx_path: str | None = None, error: str | None = None) -> None:
    cost = cost or {}
    # docx_name is stored ALONGSIDE docx_path because the path is only valid on the
    # instance that wrote it, while the name is what identifies the output anywhere —
    # it is what a download must be resolved by, instead of re-deriving a filename from
    # whatever course happens to be synced at the time (see src/outputs.py).
    docx_name = Path(docx_path).name if docx_path else None
    _exec(
        """UPDATE runs SET status=?, stage=?, accepted=?, rubric=?, est_minutes=?,
             est_pages=?, rounds=?, slides=?, cost=?, total_tokens=?, cost_json=?,
             calls_json=?, docx_path=?, docx_name=?, error=?, updated=?
           WHERE id=?""",
        (status, "done" if status == "done" else status,
         None if accepted is None else (1 if accepted else 0),
         rubric, est_minutes, est_pages, rounds, slides,
         cost.get("cost"), cost.get("total_tokens"),
         json.dumps(cost), json.dumps(calls or []),
         docx_path, docx_name, error, _now(), run_id))


# A run still marked "running" this long after its last update almost certainly
# died or was abandoned mid-way (a real generation finishes in a few minutes), so
# we surface it separately from genuinely in-progress runs.
ABANDONED_AFTER_MIN = 20


def _duration_min(r: dict):
    """Wall-clock minutes from run start (ts) to last update (updated). For a
    finished run that is its total generation time; None if unparseable."""
    try:
        d = (datetime.fromisoformat(r["updated"]) - datetime.fromisoformat(r["ts"])).total_seconds() / 60
        return round(d, 2) if d >= 0 else None
    except Exception:
        return None


def _is_abandoned(r: dict) -> bool:
    if r.get("status") != "running":
        return False
    try:
        last = datetime.fromisoformat(r.get("updated") or r.get("ts"))
        return (datetime.now(timezone.utc) - last).total_seconds() > ABANDONED_AFTER_MIN * 60
    except Exception:
        return False


def _shape_run(d: dict) -> dict:
    d["accepted"] = None if d.get("accepted") is None else bool(d["accepted"])
    d["enforce_time"] = None if d.get("enforce_time") is None else bool(d["enforce_time"])
    d["cost"] = json.loads(d.pop("cost_json", None) or "{}")
    d["calls"] = json.loads(d.pop("calls_json", None) or "[]")
    d["duration_min"] = _duration_min(d)
    d["abandoned"] = _is_abandoned(d)
    # A single, UI-friendly outcome: completed | approved | failed | abandoned | running
    if d["status"] == "done":
        d["outcome"] = "approved" if d["accepted"] else "completed"
    elif d["status"] == "error":
        d["outcome"] = "failed"
    else:
        d["outcome"] = "abandoned" if d["abandoned"] else "running"
    return d


def runs(*, user_email: str | None = None, course: str | None = None,
         team_id: int | None = None, status: str | None = None,
         limit: int = 1000) -> list[dict]:
    q = "SELECT * FROM runs WHERE 1=1"
    args: list = []
    if user_email is not None:
        q += " AND user_email=?"; args.append(user_email)
    if course is not None:
        q += " AND course=?"; args.append(course)
    if team_id is not None:
        q += " AND team_id=?"; args.append(team_id)
    if status is not None:
        q += " AND status=?"; args.append(status)
    q += " ORDER BY ts DESC LIMIT ?"; args.append(limit)
    return [_shape_run(r) for r in _query(q, tuple(args))]


def live_runs() -> list[dict]:
    """In-progress generations (for the admin live view)."""
    return runs(status="running")


# --------------------------------------------------------------------------- #
# analytics (admin)
# --------------------------------------------------------------------------- #
def _bucket(ts: str, unit: str) -> str:
    day = (ts or "")[:10]
    if unit == "day" or len(day) < 10:
        return day
    if unit == "month":
        return day[:7]
    if unit == "week":
        try:
            iso = datetime.fromisoformat(day).isocalendar()
            return f"{iso[0]}-W{iso[1]:02d}"
        except Exception:
            return day
    return day


def timeseries(unit: str = "day") -> list[dict]:
    out: dict = {}
    for r in runs(limit=100000):
        b = _bucket(r["ts"], unit)
        e = out.setdefault(b, {"bucket": b, "runs": 0, "approved": 0, "cost": 0.0, "tokens": 0})
        e["runs"] += 1
        if r["accepted"]:
            e["approved"] += 1
        e["cost"] += (r["cost"] or {}).get("cost", 0) or 0
        e["tokens"] += (r["cost"] or {}).get("total_tokens", 0) or 0
    return [{**v, "cost": round(v["cost"], 6)} for v in sorted(out.values(), key=lambda x: x["bucket"])]


def summary() -> dict:
    rs = runs(limit=100000)
    done = [r for r in rs if r["status"] == "done"]
    approved = [r for r in done if r["accepted"]]
    abandoned = [r for r in rs if r["abandoned"]]
    in_progress = [r for r in rs if r["status"] == "running" and not r["abandoned"]]
    durations = [r["duration_min"] for r in done if r["duration_min"] is not None]
    # est_pages is None for runs generated before the page ceiling existed (1.29), so
    # the length stats are computed over the runs that actually have it rather than
    # counting a missing value as zero and dragging the average down.
    pages = [r["est_pages"] for r in done if r.get("est_pages")]
    by_model: dict = {}
    for r in rs:
        for call in r["calls"]:
            m = call.get("model", "?")
            e = by_model.setdefault(m, {"model": m, "calls": 0, "cost": 0.0, "tokens": 0})
            e["calls"] += 1
            e["cost"] += call.get("cost", 0) or 0
            e["tokens"] += call.get("total_tokens", 0) or 0
    return {
        "total_runs": len(rs),
        "done": len(done),
        "running": len(in_progress),                 # genuinely in progress
        "in_progress": len(in_progress),
        "abandoned": len(abandoned),                 # started, never completed
        "errors": len([r for r in rs if r["status"] == "error"]),
        "approved": len(approved),
        "completion_rate": round(100 * len(done) / len(rs), 1) if rs else 0,
        "acceptance_rate": round(100 * len(approved) / len(done), 1) if done else 0,
        "avg_rubric": round(sum((r["rubric"] or 0) for r in done) / len(done), 1) if done else 0,
        "avg_duration_min": round(sum(durations) / len(durations), 1) if durations else 0,
        # Document length. `over_page_limit` is the number that matters: the reviewer's
        # original complaint was length, so an admin needs to see at a glance whether
        # docs are still coming out over the ceiling rather than infer it per run.
        "avg_pages": round(sum(pages) / len(pages), 1) if pages else 0,
        "max_pages_seen": max(pages) if pages else 0,
        "page_limit": _page_limit(),
        "over_page_limit": len([p for p in pages if p > _page_limit()]),
        "docs_with_pages": len(pages),
        "total_cost": round(sum((r["cost"] or {}).get("cost", 0) or 0 for r in rs), 6),
        "total_tokens": sum((r["cost"] or {}).get("total_tokens", 0) or 0 for r in rs),
        "models": sorted(by_model.values(), key=lambda x: -x["cost"]),
    }


def _page_limit() -> int:
    """The harness page ceiling, so the dashboard flags over-length docs against the
    same number the gate enforces rather than a copy of it."""
    try:
        return int(config.harness()["constraints"]["pages"]["max"])
    except Exception:
        return 16


def per_user() -> list[dict]:
    out: dict = {}
    for r in runs(limit=100000):
        who = r["user_email"] or "unknown"
        e = out.setdefault(who, {"user": who, "runs": 0, "completed": 0, "approved": 0,
                                 "abandoned": 0, "failed": 0, "cost": 0.0, "tokens": 0,
                                 "courses": set(), "_durations": [], "last": r["ts"]})
        e["runs"] += 1
        if r["status"] == "done":
            e["completed"] += 1
            if r["duration_min"] is not None:
                e["_durations"].append(r["duration_min"])
        if r["accepted"]:
            e["approved"] += 1
        if r["abandoned"]:
            e["abandoned"] += 1        # left mid-way, never completed
        if r["status"] == "error":
            e["failed"] += 1
        e["cost"] += (r["cost"] or {}).get("cost", 0) or 0
        e["tokens"] += (r["cost"] or {}).get("total_tokens", 0) or 0
        if r["course"]:
            e["courses"].add(r["course"])
    res = []
    for e in out.values():
        ds = e.pop("_durations")
        e["avg_duration_min"] = round(sum(ds) / len(ds), 1) if ds else 0
        e["courses"] = sorted(e["courses"])
        e["cost"] = round(e["cost"], 6)
        res.append(e)
    return sorted(res, key=lambda x: -x["cost"])


# --------------------------------------------------------------------------- #
# knowledge-base persistence (so a synced KB survives an ephemeral disk)
# --------------------------------------------------------------------------- #
# in-flight guided sessions (checkpoint / rehydrate across a server restart)
# --------------------------------------------------------------------------- #
def save_guided(gid: str, state: dict, *, user_email: str | None = None,
                session_no: int | None = None) -> bool:
    """Checkpoint one guided run's JSON-safe state. Best effort: a storage hiccup
    must never break the run in progress, so this returns False instead of raising."""
    try:
        _exec("""INSERT INTO guided_sessions (id, ts, updated, user_email, session_no, state_json)
                 VALUES (?,?,?,?,?,?)
                 ON CONFLICT(id) DO UPDATE SET
                   updated=excluded.updated, state_json=excluded.state_json""",
              (gid, _now(), _now(), user_email, session_no,
               json.dumps(state, ensure_ascii=False)))
        return True
    except Exception:
        return False


def load_guided(gid: str) -> dict | None:
    """The saved state for one guided run, or None if it was never saved//purged."""
    try:
        rows = _query("SELECT state_json FROM guided_sessions WHERE id=?", (gid,))
    except Exception:
        return None
    if not rows:
        return None
    try:
        return json.loads(rows[0].get("state_json") or "null")
    except Exception:
        return None


def delete_guided(gid: str) -> None:
    try:
        _exec("DELETE FROM guided_sessions WHERE id=?", (gid,))
    except Exception:
        pass


def purge_guided(older_than_hours: int = 72) -> int:
    """Drop checkpoints nobody can resume any more. Called at startup so the table
    can't grow without bound. Returns the number of rows removed (0 on error)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=older_than_hours)) \
        .isoformat(timespec="seconds")
    try:
        rows = _query("SELECT id FROM guided_sessions WHERE updated < ?", (cutoff,))
        for r in rows:
            _exec("DELETE FROM guided_sessions WHERE id=?", (r["id"],))
        return len(rows)
    except Exception:
        return 0


# --------------------------------------------------------------------------- #
# The small TEXT files a sync produces. Everything here is text-only (no images),
# so it fits comfortably in the DB. The big .pptx bytes are NEVER stored — sync
# already discards them after extracting text.
_KB_TOP_FILES = ("course_structure.json", "sync_state.json", "manifest.json",
                 "app_settings.json", "learned_rules.json", "regen_events.json")


def _kb_local_files() -> list[str]:
    """KB-relative paths (posix) that currently exist on disk and are worth
    persisting: the allow-listed top-level JSON files + every extracted deck."""
    kb = config.KB_DIR
    out = [name for name in _KB_TOP_FILES if (kb / name).exists()]
    decks = kb / "decks"
    if decks.is_dir():
        out += [f"decks/{f.name}" for f in sorted(decks.glob("*.json"))]
    return out


def kb_put(name: str) -> bool:
    """Persist ONE allow-listed KB file to the DB, immediately.

    kb_backup() only runs at the end of a sync, which is fine for the synced
    artefacts (they only change during a sync) but NOT for the files the app writes
    while it is being used: learned_rules.json and regen_events.json. On an
    ephemeral host those were written to a disk that is wiped when the free instance
    spins down or redeploys, so a rule learned from the reviewer's feedback survived
    only if a Connect & Sync happened to run before the instance died — otherwise the
    self-evolution loop silently reset and the next doc repeated the same mistake.
    Called from learning._save() / regen_log.record(); best effort, never raises.
    """
    if not _use_turso():
        return False                      # persistent disk: the file already survives
    if name not in _KB_TOP_FILES:
        return False
    try:
        content = (config.KB_DIR / name).read_text(encoding="utf-8")
    except Exception:
        return False
    try:
        _exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
              (name, content, _now()))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Rendered outputs (.docx / .md), stored per run.
#
# WHY: the rendered files lived ONLY on the instance disk. On an ephemeral host that
# disk is wiped whenever the instance spins down or redeploys, and a guided run spans a
# long human review — precisely that window. A reviewer finished generating and
# reviewing a document and then found BOTH "Download Word" and "Create Google Doc"
# dead, and had to copy the whole thing out of the preview by hand.
#
# Unlike kb_put, these are stored on EVERY backend, not only Turso. The name-mismatch
# bug (src/outputs.py) could hide a file that was present, and the disk could lose one
# that was not — so the store is the single answer to "where is this run's document",
# and it is cheap: a TR doc .docx is ~40 KB.
# --------------------------------------------------------------------------- #
def run_file_put(run_id: str, path, kind: str = "docx") -> bool:
    """Persist one rendered output for `run_id`. Best effort — never raises, because a
    storage hiccup must not fail a generation that has already succeeded."""
    if not run_id:
        return False
    try:
        p = Path(path)
        blob = base64.b64encode(p.read_bytes()).decode("ascii")
    except Exception:
        return False
    try:
        _exec("""INSERT OR REPLACE INTO run_files
                   (run_id, kind, filename, content_b64, updated_at) VALUES (?,?,?,?,?)""",
              (run_id, kind, p.name, blob, _now()))
        return True
    except Exception:
        return False


def run_file_get(run_id: str, kind: str = "docx") -> tuple[str, bytes] | None:
    """(filename, bytes) for a stored output, or None."""
    try:
        rows = _query("SELECT filename, content_b64 FROM run_files WHERE run_id=? AND kind=?",
                      (run_id, kind))
    except Exception:
        return None
    if not rows:
        return None
    try:
        return rows[0]["filename"], base64.b64decode(rows[0]["content_b64"])
    except Exception:
        return None


def run_file_find(filename: str, kind: str = "docx") -> tuple[str, bytes] | None:
    """Look a stored output up by FILENAME, for a download whose run id is unknown
    (an older run logged before run ids were threaded through, or a direct link)."""
    if not filename:
        return None
    try:
        rows = _query("""SELECT filename, content_b64 FROM run_files
                          WHERE filename=? AND kind=? ORDER BY updated_at DESC LIMIT 1""",
                      (filename, kind))
    except Exception:
        return None
    if not rows:
        return None
    try:
        return rows[0]["filename"], base64.b64decode(rows[0]["content_b64"])
    except Exception:
        return None


def run_file_find_by_session(session_no: int, kind: str = "docx") -> tuple[str, bytes] | None:
    """Newest stored output for a session number — the last-resort lookup, matching the
    filename convention "Session {n} _ ...". Used when neither a run id nor an exact
    filename is available and the disk copy is gone."""
    try:
        rows = _query("""SELECT r.docx_name AS filename, f.content_b64 AS content_b64
                           FROM run_files f JOIN runs r ON r.id = f.run_id
                          WHERE r.session_no=? AND f.kind=?
                          ORDER BY f.updated_at DESC LIMIT 1""", (session_no, kind))
    except Exception:
        return None
    if not rows:
        return None
    try:
        return (rows[0]["filename"] or f"Session {session_no}.docx",
                base64.b64decode(rows[0]["content_b64"]))
    except Exception:
        return None


def kb_backup() -> int:
    """Snapshot the current KB text files into the DB. No-op unless a cloud DB
    (Turso) is in use — on a persistent disk the files already survive. Best
    effort: never raises, so a storage hiccup can't fail a sync."""
    if not _use_turso():
        return 0
    kb = config.KB_DIR
    paths = _kb_local_files()
    if not paths:
        return 0
    ts = _now()
    n = 0
    for rel in paths:
        try:
            content = (kb / rel).read_text(encoding="utf-8")
        except Exception:
            continue
        try:
            _exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
                  (rel, content, ts))
            n += 1
        except Exception:
            continue
    # Drop rows whose file is gone locally (e.g. a deck removed from the sheet).
    try:
        ph = ",".join("?" * len(paths))
        _exec(f"DELETE FROM kb_files WHERE path NOT IN ({ph})", tuple(paths))
    except Exception:
        pass
    return n


def kb_restore() -> int:
    """Write any KB files stored in the DB back to disk (only when missing, so a
    fresh local sync is never clobbered). No-op unless a cloud DB is in use.
    Called once at startup so an ephemeral host recovers its synced KB."""
    if not _use_turso():
        return 0
    kb = config.KB_DIR
    try:
        rows = _query("SELECT path, content FROM kb_files")
    except Exception:
        return 0
    n = 0
    for r in rows:
        rel = (r.get("path") or "").lstrip("/")
        if not rel:
            continue
        dest = kb / rel
        if dest.exists():
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(r.get("content") or "", encoding="utf-8")
            n += 1
        except Exception:
            continue
    return n


# --------------------------------------------------------------------------- #
# one-time migration of the legacy JSON run log
# --------------------------------------------------------------------------- #
def _migrate_json_log() -> None:
    log = config.OUTPUTS_DIR / "generation_log.json"
    if not log.exists():
        return
    if _query("SELECT COUNT(*) AS n FROM runs")[0]["n"]:
        return  # already have runs; don't re-import
    try:
        data = json.loads(log.read_text(encoding="utf-8"))
    except Exception:
        return
    for i, r in enumerate(data.get("runs", [])):
        rid = f"legacy-{i}-{r.get('session_no')}"
        _exec(
            """INSERT OR IGNORE INTO runs
               (id, ts, updated, user_email, course, team_id, session_no, title,
                status, stage, accepted, rubric, est_minutes, enforce_time, rounds,
                slides, cost, total_tokens, cost_json, calls_json, docx_path)
               VALUES (?,?,?,?,?,?,?,?, 'done','done', ?,?,?,?,?,?,?,?,?,?,?)""",
            (rid, r.get("ts"), r.get("ts"), r.get("user"), r.get("course"), None,
             r.get("session_no"), r.get("title"),
             1 if r.get("accepted") else 0, r.get("rubric"), r.get("est_minutes"),
             1 if r.get("enforce_time") else 0, r.get("rounds"), r.get("slides"),
             (r.get("cost") or {}).get("cost"), (r.get("cost") or {}).get("total_tokens"),
             json.dumps(r.get("cost") or {}), json.dumps(r.get("calls") or []),
             r.get("docx")))
