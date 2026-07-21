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
import json
import os
import sqlite3
from datetime import datetime, timezone

from . import config

DB_PATH = config.KB_DIR / "tr_app.db"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _use_turso() -> bool:
    return bool((os.environ.get("TURSO_DATABASE_URL") or "").strip())


def _connect():
    """Open a fresh connection to whichever backend is configured."""
    if _use_turso():
        import libsql_experimental as libsql   # pip install libsql-experimental
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
         accepted INTEGER, rubric REAL, est_minutes REAL, enforce_time INTEGER,
         rounds INTEGER, slides INTEGER, cost REAL, total_tokens INTEGER,
         cost_json TEXT, calls_json TEXT, docx_path TEXT, error TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_runs_user   ON runs(user_email)",
    "CREATE INDEX IF NOT EXISTS idx_runs_course ON runs(course)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_ts     ON runs(ts)",
    # Persisted knowledge base: relative KB path -> file text. Lets the synced
    # course structure + extracted decks survive an ephemeral disk (Render free),
    # so the app never has to re-sync after a restart. See kb_backup/kb_restore.
    """CREATE TABLE IF NOT EXISTS kb_files (
         path TEXT PRIMARY KEY, content TEXT, updated_at TEXT)""",
]


def init() -> None:
    """Create tables (idempotent) and one-time import the old JSON run log."""
    conn = _connect()
    try:
        for stmt in _SCHEMA:
            conn.execute(stmt)
        conn.commit()
    finally:
        _close(conn)
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
               rubric=None, est_minutes=None, rounds=None, slides=None,
               cost: dict | None = None, calls: list | None = None,
               docx_path: str | None = None, error: str | None = None) -> None:
    cost = cost or {}
    _exec(
        """UPDATE runs SET status=?, stage=?, accepted=?, rubric=?, est_minutes=?,
             rounds=?, slides=?, cost=?, total_tokens=?, cost_json=?, calls_json=?,
             docx_path=?, error=?, updated=?
           WHERE id=?""",
        (status, "done" if status == "done" else status,
         None if accepted is None else (1 if accepted else 0),
         rubric, est_minutes, rounds, slides,
         cost.get("cost"), cost.get("total_tokens"),
         json.dumps(cost), json.dumps(calls or []),
         docx_path, error, _now(), run_id))


def _shape_run(d: dict) -> dict:
    d["accepted"] = None if d.get("accepted") is None else bool(d["accepted"])
    d["enforce_time"] = None if d.get("enforce_time") is None else bool(d["enforce_time"])
    d["cost"] = json.loads(d.pop("cost_json", None) or "{}")
    d["calls"] = json.loads(d.pop("calls_json", None) or "[]")
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
        "running": len([r for r in rs if r["status"] == "running"]),
        "errors": len([r for r in rs if r["status"] == "error"]),
        "approved": len(approved),
        "acceptance_rate": round(100 * len(approved) / len(done), 1) if done else 0,
        "avg_rubric": round(sum((r["rubric"] or 0) for r in done) / len(done), 1) if done else 0,
        "total_cost": round(sum((r["cost"] or {}).get("cost", 0) or 0 for r in rs), 6),
        "total_tokens": sum((r["cost"] or {}).get("total_tokens", 0) or 0 for r in rs),
        "models": sorted(by_model.values(), key=lambda x: -x["cost"]),
    }


def per_user() -> list[dict]:
    out: dict = {}
    for r in runs(limit=100000):
        who = r["user_email"] or "unknown"
        e = out.setdefault(who, {"user": who, "runs": 0, "approved": 0, "cost": 0.0,
                                 "tokens": 0, "courses": set(), "last": r["ts"]})
        e["runs"] += 1
        if r["accepted"]:
            e["approved"] += 1
        e["cost"] += (r["cost"] or {}).get("cost", 0) or 0
        e["tokens"] += (r["cost"] or {}).get("total_tokens", 0) or 0
        if r["course"]:
            e["courses"].add(r["course"])
    res = []
    for e in out.values():
        e["courses"] = sorted(e["courses"])
        e["cost"] = round(e["cost"], 6)
        res.append(e)
    return sorted(res, key=lambda x: -x["cost"])


# --------------------------------------------------------------------------- #
# knowledge-base persistence (so a synced KB survives an ephemeral disk)
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
