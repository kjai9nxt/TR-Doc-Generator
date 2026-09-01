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


# How far out of the way a row is parked while a renumber is in flight. Any value
# larger than the longest imaginable course works; the point is only that the parked
# range provably contains no real session number, so the move cannot collide with a
# row that has not been moved yet.
_PARK = 1_000_000


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
    # THE CURRICULUM ITSELF — the agent's own copy, and the source of truth once a
    # course has been imported. The Google Sheet used to be that source, which meant
    # re-pasting a link and re-reading the sheet on every visit just to learn what the
    # app already knew, with no way to correct a takeaway or attach a deck without
    # leaving the app. The sheet is now an IMPORT format: paste it once to populate
    # this table, then edit here.
    #
    # `ppt_link` carries each session's deck. `deck_hash` records the content hash of
    # the deck we last extracted FROM THAT LINK: it is what lets a sync skip a deck
    # entirely instead of re-downloading multiple megabytes to discover nothing
    # changed (Google's export endpoint offers no ETag, no Last-Modified and says
    # no-store, so there is no cheap remote way to ask).
    # A team owns COURSES, plural. `teams.course` held exactly one, so a team working
    # across two courses needed two teams with the same members, and "select the team,
    # then pick a course" was impossible to express. The single column stays as the
    # team's primary course (nothing that reads it had to change) and is mirrored into
    # this table, which is what ownership is actually resolved against.
    """CREATE TABLE IF NOT EXISTS team_courses (
         team_id INTEGER, course TEXT, added_at TEXT,
         PRIMARY KEY (team_id, course))""",
    "CREATE INDEX IF NOT EXISTS idx_team_courses_course ON team_courses(course)",
    # WHO CREATED A COURSE — the other half of ownership, and the reason a course is
    # not simply on everybody's shelf.
    #
    # Visibility used to be: on a team -> that team's courses; on NO team -> EVERY
    # course the agent holds, on the reasoning that scoping a person to nothing would
    # lock them out. The effect was the opposite of what a private workspace means:
    # anyone signing in for the first time opened the app and found every course anyone
    # in the org had ever imported, could switch to it, edit its curriculum and
    # generate against it. Nothing recorded who a course belonged to, so nothing could
    # narrow it.
    #
    # A course is created ONCE, by one person, and that fact never changes — so it is
    # written down here on the creating request and read back on every visibility
    # decision. `created_by` is the INDIVIDUAL owner; team ownership lives in
    # team_courses and is resolved alongside it (a course can be both: mine, and shared
    # with the team I made it in).
    """CREATE TABLE IF NOT EXISTS course_owners (
         course TEXT PRIMARY KEY, created_by TEXT, created_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_course_owners_by ON course_owners(created_by)",
    """CREATE TABLE IF NOT EXISTS curriculum (
         course TEXT, session_no INTEGER, topic TEXT, session_name TEXT,
         key_takeaways TEXT, ppt_link TEXT, deck_hash TEXT, deck_status TEXT,
         updated_at TEXT, PRIMARY KEY (course, session_no))""",
    # How long a doc may be, per COURSE. A semester course and an interview course are
    # not the same shape, and editing harness.yaml to say so would change it for
    # everyone. NULL means "use the harness default".
    """CREATE TABLE IF NOT EXISTS course_settings (
         course TEXT PRIMARY KEY, max_pages INTEGER, max_slides INTEGER,
         updated_at TEXT)""",
    # WHAT A GOOD DOC LOOKS LIKE, PER COURSE. Everything about that was one set of
    # numbers in harness.yaml applied to every course on the instance — and several of
    # them are plainly about Computer Networks: the market platforms a doc is compared
    # against, a slide-role vocabulary with nothing for a code walkthrough, one prose
    # density for a theory course and a code-along alike.
    #
    # Stored as JSON rather than columns because it is a sparse tree of overrides over
    # harness.yaml, and a column per knob would mean a migration every time the harness
    # grows one. What may be overridden is a CLOSED WHITELIST in src/profiles.py — a
    # profile that can set anything is a config-injection surface.
    """CREATE TABLE IF NOT EXISTS course_profiles (
         course TEXT PRIMARY KEY, profile_json TEXT, updated_at TEXT)""",
    # COURSE SKILLS — the instructions a course is WRITTEN UNDER, authored by a person
    # and approved before they take effect.
    #
    # Distinct from learned_rules.json, which holds rules INFERRED from corrections after
    # a document was reviewed. A skill is written up front and says what this course
    # needs that others do not ("explain each snippet line by line"). Both reach the
    # writer down the same channel, labelled differently, because they carry different
    # authority.
    #
    # RETIRED, NEVER DELETED. A finished document was written under a particular set of
    # skills, and deleting one leaves no way to explain why an old doc looks as it does.
    """CREATE TABLE IF NOT EXISTS course_skills (
         id INTEGER PRIMARY KEY AUTOINCREMENT,
         course TEXT NOT NULL, text TEXT NOT NULL,
         kind TEXT, source TEXT, source_quote TEXT, status TEXT,
         check_json TEXT, version INTEGER DEFAULT 1,
         created_by TEXT, created_at TEXT,
         approved_by TEXT, approved_at TEXT, updated_at TEXT)""",
    "CREATE INDEX IF NOT EXISTS idx_course_skills_course ON course_skills(course)",
    # PREREQUISITE COURSES — what the learner already knows before session 1.
    #
    # "Already taught" used to mean earlier sessions of THIS course, so a React course
    # whose learners have done a JavaScript course had no way to say so and the writer
    # guessed whether to define `const`. The page budget is fixed, so every re-taught
    # concept costs a page from something new.
    #
    # A prerequisite is a COURSE THIS AGENT ALREADY HOLDS: its decks are here, so nothing
    # is uploaded twice and a course library compounds. `prereq` is a course name, the
    # same key everything else in this schema uses.
    """CREATE TABLE IF NOT EXISTS course_prereqs (
         course TEXT, prereq TEXT, kind TEXT, added_by TEXT, added_at TEXT,
         PRIMARY KEY (course, prereq))""",
    "CREATE INDEX IF NOT EXISTS idx_course_prereqs_prereq ON course_prereqs(prereq)",
    "CREATE INDEX IF NOT EXISTS idx_curriculum_course ON curriculum(course)",
]


# Columns added to `runs` after the table first shipped. CREATE TABLE IF NOT EXISTS is
# a no-op on an existing database, so a new column in _SCHEMA would appear only on a
# fresh install — every deployed instance would keep the old table and every write
# naming the column would fail. Each entry is applied with ALTER TABLE ADD COLUMN,
# which is cheap and, unlike a rebuild, cannot lose rows.
_RUNS_ADDED_COLUMNS = [
    ("est_pages", "REAL"),      # 1.29: the 16-page ceiling, shown in the dashboards
    ("docx_name", "TEXT"),      # 1.30: exact output filename, so downloads never re-derive it
    # 1.61: WHO signed off, and when. Distinct from `accepted`, which is the GRADERS'
    # verdict (every guardrail passed, inside time and pages, judge above bar). The
    # dashboard counted `accepted` under the label "Approved" and so showed 0 out of 17
    # documents that people had reviewed chunk by chunk and finalised — the human
    # approval was never recorded anywhere, because the per-chunk ticks lived only in
    # the reviewer's browser.
    ("approved_by", "TEXT"),
    ("approved_at", "TEXT"),
    # WHEN THE REVIEW ITSELF FINISHED — every chunk generated and ticked — which is a
    # different moment from `approved_at` (the reviewer pressing Create final TR Doc) and
    # from status='done' (the document assembled, graded and rendered). All three were
    # being collapsed into two, so the dashboard could not show the step where people
    # actually stop: review finished, final doc never created.
    ("review_done_at", "TEXT"),
    # WHICH SET OF COURSE SKILLS produced this document (db.skills_version). Without it
    # there is no way to explain why last month's doc differs from today's: the skills
    # changed and nothing recorded which set was in force.
    ("skills_version", "TEXT"),
]


# Same story for `curriculum`: per-SESSION budget overrides, for the odd session that
# needs more room (or much less) than the rest of its course. NULL = inherit.
_CURRICULUM_ADDED_COLUMNS = [
    ("max_pages", "INTEGER"),
    ("max_slides", "INTEGER"),
]


# THE TEAM'S COURSE OWNER — one named person, set by an admin when the team is created.
#
# Membership used to be admin-only in both directions: every add and every remove went
# through whoever holds the admin account. That does not scale past a handful of teams —
# it makes one person the bottleneck for a routine, low-stakes act, and the practical
# result is that people simply do not get added.
#
# So each team names an owner. They own the team's course (`course_owners`, set at the
# same moment) and they can add and remove that team's members, exactly as an admin can.
# Nothing else: they cannot rename the team, change its course, or delete it. Assigning
# and re-assigning the owner stays with the admin, which is what keeps this a delegation
# rather than a free-for-all.
_TEAMS_ADDED_COLUMNS = [
    ("owner_email", "TEXT"),
]


# A prerequisite is either a COURSE in this agent or one taught ELSEWHERE — a name and a
# set of decks. The two differ only in where the decks live, and everything downstream
# treats them identically, but the store has to know which.
_PREREQS_ADDED_COLUMNS = [
    ("kind", "TEXT"),
]

# A drafted skill can come from SEVERAL phrases at once. A person writing rough
# requirements says the same thing twice in different words — "code snippets should be
# small" and "small code snippets to be used" — and those are ONE requirement, not two
# rules to approve separately. `source_quote` keeps the first for anything reading a
# single string; `source_quotes` holds them all, as JSON.
_SKILLS_ADDED_COLUMNS = [
    ("source_quotes", "TEXT"),
    # THE SKILL SYSTEM, added after skills had been in use for months as a flat list of
    # course-wide style notes. Four columns, each answering a question the flat list
    # could not:
    #
    #   category      WHAT KIND of instruction this is — teaching flow, teaching
    #                 guidelines, examples & visuals, or a reviewer correction. The
    #                 writer needs the flow before it needs the wording rules, and a
    #                 reviewer correction outranks both; a flat list cannot say that.
    #   scope         WHO it governs: this course, one session of it, or every course.
    #   session_ref   which session, when scope='session'.
    #   instructions  THE SKILL'S OWN BULLETS, as JSON. An author who writes four
    #                 related lines under "Teaching Guidelines" has written ONE skill
    #                 with four instructions, not four skills. Splitting them loses the
    #                 grouping and the order the author put them in, and turns one
    #                 approval into four.
    #
    # All four are NULLABLE and every reader defaults them, so the rows written before
    # this existed are exactly what they always were: course-scoped, uncategorised,
    # single-instruction skills.
    ("category", "TEXT"),
    ("scope", "TEXT"),
    ("session_ref", "TEXT"),
    ("instructions", "TEXT"),
]


def _add_missing_columns(conn) -> list[str]:
    """Bring existing tables up to date. Idempotent."""
    added = []
    for table, cols in (("runs", _RUNS_ADDED_COLUMNS),
                        ("curriculum", _CURRICULUM_ADDED_COLUMNS),
                        ("teams", _TEAMS_ADDED_COLUMNS),
                        ("course_prereqs", _PREREQS_ADDED_COLUMNS),
                        ("course_skills", _SKILLS_ADDED_COLUMNS)):
        try:
            cur = conn.cursor()
            cur.execute(f"PRAGMA table_info({table})")
            have = {row[1] for row in cur.fetchall()}
        except Exception:
            continue
        if not have:
            continue             # table not created yet; _SCHEMA covers it
        for name, decl in cols:
            if name in have:
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                added.append(f"{table}.{name}")
                if table == "runs" and name == "review_done_at":
                    # Every finished run necessarily had its review finished: Create
                    # final TR Doc is the only path to a document and the button is
                    # disabled until every chunk is ticked. The fact simply had nowhere
                    # to be written down before.
                    conn.execute("UPDATE runs SET review_done_at=COALESCE(approved_at, "
                                 "updated) WHERE status='done' AND review_done_at IS NULL")
                if table == "runs" and name == "approved_at":
                    # BACKFILL, once, at the moment the column appears. Every finished
                    # run got there through Create final TR Doc, which the UI enables
                    # only after every chunk has been approved — guided review is the
                    # only path to a document. So a run with status='done' WAS approved
                    # by a person; the fact simply had nowhere to be written down until
                    # now. Without this, seventeen documents the reviewer approved would
                    # go on reading "Approved: 0" forever.
                    conn.execute("UPDATE runs SET approved_at=updated "
                                 "WHERE status='done' AND approved_at IS NULL")
            except Exception:
                pass             # a racing instance added it first — harmless
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
        print(f"[db] migrated schema: added column(s) {', '.join(added)}")
    _migrate_json_log()
    # Attribute courses that predate ownership being recorded — once, here, so the
    # first request after a deploy already draws a correctly scoped shelf instead of
    # every course on the instance. See backfill_course_owners.
    try:
        claimed = backfill_course_owners()
        if claimed:
            print(f"[db] attributed {len(claimed)} pre-existing course(s) to a creator: "
                  + ", ".join(f"{c} -> {who}" for c, who in claimed.items()))
        loose = unclaimed_courses()
        if loose:
            print(f"[db] {len(loose)} course(s) have no recorded creator and no owning "
                  f"team, so they stay visible to everyone until something writes to "
                  f"them: " + ", ".join(loose))
    except Exception as e:
        print(f"[db] course-owner backfill skipped: {e!r}")


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
def create_team(name: str, course: str | None, created_by: str,
                owner_email: str | None = None) -> int:
    """Create a team. `owner_email` is its course owner — see _TEAMS_ADDED_COLUMNS.

    `created_by` is the admin who set the team up and is only a record of that; the
    OWNER is who can then run it. They are usually different people, which is the whole
    point of naming one.
    """
    owner = (owner_email or "").strip().lower() or None
    tid = _exec("INSERT INTO teams (name, course, created_by, created_at, owner_email) "
                "VALUES (?,?,?,?,?)", (name, course, created_by, _now(), owner))
    if owner and tid:
        add_member(int(tid), owner)
    return tid


def rename_team(team_id: int, name: str) -> bool:
    """Change a team's display name. Nothing else moves.

    Safe precisely because the name is NOT a key: every lookup in this module goes by
    `teams.id` (membership, courses, ownership) or by course name (history), so a rename
    cannot orphan a member, a curriculum, or a run. The one thing that reads it is the
    screen — which is the whole reason to be able to fix it.
    """
    name = (name or "").strip()
    if not name:
        return False
    try:
        _exec("UPDATE teams SET name=? WHERE id=?", (name, int(team_id)))
        return True
    except Exception:
        return False


def set_team_course(team_id: int, course: str) -> None:
    _exec("UPDATE teams SET course=? WHERE id=?", (course, team_id))


def add_member(team_id: int, user_email: str) -> None:
    _exec("INSERT OR IGNORE INTO team_members (team_id, user_email) VALUES (?,?)",
          (team_id, user_email))


def remove_member(team_id: int, user_email: str) -> None:
    _exec("DELETE FROM team_members WHERE team_id=? AND user_email=?", (team_id, user_email))


def set_team_owner(team_id: int, email: str | None) -> str | None:
    """Name (or re-name) the team's course owner. Admin-only at the endpoint.

    The owner is added as a MEMBER at the same time: they are the person responsible for
    the team's course, so a team whose owner cannot open its own workspace is not a
    configuration anyone wants. Returns the email recorded, or None if it was cleared.
    """
    email = (email or "").strip().lower() or None
    _exec("UPDATE teams SET owner_email=? WHERE id=?", (email, int(team_id)))
    if email:
        add_member(int(team_id), email)
    return email


def team_owner(team_id: int) -> str | None:
    try:
        rows = _query("SELECT owner_email FROM teams WHERE id=?", (int(team_id),))
    except Exception:
        return None
    if not rows:
        return None
    return (rows[0].get("owner_email") or "").lower() or None


def can_manage_team(email: str, team_id: int, *, is_admin: bool = False,
                    all_teams: list[dict] | None = None) -> bool:
    """May this person add and remove members of this team?

    An admin, or the team's own course owner — and nobody else. An ordinary member
    cannot: being able to see a team's work is not the same as deciding who else can.
    """
    if is_admin:
        return True
    email = (email or "").lower()
    if not email:
        return False
    if all_teams is not None:
        t = next((x for x in all_teams if x.get("id") == int(team_id)), None)
        return bool(t) and (t.get("owner_email") or "").lower() == email
    return team_owner(team_id) == email


def delete_team(team_id: int) -> None:
    """Remove a team: its members, its course attachments, and the team itself.

    The `team_courses` rows were being left behind. They were invisible — teams() groups
    them by team_id and only attaches them to a team that exists — so nothing broke, but
    the table accumulated rows pointing at nothing, and team_course_list() reads it with
    no join, so anything that ever reused an id would inherit a dead team's courses.
    The COURSES themselves are untouched: deleting a team ends the sharing, it does not
    delete anyone's curriculum.
    """
    _exec("DELETE FROM team_members WHERE team_id=?", (team_id,))
    try:
        _exec("DELETE FROM team_courses WHERE team_id=?", (team_id,))
    except Exception:
        pass                      # a database predating team_courses has nothing to clear
    _exec("DELETE FROM teams WHERE id=?", (team_id,))


def team_add_course(team_id: int, course: str) -> bool:
    """Give a team another course to work on."""
    course = (course or "").strip()
    if not course:
        return False
    try:
        _exec("INSERT OR IGNORE INTO team_courses (team_id, course, added_at) "
              "VALUES (?,?,?)", (int(team_id), course, _now()))
        # Keep the legacy single column meaningful: the first course a team is given is
        # its primary one, which is what older readers (and the admin page) show.
        row = _query("SELECT course FROM teams WHERE id=?", (int(team_id),))
        if row and not (row[0].get("course") or "").strip():
            _exec("UPDATE teams SET course=? WHERE id=?", (course, int(team_id)))
        return True
    except Exception:
        return False


def team_remove_course(team_id: int, course: str) -> bool:
    """Take a course off a team.

    BOTH places have to be updated. `teams.course` is the team's primary course and is
    what team_course_list() and the admin page still read, so deleting only the
    team_courses row left the course apparently still attached — removed from the join
    table and reported right back by the legacy column. It is repointed at another course
    the team holds, or cleared.
    """
    try:
        _exec("DELETE FROM team_courses WHERE team_id=? AND course=?",
              (int(team_id), course))
        row = _query("SELECT course FROM teams WHERE id=?", (int(team_id),))
        if row and (row[0].get("course") or "").strip() == (course or "").strip():
            rest = [c for c in team_course_list(int(team_id)) if c != course]
            _exec("UPDATE teams SET course=? WHERE id=?",
                  (rest[0] if rest else None, int(team_id)))
        return True
    except Exception:
        return False


def team_course_list(team_id: int) -> list[str]:
    """Every course this team owns — the join table, plus the legacy primary column so
    a team created before team_courses existed is not suddenly course-less."""
    out = []
    try:
        out = [r["course"] for r in _query(
            "SELECT course FROM team_courses WHERE team_id=? ORDER BY course",
            (int(team_id),)) if r.get("course")]
    except Exception:
        out = []
    try:
        row = _query("SELECT course FROM teams WHERE id=?", (int(team_id),))
        primary = (row[0].get("course") or "").strip() if row else ""
        if primary and primary not in out:
            out.insert(0, primary)
    except Exception:
        pass
    return out


def teams() -> list[dict]:
    """Every team, with its members and courses — in THREE queries, not 1 + 2N.

    It used to fetch the team list and then, per team, one query for members and one
    for courses. That is invisible on a local SQLite file and expensive on the cloud
    database the app actually runs against, where every query is a network round-trip:
    a single page load was making 26 of them, which is where the "everything takes a
    moment" came from. Fetch the three tables whole and group them here instead.
    """
    rows = _query("SELECT * FROM teams ORDER BY created_at DESC")
    if not rows:
        return []
    members: dict = {}
    for r in _query("SELECT team_id, user_email FROM team_members"):
        members.setdefault(r["team_id"], []).append(r["user_email"])
    courses: dict = {}
    try:
        for r in _query("SELECT team_id, course FROM team_courses ORDER BY course"):
            if r.get("course"):
                courses.setdefault(r["team_id"], []).append(r["course"])
    except Exception:
        courses = {}
    for t in rows:
        # Normalised once, here, because every permission check compares against it.
        t["owner_email"] = (t.get("owner_email") or "").lower() or None
        t["members"] = members.get(t["id"], [])
        got = list(courses.get(t["id"], []))
        primary = (t.get("course") or "").strip()
        if primary and primary not in got:
            got.insert(0, primary)      # teams predating team_courses
        t["courses"] = got
    return rows


def teams_for_user(email: str, all_teams: list[dict] | None = None) -> list[dict]:
    """`all_teams` lets a caller that already holds the team list avoid re-fetching it —
    teams() is three queries, and this used to be called two or three times per request."""
    all_teams = teams() if all_teams is None else all_teams
    return [t for t in all_teams if email in (t.get("members") or [])]


def team_for_user_course(email: str, course: str | None):
    """The team this user belongs to for a given course (first match), or None."""
    for t in teams_for_user(email):
        if course and course in (t.get("courses") or []):
            return t["id"]
        if (t.get("course") or None) == (course or None):
            return t["id"]
    return None


def team_runs(team_id: int, courses: list[str] | None = None) -> list[dict]:
    """Everything the team has produced — by its stamp OR by any course it owns.

    Both halves are needed. The stamp is missing on runs made before the team existed,
    before a member joined, or by someone on no team; the course catches those. And a
    run stamped with the team but for a course since removed from it is still the
    team's work, so the stamp catches that. A member added next month sees the whole
    history either way, which is the point of gathering it here.
    """
    courses = team_course_list(team_id) if courses is None else courses
    # ONE query rather than one per course plus one for the stamp: on the cloud database
    # each of those is a network round-trip, and a team with several courses was paying
    # for all of them on every page load.
    q = "SELECT * FROM runs WHERE team_id = ?"
    args: list = [team_id]
    if courses:
        q += " OR course IN (" + ",".join("?" for _ in courses) + ")"
        args += courses
    q += " ORDER BY ts DESC LIMIT 1000"
    try:
        return [_shape_run(r) for r in _query(q, tuple(args))]
    except Exception:
        return []


# --------------------------------------------------------------------------- #
# course ownership
#
# Two independent facts decide who may open a course, and BOTH are needed:
#   · course_owners.created_by — the person who created it (their individual shelf);
#   · team_courses             — the teams it has been shared with (their shelves).
# Everything below resolves visibility from those two and nothing else.
# --------------------------------------------------------------------------- #
def claim_course(course: str, email: str | None) -> str | None:
    """Record `email` as the creator of `course`. FIRST CLAIM WINS.

    Called on the paths that bring a course into existence (selecting a new name,
    saving or importing a curriculum for it). Idempotent by design: a course is created
    once, so a later caller writing its own email over the original would quietly hand
    somebody else's course to whoever edited it most recently.

    Returns the effective owner after the call — which is the EXISTING owner when there
    already was one, so a caller can tell a claim from a no-op.
    """
    course = (course or "").strip()
    email = (email or "").strip().lower() or None
    if not course or not email:
        return course_owner(course) if course else None
    try:
        _exec("INSERT OR IGNORE INTO course_owners (course, created_by, created_at) "
              "VALUES (?,?,?)", (course, email, _now()))
    except Exception:
        return None
    return course_owner(course)


def set_course_owner(course: str, email: str | None) -> str | None:
    """Set a course's owner OUTRIGHT, replacing whoever was recorded.

    Deliberately not claim_course(), which is first-claim-wins and must stay that way:
    an automatic claim on a save must never quietly move a course to whoever edited it
    last. This is the admin's override — the path that ASSIGNS ownership, used when a
    team is created and when its owner is re-assigned.
    """
    course = (course or "").strip()
    email = (email or "").strip().lower() or None
    if not course:
        return None
    if not email:
        try:
            _exec("DELETE FROM course_owners WHERE course=?", (course,))
        except Exception:
            pass
        return None
    try:
        # No UPSERT: libSQL and older SQLite disagree on ON CONFLICT support in enough
        # places that a delete-then-insert is the portable form, and this runs once per
        # assignment rather than on any hot path.
        _exec("DELETE FROM course_owners WHERE course=?", (course,))
        _exec("INSERT INTO course_owners (course, created_by, created_at) VALUES (?,?,?)",
              (course, email, _now()))
    except Exception:
        return None
    return email


def course_owners() -> dict:
    """{course: creator_email} for every course whose creator is recorded."""
    try:
        return {r["course"]: (r.get("created_by") or "").lower()
                for r in _query("SELECT course, created_by FROM course_owners")
                if r.get("course") and r.get("created_by")}
    except Exception:
        return {}


def course_owner(course: str) -> str | None:
    """One course's creator. A targeted read, not course_owners() filtered: this is on
    the authorisation path of every request, and on the cloud database each query is a
    network round-trip."""
    course = (course or "").strip()
    if not course:
        return None
    try:
        rows = _query("SELECT created_by FROM course_owners WHERE course = ?", (course,))
    except Exception:
        return None
    if not rows:
        return None
    return (rows[0].get("created_by") or "").lower() or None


def backfill_course_owners() -> dict:
    """Attribute the courses that existed before ownership was recorded. Runs once, at
    init, and is a no-op afterwards.

    Without this every pre-existing course would be owner-less on the day this ships,
    and the person who imported a 34-session curriculum would open the app to an empty
    shelf. Two signals stand in for the missing record, strongest first:

      1. the EARLIEST run against the course — whoever generated the first document
         for it is who was working on it, and on a real instance almost every course
         has one;
      2. the creator of a team that owns it — a course attached to a team was imported
         by somebody who was working in that team.

    A course with neither signal stays unattributed on purpose: guessing would be
    inventing an owner. `can_use_course` treats those as UNCLAIMED rather than
    forbidden, so an imported-but-never-used curriculum is not stranded, and the first
    write to it claims it properly.
    """
    have = course_owners()
    known: set = set()
    try:
        known.update(r["course"] for r in
                     _query("SELECT DISTINCT course FROM curriculum WHERE course IS NOT NULL"))
    except Exception:
        pass
    all_teams = teams()
    for t in all_teams:
        known.update(t.get("courses") or [])
    todo = sorted(c for c in known if c and c not in have)
    if not todo:
        return {}
    # One query for the earliest run per course rather than one per course: on the
    # cloud database each is a network round-trip, and this runs during startup.
    first_run: dict = {}
    try:
        for r in _query("SELECT course, user_email, MIN(ts) AS first_ts FROM runs "
                        "WHERE course IS NOT NULL AND user_email IS NOT NULL "
                        "GROUP BY course"):
            if r.get("course") and r.get("user_email"):
                first_run[r["course"]] = r["user_email"].lower()
    except Exception:
        first_run = {}
    team_creator: dict = {}
    for t in all_teams:
        for c in (t.get("courses") or []):
            if c not in team_creator and (t.get("created_by") or "").strip():
                team_creator[c] = t["created_by"].lower()
    done = {}
    for course in todo:
        owner = first_run.get(course) or team_creator.get(course)
        if not owner:
            continue
        if claim_course(course, owner):
            done[course] = owner
    return done


def unclaimed_courses() -> list[str]:
    """Courses nobody is recorded as having created and no team owns.

    Not an error state — a curriculum can be imported and never generated against — but
    worth naming at startup, because these are the ones every signed-in user can still
    see until somebody writes to them.
    """
    have = set(course_owners())
    owned_by_team = {c for t in teams() for c in (t.get("courses") or [])}
    try:
        known = {r["course"] for r in
                 _query("SELECT DISTINCT course FROM curriculum WHERE course IS NOT NULL")
                 if r.get("course")}
    except Exception:
        known = set()
    return sorted(known - have - owned_by_team)


def can_use_course(email: str, course: str, *, is_admin: bool = False,
                   all_teams: list[dict] | None = None,
                   owners: dict | None = None) -> bool:
    """May this person read and write this course?

    · admin              — yes, everything (the admin dashboard reports on the whole
                           instance, and an admin is who fixes a mis-scoped course);
    · created it         — yes, it is on their individual shelf;
    · a team owns it     — yes if they are on that team, no if they are not;
    · nobody owns it     — yes: an UNCLAIMED course belongs to no one, so there is no
                           one to keep it from, and refusing would strand a curriculum
                           imported before ownership was recorded with no way back in.
                           The first write claims it.
    """
    course = (course or "").strip()
    if not course:
        return True                      # nothing named — the caller resolves a default
    if is_admin:
        return True
    email = (email or "").lower()
    # Owner first, and on its own query: the common case is somebody opening a course
    # they created, and that answers it in ONE round-trip without reading the team
    # tables at all.
    owner = owners.get(course) if owners is not None else course_owner(course)
    if owner and owner == email:
        return True
    all_teams = teams() if all_teams is None else all_teams
    owning_teams = [t for t in all_teams if course in (t.get("courses") or [])]
    if owning_teams:
        return any(email in (t.get("members") or []) for t in owning_teams)
    return owner is None                 # unclaimed and team-less


def courses_for_user(email: str, *, is_admin: bool = False,
                     all_teams: list[dict] | None = None,
                     counts: dict | None = None,
                     owners: dict | None = None) -> list[dict]:
    """Which courses this person may work on, and who else is on each.

    A course belongs to the person who CREATED it, and to any team it has been shared
    with. Those are the only two ways onto this list:

      · admin            — every course the agent holds;
      · created by them  — their own shelf, whether or not a team is involved;
      · their teams'     — every course each team they are on owns, and nothing else;
      · unclaimed        — a course with no recorded creator and no owning team, which
                           is what a curriculum imported before ownership was recorded
                           looks like. Visible so it is not stranded; the first write
                           to it claims it.

    It used to be "on a team -> that team's courses, on no team -> EVERYTHING", which
    meant a new signee saw every course anyone in the org had imported, and someone on
    a team could not see the course they had made themselves. Both halves of that are
    fixed here.

    Each row carries WHY it is visible, so the UI can keep the individual shelf and the
    team shelf apart instead of pooling them:
      `created_by`  who made it (None if unclaimed)
      `mine`        this user created it        -> individual workspace
      `teams`       teams that own it           -> team workspaces
      `shared`      one of THIS user's teams owns it
      `unclaimed`   nobody owns it yet
    """
    # One count query instead of curriculum(name) per course, and ONE teams() rather
    # than teams() plus teams_for_user() fetching it all over again.
    # A caller that already holds these passes them in: teams() is three queries and
    # the counts are one, and a page that needs both was paying for them twice.
    counts = curriculum_session_counts() if counts is None else counts
    known = set(counts)
    all_teams = teams() if all_teams is None else all_teams
    for t in all_teams:
        known.update(t.get("courses") or [])
    owners = course_owners() if owners is None else owners
    email = (email or "").lower()
    mine = teams_for_user(email, all_teams)
    my_team_courses = {c for t in mine for c in (t.get("courses") or [])}
    my_own = {c for c, who in owners.items() if who == email}
    team_owned = {c for t in all_teams for c in (t.get("courses") or [])}
    unclaimed = {c for c in known if c not in owners and c not in team_owned}
    visible = known if is_admin else (my_own | my_team_courses | unclaimed) & known
    out = []
    for name in sorted(visible):
        owning = [t for t in all_teams if name in (t.get("courses") or [])]
        members = sorted({m for t in owning for m in (t.get("members") or [])})
        out.append({
            "name": name,
            "sessions": counts.get(name, 0),
            "teams": [t["name"] for t in owning],
            "members": members,
            "created_by": owners.get(name),
            "mine": name in my_own,
            "shared": name in my_team_courses,
            "unclaimed": name in unclaimed,
            # WHICH SHELF THIS SITS ON, for this person — computed once, here, so the
            # course list, the workspace payload and the picker cannot answer it
            # differently. A course shared with a team you are on belongs to the TEAM: it
            # was on both shelves at once, which is not what "moved it to the team" means
            # and left the same course appearing twice.
            #
            # Two ways onto the team shelf, and the second one matters for ADMINS. The
            # first is "a team you are on owns it". The second is "a team owns it and you
            # did not create it" — without which an admin, who can see every course, had
            # every OTHER team's courses filed onto their personal shelf, because no team
            # THEY are on owned any of them. A team's course is the team's, whoever asks.
            #
            # What both clauses protect is the one case that must NOT move: a course YOU
            # created that an admin attached to a team you are not a member of. That stays
            # individual, because otherwise it vanishes from every shelf while
            # can_use_course still (correctly) lets you open it — your own course,
            # reachable by URL and by nothing else.
            "shelf": ("team" if (name in my_team_courses
                                 or (name in team_owned and name not in my_own))
                      else "individual"),
        })
    return out


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
            status, stage, enforce_time, skills_version)
           VALUES (?,?,?,?,?,?,?,?, 'running', 'queued', ?, ?)""",
        (run_id, now, now, user_email, course, team_id, session_no, title,
         1 if enforce_time else 0,
         # Stamped at the START, because that is the set the document was written under.
         # Reading it at finalize would record whatever the skills happened to be by the
         # time a long review ended.
         skills_version(course, session_no) or None))


def update_stage(run_id: str, stage: str) -> None:
    _exec("UPDATE runs SET stage=?, updated=? WHERE id=?", (stage, _now(), run_id))


def update_cost(run_id: str, cost: dict | None, calls: list | None = None) -> None:
    """Write a run's cost-so-far WITHOUT finishing it.

    Cost used to be recorded only by finish_run, so a guided run the reviewer never
    finalised reported $0.00 — while having paid for one LLM call per chunk. Three
    abandoned Session-30 runs sat in the dashboard at $0.0000 with all their chunks
    generated, so the org-wide spend was understated by whatever those cost. Guided
    mode now calls this after every chunk, which is also the checkpoint granularity,
    so an interrupted run's spend is never invisible.
    """
    cost = cost or {}
    _exec(
        """UPDATE runs SET cost=?, total_tokens=?, cost_json=?, calls_json=?, updated=?
           WHERE id=?""",
        (cost.get("cost"), cost.get("total_tokens"), json.dumps(cost),
         json.dumps(calls or []), _now(), run_id))


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
    # TWO different verdicts, and conflating them is what made the dashboard read
    # "Approved: 0" for seventeen finished documents:
    #   · gates_passed — the GRADERS' verdict. Every guardrail passed, inside the time
    #     and page budgets, judge above bar. Strict by design; most real documents
    #     finish with something still flagged, and a reviewer signs off anyway.
    #   · approved      — a PERSON reviewed every chunk and pressed Create final TR Doc.
    # `accepted` is kept as the old name of the first so nothing reading it breaks.
    d["accepted"] = None if d.get("accepted") is None else bool(d["accepted"])
    d["gates_passed"] = d["accepted"]
    d["approved"] = bool(d.get("approved_at"))
    # The review finished — every chunk generated and ticked. Strictly earlier than
    # `approved`, and the two differ exactly where somebody reviewed a whole document and
    # then never pressed the button.
    d["review_done"] = bool(d.get("review_done_at"))
    d["enforce_time"] = None if d.get("enforce_time") is None else bool(d["enforce_time"])
    d["cost"] = json.loads(d.pop("cost_json", None) or "{}")
    d["calls"] = json.loads(d.pop("calls_json", None) or "[]")
    d["duration_min"] = _duration_min(d)
    d["abandoned"] = _is_abandoned(d)
    # A single, UI-friendly outcome: completed | approved | failed | abandoned | running
    if d["status"] == "done":
        d["outcome"] = "approved" if d["approved"] else "completed"
    elif d["status"] == "error":
        d["outcome"] = "failed"
    else:
        d["outcome"] = "abandoned" if d["abandoned"] else "running"
    return d


def mark_review_done(run_id: str) -> None:
    """Stamp the moment every chunk of this run had been generated and approved.

    First one wins: a reviewer who un-ticks a chunk, changes it and ticks it again has
    finished reviewing once, not twice, and the interesting fact is when the document was
    first fully reviewed.
    """
    try:
        _exec("UPDATE runs SET review_done_at=?, updated=? "
              "WHERE id=? AND review_done_at IS NULL", (_now(), _now(), run_id))
    except Exception:
        pass


def mark_approved(run_id: str, user_email: str | None) -> None:
    """Record that a PERSON signed this document off.

    Called at finalize, which the UI only enables once every chunk has been ticked, so
    reaching it IS the approval. Nothing recorded this before: the ticks lived in React
    state and died with the page, and the dashboard fell back to the graders' verdict.
    """
    _exec("UPDATE runs SET approved_by=?, approved_at=?, updated=? WHERE id=?",
          (user_email, _now(), _now(), run_id))


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


def run_for_output(run_id: str | None = None,
                   filename: str | None = None) -> dict | None:
    """The run that produced a given output, found by id or by rendered filename.

    Used to answer "whose document is this?" on the download / preview / Google-Doc
    paths, which identify an output by run id or exact filename and so cannot otherwise
    tell one team's document from another's. Returns None when nothing matches — an
    output on disk from before runs were recorded has no owner to check.
    """
    if run_id:
        rows = _query("SELECT * FROM runs WHERE id = ?", (run_id,))
        if rows:
            return _shape_run(rows[0])
    if filename:
        rows = _query("SELECT * FROM runs WHERE docx_name = ? ORDER BY ts DESC LIMIT 1",
                      (filename,))
        if rows:
            return _shape_run(rows[0])
        # The .md preview and the .docx share a run; match on the stem so a preview
        # request for "X.md" still finds the run that rendered "X.docx".
        stem = filename.rsplit(".", 1)[0]
        rows = _query("SELECT * FROM runs WHERE docx_name LIKE ? ORDER BY ts DESC "
                      "LIMIT 1", (stem + ".%",))
        if rows:
            return _shape_run(rows[0])
    return None


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
        e = out.setdefault(b, {"bucket": b, "runs": 0, "approved": 0, "gates_passed": 0,
                               "cost": 0.0, "tokens": 0})
        e["runs"] += 1
        # `approved` is the PERSON's sign-off; `accepted`/`gates_passed` is the GRADERS'.
        # This counted the graders under the name of the human, which is the same
        # conflation _shape_run exists to warn about.
        if r["approved"]:
            e["approved"] += 1
        if r["accepted"]:
            e["gates_passed"] += 1
        e["cost"] += (r["cost"] or {}).get("cost", 0) or 0
        e["tokens"] += (r["cost"] or {}).get("total_tokens", 0) or 0
    return [{**v, "cost": round(v["cost"], 6)} for v in sorted(out.values(), key=lambda x: x["bucket"])]


def summary() -> dict:
    rs = runs(limit=100000)
    done = [r for r in rs if r["status"] == "done"]
    # TWO DIFFERENT VERDICTS, and the admin dashboard was reporting one under the other's
    # name — the very confusion _shape_run's docstring exists to warn about:
    #   approved      a PERSON reviewed every chunk and pressed Create final TR Doc;
    #   gates_passed  the GRADERS passed everything (`accepted`) — strict by design, and
    #                 most real documents finish with something still flagged.
    # So "Completed" and "Approved" differed on screen for a reason nobody could see: the
    # Approved card was counting grader passes, while the runs table right below it
    # labelled the same rows from `outcome`, which uses the human sign-off.
    approved = [r for r in done if r["approved"]]
    gates_passed = [r for r in done if r["accepted"]]
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
        # Every chunk generated and ticked. Counted over ALL runs, not just finished
        # ones: the number worth seeing is the one that includes reviews that were
        # completed and then never turned into a document.
        "review_done": len([r for r in rs if r.get("review_done")]),
        "reviewed_not_created": len([r for r in rs if r.get("review_done")
                                     and not r.get("approved")]),
        "approved": len(approved),                   # a person signed it off
        "gates_passed": len(gates_passed),           # every grader passed it
        "completion_rate": round(100 * len(done) / len(rs), 1) if rs else 0,
        "approval_rate": round(100 * len(approved) / len(done), 1) if done else 0,
        # Kept under its old name so nothing reading it breaks, but it is the GRADERS'
        # rate and the dashboard must not label it "Approval rate".
        "acceptance_rate": round(100 * len(gates_passed) / len(done), 1) if done else 0,
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
                                 "gates_passed": 0,
                                 "abandoned": 0, "failed": 0, "cost": 0.0, "tokens": 0,
                                 "courses": set(), "_durations": [], "last": r["ts"]})
        e["runs"] += 1
        if r["status"] == "done":
            e["completed"] += 1
            if r["duration_min"] is not None:
                e["_durations"].append(r["duration_min"])
        if r["approved"]:              # the person's sign-off, not the graders' verdict
            e["approved"] += 1
        if r["accepted"]:
            e["gates_passed"] += 1
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


# --------------------------------------------------------------------------- #
# CURRICULUM — the agent's own copy of the course, editable in the app.
#
# The sheet is an import format, not a dependency: it is read once to populate this
# table and thereafter only if the user explicitly asks to re-import. Everything the
# generator needs (sessions, takeaways, deck links) is served from here.
# --------------------------------------------------------------------------- #
def _row_to_session(r: dict) -> dict:
    return {
        "session_no": r.get("session_no"),
        "topic": r.get("topic") or "",
        "session_name": r.get("session_name") or "",
        "key_takeaways": [t for t in (r.get("key_takeaways") or "").split("\n") if t.strip()],
        "ppt_link": r.get("ppt_link") or "",
        "deck_hash": r.get("deck_hash") or "",
        "deck_status": r.get("deck_status") or ("linked" if (r.get("ppt_link") or "") else "none"),
        # Per-session budget overrides; None means "inherit the course's".
        "max_pages": r.get("max_pages"),
        "max_slides": r.get("max_slides"),
        "updated_at": r.get("updated_at"),
    }


def curriculum(course: str) -> list[dict]:
    """Every session of a course, in session order."""
    try:
        rows = _query("SELECT * FROM curriculum WHERE course = ? ORDER BY session_no",
                      (course or "",))
    except Exception:
        return []
    return [_row_to_session(r) for r in rows]


def course_settings(course: str) -> dict:
    """A course's own page/slide budgets, or {} when it uses the harness defaults."""
    try:
        rows = _query("SELECT max_pages, max_slides FROM course_settings WHERE course=?",
                      (course,))
    except Exception:
        return {}
    return rows[0] if rows else {}


def set_course_settings(course: str, *, max_pages=None, max_slides=None) -> bool:
    """Set (or clear, by passing None) a course's budgets."""
    try:
        _exec("""INSERT INTO course_settings (course, max_pages, max_slides, updated_at)
                 VALUES (?,?,?,?)
                 ON CONFLICT(course) DO UPDATE SET
                   max_pages=excluded.max_pages, max_slides=excluded.max_slides,
                   updated_at=excluded.updated_at""",
              (course, max_pages, max_slides, _now()))
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# prerequisite courses (what the learner already knows)
# --------------------------------------------------------------------------- #
def prereqs(course: str) -> list[dict]:
    try:
        rows = _query("SELECT * FROM course_prereqs WHERE course=? ORDER BY prereq",
                      ((course or "").strip(),))
    except Exception:
        return []
    for r in rows:
        # Rows written before the two kinds existed are internal — that was the only kind.
        r["kind"] = (r.get("kind") or "course")
    return rows


def add_prereq(course: str, prereq: str, *, added_by: str | None = None,
               kind: str = "course") -> bool:
    """Link a prerequisite. False if it was already there, or is the course itself.

    `kind` is "course" (one this agent holds — its decks are its own) or "external" (one
    taught elsewhere — a name, whose decks belong to THIS course because there is no
    course of their own to hang them on).
    """
    course, prereq = (course or "").strip(), (prereq or "").strip()
    if not course or not prereq or course == prereq:
        return False
    if any(p["prereq"] == prereq for p in prereqs(course)):
        return False
    kind = "external" if str(kind).lower() == "external" else "course"
    try:
        _exec("INSERT OR IGNORE INTO course_prereqs (course, prereq, kind, added_by, "
              "added_at) VALUES (?,?,?,?,?)",
              (course, prereq, kind, (added_by or "").lower() or None, _now()))
        return True
    except Exception:
        return False


def remove_prereq(course: str, prereq: str) -> bool:
    """Unlink a prerequisite — and, if it was EXTERNAL, delete its decks.

    An internal prerequisite's decks are its own course's and are left alone. An external
    one's exist only because this course declared it, so nothing else would ever read
    them again.
    """
    course, prereq = (course or "").strip(), (prereq or "").strip()
    row = next((p for p in prereqs(course) if p["prereq"] == prereq), None)
    try:
        _exec("DELETE FROM course_prereqs WHERE course=? AND prereq=?", (course, prereq))
    except Exception:
        return False
    if row and row.get("kind") == "external":
        try:
            from . import pptx_ingest
            pptx_ingest.drop_prereq_decks(course, prereq)
        except Exception as e:
            print(f"[prereqs] could not drop {prereq!r}'s decks: {e!r}")
    return True


# --------------------------------------------------------------------------- #
# course skills (authored instructions, approved before they take effect)
# --------------------------------------------------------------------------- #
# The course every course sees. A skill stored under this name is a HOUSE rule: it
# governs every course on the instance, and it is the weakest tier in the precedence
# order, so anything a course or a session says about the same thing wins. It is a
# reserved course name rather than a nullable column so that every existing query,
# index and foreign relation keeps working unchanged.
GLOBAL_COURSE = "*"


def skill_body(text) -> str:
    """A skill's text, KEPT AS IT WAS WRITTEN.

    A skill is a fragment of the prompt the writer works from, not a database label, and
    an author writes it the way they would write any instruction: a paragraph of context,
    then the points it breaks into, sometimes both. All of that used to go through
    `" ".join(text.split())`, which collapses newlines along with spaces — so a note laid
    out as

        Explain every snippet line by line.
        - name the variable before it is used
        - say what the line does, not what it says

    was stored, shown for approval, and handed to the model as one run-on paragraph. The
    author's layout is part of the instruction; a list is a list because they meant it to
    be read as one.

    Tidied, not flattened: runs of spaces WITHIN a line go, trailing whitespace goes, and
    three blank lines become one. Line breaks and paragraph breaks stay.
    """
    raw = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    out: list[str] = []
    for line in raw.split("\n"):
        line = " ".join(line.split())
        if line:
            out.append(line)
        elif out and out[-1] != "":       # one blank line between blocks, never more
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _shape_skill(r: dict) -> dict:
    try:
        r["check"] = json.loads(r.pop("check_json", None) or "null")
    except Exception:
        r["check"] = None
    # Always a LIST, whatever the row holds. Rows written before source_quotes existed
    # have only the single column, and a caller should not have to know which.
    try:
        quotes = json.loads(r.get("source_quotes") or "null")
    except Exception:
        quotes = None
    if not isinstance(quotes, list) or not quotes:
        quotes = [r["source_quote"]] if r.get("source_quote") else []
    r["source_quotes"] = [q for q in quotes if q]
    # THE SKILL'S OWN INSTRUCTIONS. One skill, several lines — see the column comment.
    # A row written before this existed has none, and its `text` IS the instruction;
    # callers read `instructions` and fall back to `text`, so neither has to know which
    # kind of row it is holding.
    try:
        ins = json.loads(r.get("instructions") or "null")
    except Exception:
        ins = None
    r["instructions"] = [skill_body(i) for i in ins
                         if str(i or "").strip()] if isinstance(ins, list) else []
    r["scope"] = (r.get("scope") or "course").strip().lower() or "course"
    r["category"] = (r.get("category") or "").strip().lower() or None
    ref = r.get("session_ref")
    r["session_ref"] = str(ref).strip() if ref not in (None, "") else None
    return r


def _session_key(session) -> str | None:
    """A session identity as stored. `12`, `"12"` and `" 12 "` are the same session.

    Anything that is not a bare number is kept as trimmed text, so a course that
    numbers its sessions '3a' is not silently mapped onto session 3.
    """
    if session in (None, ""):
        return None
    s = str(session).strip()
    if not s:
        return None
    try:
        return str(int(s))
    except (TypeError, ValueError):
        return s


def skills(course: str, *, include_retired: bool = False, session=None,
           include_global: bool = False) -> list[dict]:
    """One course's skills, newest last. Retired ones are excluded unless asked for —
    they are kept so an old document can still be explained, not to be applied.

    EVERY session's skills by default, because this is what the AUTHORING screen lists
    and an author managing session 12's skills has to be able to see them from anywhere.
    Pass `session` to narrow it to the course-wide skills plus that one session's —
    which is what a RUN needs, and what `approved_skills` does.
    """
    names = [(course or "").strip()]
    if include_global:
        names.append(GLOBAL_COURSE)
    q = f"SELECT * FROM course_skills WHERE course IN ({','.join('?' * len(names))})"
    if not include_retired:
        q += " AND status != 'retired'"
    q += " ORDER BY id"
    try:
        rows = [_shape_skill(r) for r in _query(q, tuple(names))]
    except Exception:
        return []
    return _for_session(rows, session) if session not in (None, "") else rows


def _for_session(rows: list[dict], session) -> list[dict]:
    """Drop the session-scoped skills that belong to a DIFFERENT session.

    A session skill applies only to its session. Kept as a filter over already-fetched
    rows rather than a WHERE clause so that the one rule — 'course-wide always, this
    session's as well' — is written once and cannot drift between the two readers.
    """
    key = _session_key(session)
    return [r for r in rows
            if r.get("scope") != "session" or r.get("session_ref") == key]


def approved_skills(course: str, *, session=None) -> list[dict]:
    """The skills that actually govern generation for this course and session.

    Approved only. A DRAFT that already applied would make the approval step theatre —
    and the whole point of the workflow is that nothing reaches the writer unreviewed.

    Carries THREE tiers: this course's own skills, the session-scoped ones belonging to
    THIS session, and the global house skills. Ordering them by authority is
    src.skills's job, not the store's — see skills.resolve().
    """
    names = ((course or "").strip(), GLOBAL_COURSE)
    try:
        rows = [_shape_skill(r) for r in _query(
            "SELECT * FROM course_skills WHERE course IN (?,?) AND status='approved' "
            "ORDER BY id", names)]
    except Exception:
        return []
    return _for_session(rows, session)


def add_skill(course: str, text: str, *, kind: str = "style", source: str = "user",
              created_by: str | None = None, check: dict | None = None,
              source_quote: str | None = None,
              source_quotes: list | None = None,
              category: str | None = None, scope: str = "course",
              session_ref=None, instructions: list | None = None) -> int | None:
    """Add a skill as a DRAFT. Returns its id, or None.

    `source_quotes` is every phrase the requirement was drawn from — a person says the
    same thing twice in different words and it is still one rule. `source_quote` stays
    the first of them, so callers that want one string keep working.

    `instructions` are the skill's own lines. Four related instructions written under
    one heading are ONE skill with four instructions — storing them as four skills would
    lose the author's grouping and their order, and would turn one approval into four.
    `text` stays the skill's own sentence: what the whole group is for.
    """
    course, text = (course or "").strip(), skill_body(text)
    if not course or not text:
        return None
    # The QUOTES are still flattened: they are evidence of what the author typed, matched
    # as substrings against a normalised copy of their note, not something anyone reads
    # as a document.
    quotes = [" ".join(str(q).split()) for q in (source_quotes or []) if str(q).strip()]
    if source_quote and source_quote not in quotes:
        quotes.insert(0, source_quote)
    lines = [skill_body(i) for i in (instructions or []) if str(i or "").strip()]
    scope = (scope or "course").strip().lower()
    if scope not in ("course", "session", "global"):
        scope = "course"
    ref = _session_key(session_ref) if scope == "session" else None
    if scope == "session" and not ref:
        # A session skill with no session is a course skill that would silently apply
        # everywhere. Refuse it rather than quietly widening its reach.
        return None
    if scope == "global":
        course = GLOBAL_COURSE
    now = _now()
    try:
        return _exec(
            "INSERT INTO course_skills (course, text, kind, source, source_quote, "
            "source_quotes, status, check_json, version, created_by, created_at, "
            "updated_at, category, scope, session_ref, instructions) "
            "VALUES (?,?,?,?,?,?,'draft',?,1,?,?,?,?,?,?,?)",
            (course, text, kind, source, (quotes[0] if quotes else None),
             json.dumps(quotes) if quotes else None,
             json.dumps(check) if check else None,
             (created_by or "").lower() or None, now, now,
             (category or "").strip().lower() or None, scope, ref,
             json.dumps(lines) if lines else None))
    except Exception as e:
        print(f"[db] add_skill failed: {e!r}")
        return None


def edit_skill(skill_id: int, text: str, *, check: dict | None = None,
               instructions: list | None = None) -> bool:
    """Change a skill's wording. It goes BACK TO DRAFT.

    An approval is of the words that were approved. Letting an edit keep the approval
    would mean a skill nobody signed off governing every document from then on. That
    covers the INSTRUCTIONS too: they are the part a writer actually follows, so an edit
    that rewrote them while keeping the approval would be the same hole by another door.

    `instructions=None` leaves them as they are — an edit to the skill's own sentence is
    not a decision to discard its lines.
    """
    text = skill_body(text)
    if not text:
        return False
    sets = ["text=?", "check_json=?"]
    args = [text, json.dumps(check) if check else None]
    if instructions is not None:
        lines = [skill_body(i) for i in instructions if str(i or "").strip()]
        sets.append("instructions=?")
        args.append(json.dumps(lines) if lines else None)
    try:
        _exec(f"UPDATE course_skills SET {', '.join(sets)}, status='draft', "
              "approved_by=NULL, approved_at=NULL, version=version+1, updated_at=? "
              "WHERE id=?", tuple(args) + (_now(), int(skill_id)))
        return True
    except Exception:
        return False


def approve_skill(skill_id: int, who: str | None) -> bool:
    try:
        _exec("UPDATE course_skills SET status='approved', approved_by=?, approved_at=?, "
              "updated_at=? WHERE id=?",
              ((who or "").lower() or None, _now(), _now(), int(skill_id)))
        return True
    except Exception:
        return False


def retire_skill(skill_id: int, who: str | None) -> bool:
    """Stop a skill applying. The row stays — see the table comment."""
    try:
        _exec("UPDATE course_skills SET status='retired', updated_at=? WHERE id=?",
              (_now(), int(skill_id)))
        return True
    except Exception:
        return False


def import_skills(from_course: str, to_course: str, who: str | None) -> int:
    """Copy another course's APPROVED skills in, as drafts. Returns how many were new.

    Drafts on purpose: a skill that was right for one course is a proposal for the next,
    not a decision already taken. Skills whose text is already present are skipped, so
    importing twice is a no-op rather than a pile of duplicates.

    COURSE-SCOPED SKILLS ONLY. A session skill is written about session 12 of the course
    it was written for — its numbering means nothing in another course — and a global
    skill already applies to the destination, so copying either would produce a rule
    that is at best a duplicate and at worst about somebody else's session.
    """
    src = [s for s in approved_skills(from_course) if s.get("scope") == "course"]
    if not src:
        return 0
    have = {" ".join((s.get("text") or "").split()).lower()
            for s in skills(to_course, include_retired=True)}
    n = 0
    for s in src:
        text = " ".join((s.get("text") or "").split())
        if text.lower() in have:
            continue
        if add_skill(to_course, text, kind=s.get("kind") or "style",
                     source=f"imported:{from_course}", created_by=who,
                     check=s.get("check"), category=s.get("category"),
                     instructions=s.get("instructions")):
            n += 1
            have.add(text.lower())
    return n


def skills_version(course: str, session=None) -> str:
    """A short fingerprint of the approved skills, stamped on a run.

    Without it there is no way to explain why last month's document differs from today's:
    the skills changed and nothing recorded which set produced which doc. It covers the
    SESSION's skills too, so two runs of different sessions of one course do not claim to
    have been written under the same set when they were not.
    """
    import hashlib
    parts = [f"{s['id']}:{s.get('version', 1)}"
             for s in approved_skills(course, session=session)]
    if not parts:
        return ""
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:8]


def course_profile(course: str) -> dict:
    """One course's stored overrides, or {} — the RAW row, not the resolved profile.
    Callers want src.profiles.for_course(), which merges this over the harness."""
    try:
        rows = _query("SELECT profile_json FROM course_profiles WHERE course=?",
                      ((course or "").strip(),))
    except Exception:
        return {}
    if not rows:
        return {}
    try:
        out = json.loads(rows[0].get("profile_json") or "{}")
        return out if isinstance(out, dict) else {}
    except Exception:
        return {}


def course_profiles() -> dict:
    """{course: overrides} for every course that has any. One query."""
    try:
        rows = _query("SELECT course, profile_json FROM course_profiles")
    except Exception:
        return {}
    out = {}
    for r in rows:
        try:
            v = json.loads(r.get("profile_json") or "{}")
        except Exception:
            continue
        if isinstance(v, dict) and r.get("course"):
            out[r["course"]] = v
    return out


def set_course_profile(course: str, overrides: dict) -> bool:
    """Store a course's overrides, VALIDATED. False if the profile was rejected.

    Validation lives in src.profiles so the rule and the resolution cannot drift apart;
    this is only the storage. Returns False rather than raising because every caller —
    the endpoint, a test, a migration — wants to say what was wrong, not unwind.
    """
    course = (course or "").strip()
    if not course:
        return False
    from . import profiles as _profiles
    ok, cleaned, _why = _profiles.validate(overrides)
    if not ok:
        return False
    try:
        _exec("DELETE FROM course_profiles WHERE course=?", (course,))
        if cleaned:
            _exec("INSERT INTO course_profiles (course, profile_json, updated_at) "
                  "VALUES (?,?,?)", (course, json.dumps(cleaned), _now()))
        return True
    except Exception as e:
        print(f"[db] set_course_profile({course!r}) failed: {e!r}")
        return False


def session_settings(course: str, session_no: int) -> dict:
    """One session's overrides, or {} when it inherits the course's."""
    try:
        rows = _query("SELECT max_pages, max_slides FROM curriculum "
                      "WHERE course=? AND session_no=?", (course, int(session_no)))
    except Exception:
        return {}
    return rows[0] if rows else {}


def set_session_settings(course: str, session_no: int, *, max_pages=None,
                         max_slides=None) -> bool:
    try:
        _exec("UPDATE curriculum SET max_pages=?, max_slides=?, updated_at=? "
              "WHERE course=? AND session_no=?",
              (max_pages, max_slides, _now(), course, int(session_no)))
        return True
    except Exception:
        return False


def curriculum_session_counts() -> dict:
    """course -> number of sessions, in one query. The course list used to run a full
    SELECT per course just to count its rows."""
    try:
        rows = _query("SELECT course, COUNT(*) AS n FROM curriculum GROUP BY course", ())
    except Exception:
        return {}
    return {r["course"]: r["n"] for r in rows if r.get("course")}


def curriculum_courses() -> list[str]:
    try:
        rows = _query("SELECT DISTINCT course FROM curriculum ORDER BY course", ())
    except Exception:
        return []
    return [r["course"] for r in rows if r.get("course")]


def curriculum_upsert(course: str, session_no: int, *, topic: str = "",
                      session_name: str = "", key_takeaways=None,
                      ppt_link: str | None = None) -> bool:
    """Insert or update one session.

    `ppt_link=None` leaves the existing link (and its deck_hash) alone — an edit to a
    takeaway must not look like a deck change. Passing a DIFFERENT link clears
    deck_hash, which is what marks the deck as needing ingestion; passing the SAME link
    keeps it, so saving the row again does not re-download anything.
    """
    kt = key_takeaways or []
    if isinstance(kt, str):
        kt = [l for l in kt.split("\n") if l.strip()]
    kt_text = "\n".join(str(x).strip() for x in kt if str(x).strip())
    try:
        prev = _query("SELECT ppt_link, deck_hash FROM curriculum "
                      "WHERE course=? AND session_no=?", (course, int(session_no)))
        old_link = (prev[0].get("ppt_link") if prev else "") or ""
        old_hash = (prev[0].get("deck_hash") if prev else "") or ""
        link = old_link if ppt_link is None else (ppt_link or "").strip()
        keep_hash = old_hash if link and link == old_link else ""
        status = "none" if not link else ("extracted" if keep_hash else "pending")
        _exec("""INSERT INTO curriculum
                   (course, session_no, topic, session_name, key_takeaways, ppt_link,
                    deck_hash, deck_status, updated_at)
                 VALUES (?,?,?,?,?,?,?,?,?)
                 ON CONFLICT(course, session_no) DO UPDATE SET
                   topic=excluded.topic, session_name=excluded.session_name,
                   key_takeaways=excluded.key_takeaways, ppt_link=excluded.ppt_link,
                   deck_hash=excluded.deck_hash, deck_status=excluded.deck_status,
                   updated_at=excluded.updated_at""",
              (course, int(session_no), topic or "", session_name or "", kt_text,
               link, keep_hash, status, _now()))
        return True
    except Exception:
        return False


def curriculum_shift_from(course: str, at_session_no: int, by: int = 1) -> dict:
    """Move every session at or after `at_session_no` by `by`. Returns {old: new}.

    A curriculum is an ORDERED list — session 1 is taught first — so inserting a row in
    the middle has to push the rest down, exactly as it would in the sheet. The first
    version of the insert button instead gave a new row the next FREE number, which put
    "35" at the top of a 34-session course and read as nonsense.

    (course, session_no) is the primary key, so a plain `session_no = session_no + 1`
    over the whole range collides the moment it rewrites 5 to 6 while 6 still exists.
    The rows are therefore PARKED far outside the real range first and brought back in
    a second pass: both statements move a set of rows into a range that provably holds
    no other row, so neither can collide and neither depends on the order SQLite
    happens to visit rows in (`UPDATE ... ORDER BY` is not available here).

    TWO statements, not one per row. The first version walked the rows highest-first,
    which is correct but costs one round trip PER SESSION — 34 of them, all inside a
    single interactive transaction held open across the whole walk. Against Turso that
    is not merely slow: the stream backing an interactive transaction is reclaimed
    while the walk is still going, and the insert dies partway with

        Hrana: `api error: `status=404 Not Found, body={"error":"stream not found: …"}``

    Every other write in this module is one statement on a short-lived connection —
    that is the module's whole contract (see the header) — and this is now the same
    shape: a fixed two statements, whatever the course's length.
    """
    rows = _query("SELECT session_no FROM curriculum WHERE course=? AND session_no>=? "
                  "ORDER BY session_no", (course, int(at_session_no)))
    nums = [int(r["session_no"]) for r in rows]
    if not nums or not by:
        return {}
    mapping = {n: n + by for n in nums}
    at, by = int(at_session_no), int(by)
    conn = _connect()
    try:
        cur = conn.cursor()
        # Park: shifted value, pushed below every real session number. A session number
        # is >= 1, so nothing else can be sitting in the parked range.
        cur.execute("UPDATE curriculum SET session_no = session_no + ? - ? "
                    "WHERE course=? AND session_no >= ?",
                    (by, _PARK, course, at))
        # Bring back. The rows still in place are all < at, and every parked row lands
        # at >= at + by, so this pass cannot collide either.
        cur.execute("UPDATE curriculum SET session_no = session_no + ?, updated_at=? "
                    "WHERE course=? AND session_no <= ?",
                    (_PARK, _now(), course, -(_PARK // 2)))
        conn.commit()
    finally:
        _close(conn)
    return mapping


def curriculum_delete(course: str, session_no: int) -> bool:
    try:
        _exec("DELETE FROM curriculum WHERE course=? AND session_no=?",
              (course, int(session_no)))
        return True
    except Exception:
        return False


def delete_course(course: str, *, detach_teams: bool = True,
                  all_teams: list[dict] | None = None) -> dict:
    """Remove a course the owner no longer needs, and report exactly what went.

    WHAT GOES: the curriculum rows (which ARE the course), its length budgets, its
    ownership record, and its attachment to any team.

    WHAT STAYS, deliberately: the RUN HISTORY. A finished document records what was
    generated, for which course, under which session number, at what cost — and deleting
    that record would not remove the document, it would only make the instance lie about
    having produced it. Every other renumbering path in this file keeps history for the
    same reason. The docs remain downloadable and the cost roll-ups stay correct.

    The extracted DECKS are the caller's to remove (they live on disk, which this module
    does not touch) — and now that the store is scoped by course they simply go with it:
    pptx_ingest.drop_course_decks(course). There used to be an `orphan_sessions`
    calculation here — "the session numbers no remaining course claims" — because one
    directory held every course's decks and a number was all there was to go on. It was
    wrong in both directions, keeping a deleted course's deck whenever another course
    happened to share the session number.
    """
    course = (course or "").strip()
    if not course:
        return {"course": course, "sessions": 0, "teams_detached": []}
    n_sessions = len(curriculum(course))

    detached = []
    if detach_teams:
        # teams() is three queries; the caller has already asked (it has to, to decide
        # whether this course is shared at all), so it passes the answer in.
        for t in (teams() if all_teams is None else all_teams):
            if course in (t.get("courses") or []):
                # Repoints the team's legacy primary course column too — see
                # team_remove_course, which is where that has to happen for every caller.
                team_remove_course(t["id"], course)
                detached.append({"id": t["id"], "name": t["name"]})

    # EVERY per-course table. A new one that is not listed here leaks a row per deleted
    # course — which is exactly how the orphaned team_courses rows accumulated.
    for sql in ("DELETE FROM curriculum WHERE course=?",
                "DELETE FROM course_settings WHERE course=?",
                "DELETE FROM course_profiles WHERE course=?",
                "DELETE FROM course_skills WHERE course=?",
                "DELETE FROM course_prereqs WHERE course=?",
                # …and where this course was somebody ELSE's prerequisite. A link to a
                # course that no longer exists would silently assume knowledge from
                # decks that are gone.
                "DELETE FROM course_prereqs WHERE prereq=?",
                "DELETE FROM course_owners WHERE course=?"):
        try:
            _exec(sql, (course,))
        except Exception as e:
            print(f"[db] delete_course({course!r}): {sql.split()[2]} — {e!r}")

    return {"course": course, "sessions": n_sessions, "teams_detached": detached}


def curriculum_mark_deck(course: str, session_no: int, deck_hash: str,
                         status: str = "extracted") -> bool:
    """Record that this row's deck has been extracted at this content hash."""
    try:
        _exec("UPDATE curriculum SET deck_hash=?, deck_status=?, updated_at=? "
              "WHERE course=? AND session_no=?",
              (deck_hash or "", status, _now(), course, int(session_no)))
        return True
    except Exception:
        return False


def curriculum_import(course: str, rows: list[dict], *, replace: bool = False) -> dict:
    """Load a sheet's rows into the table. Returns {added, updated, removed, kept}.

    MERGE by default: existing rows are updated in place, so a re-import refreshes the
    curriculum without discarding a deck already extracted (the link is unchanged, so
    its hash survives and nothing is re-downloaded). `replace=True` also deletes
    sessions absent from the import — the destructive option, never the default,
    because in-app edits are the thing most likely to be lost.
    """
    before = {r["session_no"]: r for r in curriculum(course)}
    seen, added, updated = set(), 0, 0
    for r in rows:
        try:
            no = int(float(r.get("session_no")))
        except (TypeError, ValueError):
            continue
        seen.add(no)
        (updated := updated + 1) if no in before else (added := added + 1)
        curriculum_upsert(course, no, topic=r.get("topic", ""),
                          session_name=r.get("session_name", ""),
                          key_takeaways=r.get("key_takeaways") or [],
                          ppt_link=r.get("ppt_link", ""))
    removed = 0
    if replace:
        for no in before:
            if no not in seen:
                curriculum_delete(course, no)
                removed += 1
    return {"added": added, "updated": updated, "removed": removed,
            "kept": len(before) - removed}


def unfinished_guided(user_email: str | None, limit: int = 5) -> list[dict]:
    """This user's guided runs that were never finished, newest first.

    The browser remembers the run id in localStorage, which is where the resume offer
    came from — so the offer only existed in the ONE browser that started the run. Sign
    in from a laptop after starting on a desktop, clear site data, or use a private
    window, and a run with several paid-for chunks in it was unreachable even though the
    server still held its checkpoint. The checkpoints carry user_email, so the server
    can answer "what did I leave unfinished?" directly.

    Returns id, session_no, title, status, how many chunks are done, and when it was
    last touched — enough to describe the run without loading its whole state.
    """
    try:
        rows = _query(
            "SELECT id, session_no, updated, state_json FROM guided_sessions "
            "WHERE user_email = ? ORDER BY updated DESC LIMIT ?",
            (user_email or "", int(limit) * 4))
    except Exception:
        return []
    out = []
    for r in rows:
        try:
            st = json.loads(r.get("state_json") or "null") or {}
        except Exception:
            continue
        # A finished, dead or explicitly discarded run is not resumable — the first two
        # are history (the runs table carries them) and the third is a decision the user
        # already made, which must survive a page reload.
        if st.get("status") in ("done", "error", "discarded"):
            continue
        out.append({
            "guided_id": r.get("id"),
            "session_no": r.get("session_no"),
            "title": st.get("session_title") or st.get("labels", [None])[0],
            "status": st.get("status"),
            "chunks_done": len(st.get("chunks") or []),
            "total": st.get("total"),
            "updated": r.get("updated"),
        })
        if len(out) >= limit:
            break
    return out


def discard_guided(gid: str) -> bool:
    """Mark an unfinished guided run as discarded, so it is never offered again.

    Discard used to be a purely local act — the browser forgot the id and stopped
    showing it. The checkpoint stayed on the server, so the very next page load asked
    the server for unfinished runs and got it straight back: the prompt returned again
    and again with no way to make it stop. Discarding is a decision about the RUN, not
    about one browser's memory of it, so it has to be recorded where the run lives.

    The row is kept (marked, not deleted) until the normal purge window clears it, so a
    mis-click is recoverable by an admin from the database rather than gone instantly.
    """
    snap = load_guided(gid)
    if snap is None:
        return False
    snap["status"] = "discarded"
    try:
        _exec("UPDATE guided_sessions SET state_json=?, updated=? WHERE id=?",
              (json.dumps(snap, ensure_ascii=False), _now(), gid))
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
        # RECURSIVE. Decks are filed per course now — decks/<course>/session_NN.json —
        # so a flat glob found nothing and the cloud mirror silently stopped backing up
        # any deck at all. Manifests go too: they are what tells a restored instance a
        # deck is already extracted, so without them every deck is re-downloaded.
        out += [f"decks/{f.relative_to(decks).as_posix()}"
                for f in sorted(decks.rglob("*.json"))]
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
    return _kb_mirror(name)


def kb_put_rel(rel: str) -> bool:
    """Persist ONE KB file BY PATH, immediately — decks and their manifests included.

    kb_put() is allow-listed to the top-level files; a deck is not one of them, and its
    only route to the cloud DB was kb_backup(), which runs once AFTER a whole ingest
    finishes. On an ephemeral host that made a long read all-or-nothing: an external
    prerequisite of 29 links that lost its instance at link 9 — a free-plan spin-down, a
    redeploy, an OOM, anything that shows up in the browser as a 502 — had written nine
    decks to a disk that was about to be wiped, and none of them to the DB. The
    prerequisite row itself commits immediately, so what came back up was a prerequisite
    attached with zero decks behind it: "1 course(s): 0 session(s), 0 slides".

    A deck is ~50 KB, so mirroring each one as it lands costs a single small write per
    link, against kb_backup()'s re-upload of the entire 1.5 MB store. Best effort —
    never raises, because a storage hiccup must not fail a read that succeeded.
    """
    rel = (rel or "").lstrip("/")
    if not _use_turso():
        return False
    if rel not in _KB_TOP_FILES and not rel.startswith("decks/"):
        return False
    return _kb_mirror(rel)


def _kb_mirror(rel: str) -> bool:
    try:
        content = (config.KB_DIR / rel).read_text(encoding="utf-8")
    except Exception:
        return False
    try:
        _exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
              (rel, content, _now()))
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


def kb_forget(rel_path: str) -> bool:
    """Drop one KB file from the DB mirror. Best effort.

    Deleting a session removes its deck from disk; without this the mirror would hand
    it back on the next restart, on a number that now belongs to a different session.
    """
    try:
        _exec("DELETE FROM kb_files WHERE path=?", ((rel_path or "").lstrip("/"),))
        return True
    except Exception:
        return False


def kb_forget_many(rel_paths) -> int:
    """Drop MANY KB files from the mirror in ONE statement. Returns how many were named.

    kb_forget one path at a time is fine on a local file and ruinous on the cloud
    database: every _exec opens its own connection and makes its own network round-trip,
    so deleting a 34-session course fired 32 of them back to back inside a single HTTP
    request — enough, with everything else that request does, for the platform to time it
    out and answer 503.
    """
    paths = sorted({(p or "").lstrip("/") for p in (rel_paths or []) if (p or "").strip()})
    if not paths:
        return 0
    try:
        _exec("DELETE FROM kb_files WHERE path IN (" + ",".join("?" for _ in paths) + ")",
              tuple(paths))
        return len(paths)
    except Exception:
        return 0


def kb_forget_prefix(prefix: str) -> int:
    """Drop every mirrored KB file under `prefix`, in ONE statement.

    For deleting a course: its decks are one folder, so the mirror rows are one prefix.
    Naming them individually would be a round-trip per deck — the shape that made the
    course delete time out and answer 503.
    """
    prefix = (prefix or "").lstrip("/")
    if not prefix:
        return 0
    # ONE statement, and the row count comes from the cursor rather than a SELECT before
    # it: counting first would make the common case two round-trips to save nothing.
    conn = _connect()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM kb_files WHERE path LIKE ?", (prefix + "%",))
        n = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        conn.commit()
        return n
    except Exception as e:
        print(f"[db] kb_forget_prefix({prefix!r}) failed: {e!r}")
        return 0
    finally:
        _close(conn)


def kb_rename_decks(course: str, mapping: dict) -> int:
    """Re-point the stored copies of decks that were renumbered. Returns rows moved.

    The targeted alternative to kb_backup() for the one case that needs it. Renumbering
    a curriculum renames deck files on disk, and the DB mirror they are restored from
    has to follow, or the next restart hands the old numbering back. kb_backup() would
    do that — but it re-uploads EVERY KB file through a fresh connection each, roughly
    thirty round trips to the cloud database to fix a handful of paths, which is a
    visible pause on a button press.

    Only the `path` column changes; the content is already correct. It goes through
    temporary paths for the same reason the files themselves do: a chain like 3->4,
    4->5 would otherwise have the first row collide with the second on a PRIMARY KEY
    that has not moved yet.

    THREE statements, not three per deck. The first version looped — one park per deck,
    then a delete and an update per deck, so renumbering a 34-session course sent about
    seventy statements down one interactive transaction. That is the same shape that
    broke curriculum_shift_from against Turso ("stream not found", the transaction's
    stream reclaimed mid-walk); here it was worse to find, because the caller wraps this
    in a try/except that only prints, so it failed silently and the cloud mirror simply
    stopped following the curriculum. Set-based now: park all, clear all targets,
    unpark all.
    """
    from . import pptx_ingest       # for the course-scoped KB path
    pairs = [(pptx_ingest.kb_rel(course, o), pptx_ingest.kb_rel(course, n))
             for o, n in (mapping or {}).items() if int(o) != int(n)]
    if not pairs:
        return 0
    conn = _connect()
    try:
        cur = conn.cursor()
        ts = _now()
        olds = [o for o, _ in pairs]
        news = [n for _, n in pairs]
        q = ",".join("?" * len(pairs))
        # Park every row being moved, out of the way of the paths about to be taken.
        cur.execute(f"UPDATE kb_files SET path='__moving__' || path "
                    f"WHERE path IN ({q})", tuple(olds))
        # Clear whatever is sitting on the destinations (a deck the curriculum no
        # longer lists, say) so the unpark cannot collide.
        cur.execute(f"DELETE FROM kb_files WHERE path IN ({q})", tuple(news))
        # Unpark: one CASE carries the whole old->new mapping in a single statement.
        cases = " ".join("WHEN ? THEN ?" for _ in pairs)
        args = []
        for old, new in pairs:
            args += [f"__moving__{old}", new]
        args.append(ts)
        args += [f"__moving__{o}" for o in olds]
        cur.execute(f"UPDATE kb_files SET path = CASE path {cases} END, updated_at=? "
                    f"WHERE path IN ({q})", tuple(args))
        moved = len(pairs)
        conn.commit()
        return moved
    except Exception as e:
        print(f"[db] kb_rename_decks failed: {e!r}")
        return 0
    finally:
        _close(conn)


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


def kb_get(rel: str) -> str | None:
    """One KB file's mirrored content, straight from the DB. None if it is not there.

    THE DISK IS NOT THE SOURCE OF TRUTH on an ephemeral host, and code that assumes it
    is will be wrong in exactly the window that matters. kb_restore() brings the mirror
    back at start-up, but it runs on a BACKGROUND thread — the port answers first, on
    purpose, so a free instance waking up is not also slow — so a request arriving early
    sees a disk that is still empty. A deck that exists in the DB and not yet on disk
    then reads as a deck that does not exist, and the caller re-does work it had already
    paid for. This lets a caller ask the question the right way round.
    """
    rel = (rel or "").lstrip("/")
    if not rel or not _use_turso():
        return None
    try:
        rows = _query("SELECT content FROM kb_files WHERE path = ?", (rel,))
    except Exception:
        return None
    return rows[0]["content"] if rows else None


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
