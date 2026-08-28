"""FastAPI backend for the TR Doc Generator React frontend.

Run:
    cd "/home/nxtwave/Desktop/TR Doc Generator"
    python server.py                 # serves the API on http://localhost:8000

Endpoints:
    GET  /api/status                 -> key status / saved links / settings / policy
    GET  /api/template-guide         -> markdown of the required sheet templates
    POST /api/sync                    -> validate + sync the curriculum sheet (changelog/sessions)
    GET  /api/sessions               -> synced session list
    POST /api/guided/start            -> start a guided run (chunk -> review -> finalize)
    GET  /api/guided/{gid}           -> poll guided state
    POST /api/guided/{gid}/finalize   -> assemble, grade and render the approved doc
    GET  /api/jobs/{job_id}          -> poll job status/logs/result
    GET  /api/download/{session_no}  -> download the generated .docx
"""
from __future__ import annotations
import json
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src import (config, sheets, sync, course_loader, pipeline, pptx_ingest,
                 context_builder, generator, docx_writer, app_settings, auth, db,
                 outputs, llm, gslides)
from src import prereqs as prereqs_mod

app = FastAPI(title="TR Doc Generator API")

# Allow the Vite dev server (any localhost port) to call us during `npm run dev`.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"], allow_headers=["*"],
)

@app.exception_handler(Exception)
async def _unhandled(request, exc):
    """Say WHAT failed, and leave a traceback in the logs.

    An unhandled error used to reach the browser as a bare 500, which the client
    rendered as "Request failed (HTTP 500). Is the backend running?" — a message that
    names neither the request nor the reason. Twice now that has meant a defect could
    only be guessed at from a screenshot. The traceback goes to the server log (where
    the platform keeps it) and the path plus the exception type come back to the
    caller, so the next failure can be read instead of reconstructed.
    """
    import traceback
    path = getattr(getattr(request, "url", None), "path", "?")
    traceback.print_exc()
    print(f"[error] {request.method} {path} -> {type(exc).__name__}: {exc}", flush=True)
    return Response(
        content=json.dumps({"detail": {
            "message": f"{type(exc).__name__} on {path}: {exc}",
            "path": path, "kind": "server_error"}}),
        status_code=500, media_type="application/json")


JOBS: dict[str, dict] = {}
GUIDED: dict[str, dict] = {}
_lock = threading.Lock()

# Load .env FIRST. src.db chooses its backend (local SQLite vs cloud Turso) from
# TURSO_DATABASE_URL on every call, so if .env were loaded lazily (by the first
# config lookup a request happens to make) the backend would flip mid-process: the
# schema created here would land in one database and later reads/writes in the other.
config.load_env()

# ---------------------------------------------------------------------------- #
# STARTUP — get the port answering FIRST, then do the housekeeping.
#
# All of this used to run at IMPORT time, so the process did not bind its port until
# every step had finished. On the deployed (free, ephemeral) host that meant a cold
# start paid, before serving anything: the schema round-trips, a 1.3 MB knowledge-base
# restore pulled back from the cloud database, a learned-rule sweep and a checkpoint
# purge. Every one of those is a network call to a database that may itself be cold.
# While it ran, the health check had nothing to talk to and the platform returned 503.
#
# Worse, `db.init()` was not wrapped: a single hiccup there raised through the import
# and the service never started at all — a permanent 503 until the next deploy.
#
# So: schema first (nothing can query without it) but never fatal, and everything else
# on a background thread. /api/auth/config — the health check — touches no database at
# all, so the instance is answerable within milliseconds of the port opening.
# ---------------------------------------------------------------------------- #
STARTUP: dict = {"ready": False, "steps": [], "error": None}

try:
    db.init()   # create the schema + import the legacy JSON log
except Exception as _e:
    # A boot that cannot reach the database must still serve: the UI can render, the
    # health check passes, and each request retries on its own.
    STARTUP["error"] = f"schema init failed: {_e}"
    print(f"[startup] WARNING — {STARTUP['error']}")


def _migrate_deck_layout() -> int:
    """Course-scope the extracted-deck store, once. Returns how many decks moved.

    Announces what it did in full: an unattributable deck is parked rather than guessed
    at, and an admin has to be told which ones so they can be re-fetched under the right
    course. Repointing the cloud mirror is a single kb_backup — it re-snapshots the KB
    and drops rows whose path no longer exists, which is exactly what a layout change
    needs, and it is a no-op on a local disk.
    """
    res = pptx_ingest.migrate_legacy_decks()
    moved, loose = res.get("moved") or {}, res.get("unassigned") or []
    if moved:
        by_course: dict = {}
        for no, course in moved.items():
            by_course.setdefault(course, []).append(no)
        for course, nos in by_course.items():
            print(f"[startup] decks: moved {len(nos)} deck(s) under {course!r} "
                  f"(sessions {', '.join(str(n) for n in sorted(nos))})")
    if loose:
        print(f"[startup] ⚠ decks: {len(loose)} deck(s) could not be attributed to one "
              f"course and were parked in decks/_unassigned — sessions "
              f"{', '.join(str(n) for n in loose)}. Re-fetch them from the course they "
              f"belong to; nothing reads them where they are.")
    if moved or loose:
        try:
            db.kb_backup()          # repoint the mirror at the new paths, in one pass
        except Exception as e:
            print(f"[startup] decks: mirror not repointed ({e!r}) — it will catch up on "
                  f"the next sync.")
    return len(moved) + len(loose)


def _startup_housekeeping() -> None:
    """The slow, optional work — off the critical path, best effort, never fatal."""
    import time as _t
    for label, fn in (
            # On an ephemeral host the disk is wiped on every restart, so bring the
            # previously-synced knowledge base back from the DB. No-op locally.
            ("knowledge-base restore", db.kb_restore),
            # Move any decks still in the pre-course-scoping flat layout into the folder
            # of the course that owns them. Runs after the restore, because on an
            # ephemeral host the files it has to move arrive with that restore. No-op
            # once done; see pptx_ingest.migrate_legacy_decks for how ownership is
            # inferred and what happens when it cannot be.
            ("deck-layout migration", _migrate_deck_layout),
            # Retire learned rules a deterministic gate now enforces, or the judge
            # re-adjudicates them from prose and can fail a compliant doc.
            ("learned-rule retirement", lambda: __import__(
                "src.learning", fromlist=["learning"]).retire_gated()),
            # Drop any stored rule that carries no instruction — a distil reply's SCOPE
            # line saved as if it were the rule. One is in the live store; it goes into
            # every generation for its course carrying reviewer-level precedence and
            # says nothing.
            ("contentless-rule sweep", lambda: __import__(
                "src.learning", fromlist=["learning"]).drop_contentless()),
            # Guided checkpoints nobody can resume any more.
            ("guided-checkpoint purge", lambda: db.purge_guided(72)),
    ):
        t0 = _t.time()
        try:
            n = fn() or 0
            took = _t.time() - t0
            STARTUP["steps"].append({"step": label, "count": n, "seconds": round(took, 2)})
            if n:
                print(f"[startup] {label}: {n} in {took:.1f}s")
        except Exception as e:
            STARTUP["steps"].append({"step": label, "error": str(e)})
            print(f"[startup] {label} skipped: {e}")
    STARTUP["ready"] = True


threading.Thread(target=_startup_housekeeping, daemon=True).start()


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class CurriculumRow(BaseModel):
    session_no: int
    # Per-session budget overrides. None = inherit the course's (which itself falls
    # back to the harness default), so a row only carries a number when it needs to
    # differ from its neighbours.
    max_pages: int | None = None
    max_slides: int | None = None
    topic: str | None = ""
    session_name: str | None = ""
    key_takeaways: list[str] | str | None = None
    # None = leave the existing link (and its extracted deck) alone. A different link
    # marks that row's deck as pending; the same link changes nothing, so saving a row
    # never re-downloads a deck.
    ppt_link: str | None = None


class CurriculumSaveBody(BaseModel):
    rows: list[CurriculumRow]
    # Which course these rows belong to. Sent explicitly so two people working on
    # different courses on the same instance cannot write into each other's.
    course: str | None = None


class CurriculumInsertBody(BaseModel):
    # The number the NEW session should take. Everything from here on moves down one,
    # because a curriculum is an ordered list — see db.curriculum_shift_from.
    at_session_no: int
    course: str | None = None
    topic: str | None = ""
    session_name: str | None = ""
    key_takeaways: list[str] | str | None = None
    ppt_link: str | None = None


class SkillBody(BaseModel):
    course: str | None = None
    text: str
    kind: str = "style"
    check: dict | None = None


class SkillFromRequirementsBody(BaseModel):
    course: str | None = None
    requirements: str


class PrereqBody(BaseModel):
    course: str | None = None
    prereq: str


class ExternalPrereqBody(BaseModel):
    course: str | None = None
    name: str
    # One Google Slides link per session of the prerequisite, in order. There is no
    # curriculum for a course taught elsewhere — the decks ARE what is known about it.
    links: list[str] = []


class SkillImportBody(BaseModel):
    course: str | None = None
    from_course: str


class CourseProfileBody(BaseModel):
    course: str | None = None
    # A sparse tree of overrides over the harness. What may be set is a closed whitelist
    # in src/profiles.py — see validate() there for why it is not free-form.
    profile: dict = {}


class CourseSettingsBody(BaseModel):
    course: str | None = None
    max_pages: int | None = None
    max_slides: int | None = None


class SessionSettingsBody(BaseModel):
    session_no: int
    course: str | None = None
    max_pages: int | None = None
    max_slides: int | None = None


class IngestBody(BaseModel):
    course: str | None = None
    # Re-fetch decks whose link has not changed. The only way to pick up an edit made to
    # the SLIDES behind an unchanged link, since Google's export endpoint exposes no
    # ETag/Last-Modified to ask cheaply.
    force: bool = False
    sessions: list[int] | None = None


class SyncBody(BaseModel):
    course_link: str | None = None
    # One sheet now carries both the curriculum and each session's deck link (the
    # "PPT Links" column), so there is no second link to send. Accepted and ignored
    # so an older client that still posts it does not get a 422.
    details_link: str | None = None
    course_type: str | None = None         # "semester" | "interview"
    course_name: str | None = None         # grouping label for runs/teams


# use_judge / enforce_time are kept on the wire for backwards compatibility with any
# older client, but they are no longer honoured when the harness pins them
# (gates.always_run_llm_judge, constraints.recording.always_enforced) — pipeline forces
# both on. The app no longer sends them.
class EvalSetsBody(BaseModel):
    session_no: int
    use_llm: bool = True
    enforce_time: bool = True


class GuidedStartBody(BaseModel):
    session_no: int
    use_judge: bool = True
    enforce_time: bool = True
    # The workspace this run belongs to. Sent explicitly rather than guessed from the
    # user's memberships, so a doc made in a TEAM workspace is stamped with that team
    # and is visible to every member — including one added months later.
    team_id: int | None = None
    course: str | None = None


class ApproveChunkBody(BaseModel):
    index: int
    approved: bool = True     # false un-ticks it, for a reviewer who changes their mind


class SplitSlideBody(BaseModel):
    index: int               # which chunk
    slide_n: int             # which slide in it, by the number shown to the reviewer


class AskBody(BaseModel):
    """A question about one section, in the reviewer's own words.

    Deliberately unconstrained. There is no question type, no intent field and no menu:
    whatever the reviewer wants to understand about the section is a valid question, and
    anything that narrowed it would be a guess about what they are allowed to wonder.
    """
    # The section being asked about, or -1 for the document as a whole. The two are
    # different questions with different evidence behind them: "why is it phrased like
    # that" is answered by one section, "why is this in section 3 and not section 5"
    # cannot be answered from section 3 at all.
    index: int
    question: str
    # Web search costs a little latency and is not always relevant — a question about
    # why a section is short is settled by the budget, not by GeeksforGeeks. On by
    # default because the questions that matter most are usually the factual ones.
    use_web: bool = True


class RegenerateBody(BaseModel):
    index: int
    reason: str | None = None
    # Carry this note into every chunk AFTER this one as well. A reviewer note is very
    # often about the whole document — "stop restating the takeaway at the top", "use
    # plainer language" — and having to retype it into six chunks in turn, waiting for
    # each, is the same instruction six times.
    apply_to_following: bool = False


class LoginBody(BaseModel):
    credential: str


class TeamCreateBody(BaseModel):
    name: str
    course: str | None = None
    # WHO RUNS THE TEAM. Required: a team with no owner is a team whose membership only
    # an admin can ever change, which is the bottleneck this field exists to remove.
    owner: str | None = None


class MemberBody(BaseModel):
    email: str


class CourseBody(BaseModel):
    course: str


class TeamNameBody(BaseModel):
    name: str


class FeedbackBody(BaseModel):
    session_no: int
    reason: str              # a plain-language correction; distilled into a durable rule
    # WHICH COURSE this correction is about. Optional so older clients still work, but
    # the caller should always send it: without it the rule is filed against the
    # instance-wide active course, which is whoever selected one last.
    course: str | None = None


class GdocBody(BaseModel):
    access_token: str        # short-lived Google Drive token from the frontend (GIS)
    # Optional but preferred: identify the OUTPUT exactly rather than letting the server
    # re-derive its filename from whatever course is synced right now (src/outputs.py).
    run_id: str | None = None
    name: str | None = None


# --------------------------------------------------------------------------- #
# auth — Google Sign-In restricted to the org domain
# --------------------------------------------------------------------------- #
def current_user(authorization: str = Header(default="")) -> dict:
    """FastAPI dependency: resolve the signed-in user from the Bearer token.
    Set AUTH_DISABLED=1 in the env to bypass for LOCAL DEV ONLY (never deploy
    with it)."""
    if config.auth_disabled():
        dom = config.auth().get("allowed_domain", "nxtwave.co.in")
        return {"email": f"dev@{dom}", "name": "Dev (auth disabled)",
                "picture": None, "is_admin": True}
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    try:
        user = auth.verify_credential(token)
    except auth.AuthUnavailable as e:
        # 503, NOT 401. A 401 makes the client discard the session and bounce to the
        # login screen — the worst possible response to "we could not reach Google for
        # a moment", since it logs the user out over someone else's network blip and
        # sends them to re-authenticate against the very thing that is unreachable.
        raise HTTPException(status_code=503, detail={"message": str(e), "kind": "auth_unreachable"})
    except auth.AuthError as e:
        raise HTTPException(status_code=401, detail={"message": str(e)})
    try:
        db.upsert_user(user["email"], user.get("name"), user.get("is_admin", False))
    except Exception:
        pass
    return user


def require_admin(user: dict = Depends(current_user)) -> dict:
    if not user.get("is_admin"):
        raise HTTPException(status_code=403, detail={"message": "Admin access only."})
    return user


@app.get("/api/auth/config")
def auth_config():
    """Public: what the frontend needs to start Google Sign-In."""
    return {
        "client_id": config.google_client_id(),
        "allowed_domain": config.auth().get("allowed_domain"),
        "configured": config.google_client_id() is not None,
        "auth_disabled": config.auth_disabled(),
    }


@app.get("/api/health")
def health():
    """Is the instance up, and has its housekeeping finished?

    Deliberately touches NOTHING: no database, no disk, no auth. A health check that
    queries a cold cloud database is a health check that fails when the database is
    slow — which is exactly when you least want the platform to take the instance out
    of service. `ready` tells you whether the background restore has finished; the
    app serves either way, so this is for diagnosis, not gating.
    """
    return {"ok": True, "ready": STARTUP["ready"],
            "steps": STARTUP["steps"], "error": STARTUP["error"]}


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    try:
        return auth.verify_credential(body.credential)
    except auth.AuthUnavailable as e:
        raise HTTPException(status_code=503, detail={"message": str(e), "kind": "auth_unreachable"})
    except auth.AuthError as e:
        raise HTTPException(status_code=401, detail={"message": str(e)})


@app.get("/api/auth/me")
def auth_me(user: dict = Depends(current_user)):
    """Resolve the current user from the stored token (used to restore a session
    on page reload)."""
    return user


# --------------------------------------------------------------------------- #
# status / guide
# --------------------------------------------------------------------------- #
@app.get("/api/status")
def status():
    c = sync.last_link()
    return {
        # Which provider/model/harness version is running is deliberately NOT reported:
        # it is an internal detail that changes over time and the UI showed it with no
        # action attached. `key_ok` stays because the UI gates Generate on it.
        "key_ok": config.api_key() is not None,
        "saved_links": {"course": c},
        "settings": app_settings.load(),
        # Generation policy the app shows instead of offering as checkboxes: the LLM
        # quality check and the 40-minute budget always run, and every doc is capped at
        # constraints.pages.max pages.
        "policy": {
            "judge_always_on": pipeline.judge_always_on(),
            "time_always_enforced": pipeline.time_always_enforced(),
            "max_minutes": config.harness()["constraints"]["recording"]["max_minutes"],
            "max_pages": config.harness()["constraints"]["pages"]["max"],
            "target_pages": config.harness()["constraints"]["pages"]["target"],
        },
    }


@app.get("/api/template-guide")
def template_guide():
    return {"markdown": sheets.guide_text()}


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #
def _run_sync(job_id: str, course_link: str | None, course: str | None = None):
    def on_event(msg: str):
        with _lock:
            JOBS[job_id]["logs"].append(msg)
    try:
        # The course is passed EXPLICITLY. sync() otherwise falls back to
        # app_settings.course_name(), one instance-wide setting, so an import would land
        # in whichever course was selected last — by anybody — and the caller's own
        # authorisation check would have been about a different course entirely.
        res = sync.sync(course_link, course=course, verbose=True, on_event=on_event)
        with _lock:
            JOBS[job_id].update(status="done", result={
                "sessions": _session_list(course),
                "changelog": res.changelog,
                "errors": res.errors,
                "extraction_warnings": res.extraction_warnings,
                "counts": {"sessions": res.sessions,
                           "ingested": res.decks_ingested, "cached": res.decks_cached},
            })
    except sheets.TemplateError as e:
        with _lock:
            JOBS[job_id].update(status="error", error=str(e), error_kind="template")
    except Exception as e:
        with _lock:
            JOBS[job_id].update(status="error", error=str(e), error_kind="read")


@app.post("/api/sync")
def do_sync(body: SyncBody, user: dict = Depends(current_user)):
    # An import REPLACES a curriculum, so it is gated exactly like editing one: you may
    # not re-import over a course somebody else created. Checked before anything is
    # written, or a refused sync would still have moved the instance's active course.
    course = _require_course(user, body.course_name)
    _claim_course(user, course)
    # Persist the course type + course name chosen at connect time so generation
    # (context_builder) can use them later.
    app_settings.save(course_type=body.course_type, course_name=body.course_name)
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None,
                        "error": None, "error_kind": None}
    threading.Thread(target=_run_sync,
                     args=(job_id, body.course_link, course), daemon=True).start()
    return {"job_id": job_id}


# --------------------------------------------------------------------------- #
# CURRICULUM — the agent's own copy of the course, edited in the app.
#
# The sheet is an import format now, not a dependency. Everything below reads and
# writes the `curriculum` table; the sheet is touched only when the user asks for an
# import. Crucially, saving a row does NOT re-fetch its deck: a deck is downloaded
# once per link, because Google's export endpoint gives no way to ask whether it
# changed without downloading the whole file (~4.7 MB, ~3.4 s each).
# --------------------------------------------------------------------------- #
def _default_course(user: dict) -> str:
    """Which course to open for a request that did not name one.

    `app_settings.course_name()` is a single INSTANCE-WIDE setting — whoever selected a
    course last set it for everybody — so it cannot be the answer on its own now that
    a course can belong to one person. Handing it back unchecked would 403 the app's
    own bootstrap for anyone who cannot open whatever the last person selected, which
    is a locked-out user rather than a scoped one.

    So the global default is used only if this person may actually have it; otherwise
    they land on one of their own courses, preferring one they created over one shared
    with them through a team.
    """
    email = user.get("email")
    is_admin = user.get("is_admin", False)
    active = (app_settings.course_name() or "").strip()
    if active and db.can_use_course(email, active, is_admin=is_admin):
        # …and only if it is a course that EXISTS. course_name() falls back to a
        # hard-coded legacy default, so after the active course is deleted this would
        # otherwise answer with a course name nobody on the instance has ever had. The
        # second half of the test keeps a course the caller has just created — claimed,
        # but with no curriculum rows yet — from being taken off them on a reload.
        owners = db.course_owners()
        if active in set(db.curriculum_session_counts()) \
           or owners.get(active) == (email or "").lower():
            return active
    theirs = db.courses_for_user(email, is_admin=is_admin)
    for pick in (lambda c: c.get("mine"), lambda c: c.get("shared"), lambda c: True):
        for c in theirs:
            if pick(c):
                return c["name"]
    # Nothing of their own yet. "default" is the same placeholder the app has always
    # used for an instance with no course selected, and the UI shows the import card.
    # Deliberately NOT `active or "default"`: handing back a course this person cannot
    # open would put its curriculum in their bootstrap reply — the very leak this is
    # here to close — with only the 403 on the next explicit request to catch it.
    return "default"


def _require_course(user: dict, course: str | None) -> str:
    """Resolve the course a request is about AND check the caller may have it.

    Filtering the course LIST was never enough on its own: every curriculum, settings,
    session and generation endpoint takes the course as a plain parameter, so a name
    typed into a URL reached another team's curriculum whatever the sidebar showed. The
    list narrows what you are offered; this decides what you are allowed.

    A name that does not exist yet is allowed through — that is how a course is created.
    """
    named = (course or "").strip()
    if not named:
        return _default_course(user)
    if db.can_use_course(user.get("email"), named,
                         is_admin=user.get("is_admin", False)):
        return named
    raise HTTPException(status_code=403, detail={"message":
        f"'{named}' was created by someone else and is not shared with a team you are "
        f"on, so you cannot open it. Ask its owner to add you to the team that owns it."})


def _claim_course(user: dict, course: str | None) -> None:
    """Record this user as the creator of `course`, if it has no creator yet.

    Called from the paths that BRING A COURSE INTO EXISTENCE — selecting a new name,
    saving or importing a curriculum for it. First claim wins, so re-saving somebody
    else's curriculum never transfers it.
    """
    try:
        db.claim_course((course or "").strip(), user.get("email"))
    except Exception as e:
        print(f"[courses] could not record the creator of {course!r}: {e!r}")


@app.get("/api/bootstrap")
def bootstrap(course: str | None = None, user: dict = Depends(current_user)):
    """Everything the app needs to draw itself, in ONE request.

    Opening the page used to fire eight — status, courses, workspaces, course-settings,
    curriculum, sessions, history, teams, resumable — each its own HTTP round-trip and
    each re-reading tables the others had just read (19 database queries between them,
    and on the cloud database every one of those is a network hop). Selecting a course
    then fired two more, the second only to learn a number the first already knew.

    Gathering them here lets the shared work happen once: the curriculum is read a
    single time and handed to everything that needs it, and the team list is fetched
    once instead of by three separate callers.
    """
    course = _require_course(user, course)
    rows = _curriculum_rows(course)
    from src import budgets as budget_rules
    email = user.get("email")
    all_teams = db.teams()
    mine = db.teams_for_user(email, all_teams)
    counts = db.curriculum_session_counts()
    known = set(counts)
    # Who created what, read ONCE and handed to both the course list and the workspace
    # split below — the two answers have to agree, and this is one query.
    owners = db.course_owners()
    # Read ONCE and used for both the course picker and the workspace split below, so the
    # two cannot disagree about which shelf a course sits on.
    _shelved = db.courses_for_user(email, is_admin=user.get("is_admin", False),
                                   all_teams=all_teams, counts=counts, owners=owners)
    return {
        "user": user,
        "status": {
            "key_ok": config.api_key() is not None,
            "saved_links": {"course": sync.last_link()},
            "settings": app_settings.load(),
            "policy": {
                "judge_always_on": pipeline.judge_always_on(),
                "time_always_enforced": pipeline.time_always_enforced(),
                "max_minutes": config.harness()["constraints"]["recording"]["max_minutes"],
                "max_pages": config.harness()["constraints"]["pages"]["max"],
                "target_pages": config.harness()["constraints"]["pages"]["target"],
            },
        },
        "course": course,
        "courses": _shelved,
        # WHICH SHELF each course sits on. The app has an individual workspace and one
        # per team, and they are not the same shelf: `individual` is what THIS person
        # created, each team's is what THAT team owns. Sending one pooled list let the
        # individual view show a team-mate's course (and, before ownership was recorded,
        # every course on the instance).
        "workspaces": {
            "individual": {
                # Same authority as the picker — see db.courses_for_user's `shelf`. A
                # course shared with one of this person's teams belongs to the team, and
                # is not also listed here.
                "courses": sorted(c["name"] for c in _shelved
                                  if c["shelf"] == "individual" and c["name"] in known),
            },
            "teams": [{"id": tm["id"], "name": tm["name"],
                       "courses": tm.get("courses") or [],
                       "members": tm.get("members") or [],
                       # WHO RUNS THIS TEAM, and whether that is the person asking. The
                       # UI offers the add/remove-member controls off `can_manage`, so
                       # the answer has to come from the server — the client cannot be
                       # the one deciding what it is allowed to do.
                       "owner_email": tm.get("owner_email"),
                       "can_manage": bool(user.get("is_admin")) or
                                     (tm.get("owner_email") or "") == (email or "").lower(),
                       "unknown_courses": [c for c in (tm.get("courses") or [])
                                           if c not in known]}
                      for tm in mine],
        },
        "curriculum": {
            "rows": rows,
            "imported_from": sync.last_link(),
            "pending": sum(1 for r in rows
                           if (r.get("ppt_link") or "") and not r["extracted"]),
        },
        "sessions": _session_list(course, rows),
        "budget": {"settings": db.course_settings(course) or {},
                   "effective": budget_rules.for_session(course),
                   "defaults": budget_rules.harness_defaults()},
        "resumable": db.unfinished_guided(email),
    }


@app.get("/api/workspaces")
def workspaces(user: dict = Depends(current_user)):
    """Where this person can work: on their own, or inside each of their teams.

    A team workspace is what makes a course shared — its curriculum and its whole
    history belong to the team, so somebody added to it next month opens the same
    dashboard and sees everything produced before they arrived.

    Each team reports the courses it owns AND whether any of them names a curriculum
    the agent does not hold: a team's course is matched by exact name, so 'Operating
    System' against a curriculum called 'Operating Systems' silently shows its members
    an empty workspace, and that is worth saying out loud rather than leaving as a
    mystery.
    """
    email = (user.get("email") or "").lower()
    all_teams = db.teams()
    counts = db.curriculum_session_counts()
    owners = db.course_owners()
    known = set(counts)
    out = []
    for t in db.teams_for_user(email, all_teams):
        courses = t.get("courses") or []
        out.append({
            "id": t["id"], "name": t["name"], "courses": courses,
            "members": t.get("members") or [],
            "owner_email": t.get("owner_email"),
            "can_manage": bool(user.get("is_admin"))
                          or (t.get("owner_email") or "") == email,
            "unknown_courses": [c for c in courses if c not in known],
        })
    # THE INDIVIDUAL SHELF IS THIS PERSON'S OWN COURSES — the ones they created that no
    # team of theirs owns. It used to be `sorted(known)`: every course on the instance,
    # for everybody, admin or not (both branches of that conditional returned the same
    # thing). So a new signee opened the app and found colleagues' courses in their
    # private workspace, switchable and editable.
    #
    # The rule comes from db.courses_for_user's `shelf`, so this and the course picker
    # cannot disagree about where a course lives.
    #
    # There used to be an admin branch here that listed EVERY course as individual. The
    # reasoning was that an admin's reach is instance-wide — but the individual workspace
    # is a PERSONAL SHELF, not a view of the instance, and the effect was that an admin
    # opened their own workspace and found every team's courses sitting in it. Being able
    # to reach every course is what the admin dashboard's All courses tab is for. An
    # admin's personal shelf is what an admin personally made, like everybody else's.
    shelved = db.courses_for_user(email, is_admin=user.get("is_admin", False),
                                  all_teams=all_teams, counts=counts, owners=owners)
    individual = sorted(c["name"] for c in shelved
                        if c["shelf"] == "individual" and c["name"] in known)
    return {
        "individual": {"courses": individual},
        "teams": out,
    }


def _run_team(user: dict, team_id: int | None, course: str | None) -> int | None:
    """Which team a run belongs to.

    The workspace the user is actually working in decides — but only if they are really
    on that team, since the id arrives from the client. Falling back to the course-based
    guess keeps runs started outside a team workspace attributed as they were before.
    """
    if team_id is not None:
        if any(t["id"] == team_id for t in db.teams_for_user(user.get("email"))):
            return team_id
    return db.team_for_user_course(user.get("email"), course)


class TeamCourseBody(BaseModel):
    course: str


@app.post("/api/teams/{team_id}/courses")
def team_add_course(team_id: int, body: TeamCourseBody,
                    user: dict = Depends(current_user)):
    """Attach a course to a team the user belongs to.

    Members can do this, not just admins: a course created inside a team workspace has
    to become the team's immediately, or the person who made it would be the only one
    who could see it — which is the whole failure this workspace model exists to end.
    """
    if not any(t["id"] == team_id for t in db.teams_for_user(user.get("email"))):
        raise HTTPException(status_code=403, detail={
            "message": "You are not a member of that team."})
    # …and only a course you may already open. Otherwise this endpoint is a way to take
    # somebody else's course: name it here and it is on your whole team's shelf.
    _require_course(user, body.course)
    ok = db.team_add_course(team_id, body.course)
    return {"ok": ok, "courses": db.team_course_list(team_id)}


@app.delete("/api/teams/{team_id}/courses")
def team_remove_course(team_id: int, course: str,
                       user: dict = Depends(current_user)):
    """Take a course off a team's shelf. Admin or the team's course owner.

    The counterpart of attaching one, and deliberately a NARROWER permission: any member
    may add a course (a course created inside a team workspace has to become the team's
    at once, or its maker is the only one who can see it), but removing one takes it off
    everybody's shelf, and that is a decision for whoever runs the team.

    The course itself is untouched — its curriculum, its history and its owner all stay.
    This only ends the sharing.
    """
    _require_team_manager(user, team_id)
    course = (course or "").strip()
    if not course:
        raise HTTPException(status_code=400, detail={"message": "Name the course."})
    ok = db.team_remove_course(team_id, course)
    return {"ok": ok, "courses": db.team_course_list(team_id)}


@app.get("/api/courses")
def list_courses(user: dict = Depends(current_user)):
    """Courses this person may work on — the team's shelf, not a text box.

    The course used to be typed by hand, which made it a private label: two people
    spelling it differently ended up with two separate curricula, and a course one
    person imported was invisible to everyone else. It is a shared thing now, so it is
    chosen from a list.
    """
    courses = db.courses_for_user(user.get("email"), is_admin=user.get("is_admin", False))
    return {"courses": courses, "active": app_settings.course_name()}


class SelectCourseBody(BaseModel):
    course: str
    course_type: str | None = None


@app.post("/api/courses/select")
def select_course(body: SelectCourseBody, user: dict = Depends(current_user)):
    """Make a course the active one and hand back everything the app needs to show it.

    Generation reads the active course from app_settings (one setting per instance), so
    switching is a write — two people driving DIFFERENT courses at the same moment on
    the same instance would still contend for it. Reading is already per-request
    (every curriculum endpoint takes an explicit course), so browsing another team's
    course never disturbs anyone; only starting a generation depends on this.
    """
    course = (body.course or "").strip()
    if not course:
        raise HTTPException(status_code=400, detail={"message": "No course given."})
    # A brand-new name is allowed — that is how a course is created — and claiming it
    # here is what makes it the creator's rather than everyone's. An EXISTING course
    # someone else created, and no team of this user's owns, is refused.
    #
    # The check used to be `allowed and course not in allowed`, and `allowed` was every
    # course on the instance for anyone not on a team, so in practice it refused nothing.
    _require_course(user, course)
    _claim_course(user, course)
    app_settings.save(course_name=course, course_type=body.course_type)
    return {"course": course, **_curriculum_reply(course),
            "imported_from": sync.last_link()}


def _require_course_owner(user: dict, course: str) -> str:
    """Resolve a course the caller is entitled to DELETE.

    Stricter than _require_course on purpose. Being able to open a course is not the
    same as being able to destroy it: a team-mate can work on a shared curriculum all
    day and must not be able to remove it, and an UNCLAIMED course (one imported before
    ownership was recorded) has no owner to authorise the deletion at all, so it is the
    admin's to remove.
    """
    course = (course or "").strip()
    if not course:
        raise HTTPException(status_code=400, detail={"message": "Name the course."})
    if user.get("is_admin"):
        return course
    owner = db.course_owner(course)
    if owner and owner == (user.get("email") or "").lower():
        return course
    if not owner:
        raise HTTPException(status_code=403, detail={"message":
            f"'{course}' has no recorded owner — it predates the agent keeping track of "
            f"who created a course — so only an admin can delete it."})
    raise HTTPException(status_code=403, detail={"message":
        f"'{course}' was created by {owner}. Only they or an admin can delete it; being "
        f"able to open it is not the same as being able to remove it."})


@app.delete("/api/courses")
def delete_course(course: str, detach_teams: bool = False,
                  user: dict = Depends(current_user)):
    """Delete a course the owner no longer needs.

    Two-step ON PURPOSE when the course is shared. A course attached to a team is not
    just its creator's any more — its curriculum is what the team opens and its history
    is what the team's shelf is gathered by — so deleting it out from under them takes a
    second, explicit request (`detach_teams=true`) rather than happening because somebody
    clicked once. The first call answers 409 and names the teams, which is what the UI
    puts in front of the user.

    RUN HISTORY SURVIVES. See db.delete_course: the finished documents stay downloadable
    and the cost roll-ups stay correct, because deleting the record would not un-generate
    the documents — it would only make the instance lie about having produced them.
    """
    course = _require_course_owner(user, course)
    # ONE teams() for the whole request. It is three queries, and this endpoint asked four
    # separate times — see the round-trip note on db.kb_forget_many: against the cloud
    # database this request was making sixty network hops, each on its own connection, and
    # the platform was timing it out with a 503 that carried no message at all.
    all_teams = db.teams()
    holders = [t for t in all_teams if course in (t.get("courses") or [])]
    if holders and not detach_teams:
        raise HTTPException(status_code=409, detail={
            "message": f"'{course}' is shared with "
                       f"{', '.join(t['name'] for t in holders)}. Deleting it removes "
                       f"the curriculum everyone there works from. Confirm to go ahead, "
                       f"or take it off the team instead and keep the course.",
            "kind": "course_shared",
            "teams": [{"id": t["id"], "name": t["name"]} for t in holders]})

    out = db.delete_course(course, detach_teams=True, all_teams=all_teams)

    # THE COURSE'S DECKS GO WITH IT. Its own folder, so there is nothing to reason about:
    # no other course's decks are in there. This used to be a per-session calculation
    # against every other course's session numbers, which kept a deleted course's deck
    # whenever some other course happened to share the number.
    try:
        cleared = pptx_ingest.drop_course_decks(course)
    except Exception as e:
        print(f"[courses] could not drop {course!r}'s decks: {e!r}")
        cleared = []
    # And the cloud mirror in ONE statement — one prefix, not one path per deck, which is
    # the shape that made this request time out and answer 503.
    if cleared:
        db.kb_forget_prefix(f"decks/{pptx_ingest.course_slug(course)}/")

    # Never leave the instance-wide active course naming something that is gone: it is
    # what an unnamed request falls back to, and course_name() otherwise drops to a
    # hard-coded legacy default nobody chose.
    # Read ONCE and used for both the repointing below and the reply — it was computed
    # twice, and it is five queries each time.
    rest = db.courses_for_user(user.get("email"), is_admin=user.get("is_admin", False))
    moved_to = None
    if (app_settings.course_name() or "").strip() == course:
        moved_to = next((c["name"] for c in rest if c.get("mine")),
                        next((c["name"] for c in rest), None))
        if moved_to:
            app_settings.save(course_name=moved_to)
        else:
            app_settings.clear_course_name()
    # …and the on-disk projection of the curriculum, which the offline session loader
    # falls back to when the database holds none. It carries no course name, so it cannot
    # be checked against the one being deleted — it is simply rewritten from a course that
    # still exists, or emptied.
    try:
        if moved_to:
            sync.write_course_cache(moved_to)
        elif not db.curriculum_courses():
            sync.clear_course_cache()
    except Exception as e:
        print(f"[courses] could not refresh the curriculum projection: {e!r}")

    return {"ok": True, "deleted": course, "sessions_removed": out["sessions"],
            "teams_detached": out["teams_detached"], "decks_cleared": cleared,
            "history_kept": True, "course": moved_to, "courses": rest}


def _curriculum_rows(course: str, rows: list[dict] | None = None) -> list[dict]:
    """The course's rows, each tagged with whether its deck is REALLY held.

    Every response that returns rows goes through here. It used to be inlined in the
    GET only, so the POST and DELETE replies came back without `extracted` — and the
    dashboard, which renders "pending" whenever that flag is falsy, showed every single
    session as pending the moment you saved an edit. Nothing was being re-extracted;
    the rows had simply lost the field. One source, one shape, no drift.

    The flag is what the knowledge base ACTUALLY holds, not what the table believes: an
    ephemeral disk can lose the extracted text while the row still says "extracted",
    and that is exactly when a re-fetch is genuinely needed.

    `rows` lets a caller that has already read the table hand it over: against a cloud
    database every read is a fresh connection, and the mutating endpoints were each
    re-reading the same curriculum two or three times over on their way out.
    """
    rows = db.curriculum(course) if rows is None else rows
    have_decks = pptx_ingest.deck_session_numbers(course)
    for r in rows:
        r["extracted"] = r["session_no"] in have_decks
    return rows


def _curriculum_reply(course: str, rows: list[dict] | None = None) -> dict:
    """The shape EVERY curriculum-mutating endpoint returns: the table, and the list of
    sessions still needing a document.

    Both, always, from one read — because they are one fact. The dashboard table and the
    Generate dropdown are two views of the curriculum, and the endpoints that changed it
    used to return only the first: insert and delete handed back `rows` and nothing
    else, so deleting session 35 removed it from the table while the dropdown went on
    offering it, and picking it started a run against a session that no longer existed.
    Saving got it right only because it fired a SECOND request for the dropdown
    afterwards — the same drift, patched at one of the three call sites.

    So the reply carries both and no caller has to remember. It is also one round trip
    cheaper than the request-a-refresh-afterwards version it replaces.
    """
    rows = _curriculum_rows(course, rows)
    return {"rows": rows, "sessions": _session_list(course, rows)}


@app.get("/api/course-settings")
def get_course_settings(course: str | None = None, user: dict = Depends(current_user)):
    """A course's length budgets, and what they resolve to.

    `defaults` is what the harness would give, so the UI can show what "inherit" means
    rather than an empty box the user has to guess at.
    """
    from src import budgets as budget_rules
    course = _require_course(user, course)
    return {"course": course,
            "settings": db.course_settings(course) or {},
            "effective": budget_rules.for_session(course),
            "defaults": budget_rules.harness_defaults()}


@app.post("/api/course-settings")
def save_course_settings(body: CourseSettingsBody, user: dict = Depends(current_user)):
    from src import budgets as budget_rules
    course = _require_course(user, body.course)
    db.set_course_settings(course, max_pages=body.max_pages, max_slides=body.max_slides)
    return {"ok": True, "effective": budget_rules.for_session(course)}


@app.post("/api/session-settings")
def save_session_settings(body: SessionSettingsBody, user: dict = Depends(current_user)):
    """One session's budget override, on its own.

    Deliberately NOT folded into the curriculum save: that path upserts the whole row,
    so sending a session number and two numbers would blank the session's name and
    takeaways. A setting that touches two columns gets an endpoint that touches two
    columns.
    """
    from src import budgets as budget_rules
    course = _require_course(user, body.course)
    db.set_session_settings(course, body.session_no,
                            max_pages=body.max_pages, max_slides=body.max_slides)
    return {"ok": True,
            "effective": budget_rules.for_session(course, body.session_no),
            **_curriculum_reply(course)}


# ---- prerequisite courses --------------------------------------------------------
@app.get("/api/prereqs")
def list_prereqs(course: str | None = None, user: dict = Depends(current_user)):
    """This course's prerequisites, what they assume, and where they overlap.

    The coverage report is the one VISIBLE product of attaching them — the other effects
    (the prompt block, the judge's view of assumed knowledge) are real but invisible, and
    a feature whose whole result is "stored" gives the user nothing to act on.
    """
    from src import prereqs as prereq_rules
    course = _require_course(user, course)
    owner = db.course_owner(course)
    return {"course": course,
            "prereqs": db.prereqs(course),
            "report": prereq_rules.coverage_report(course),
            "available": [c["name"] for c in db.courses_for_user(
                user.get("email"), is_admin=user.get("is_admin", False))
                if c["name"] != course],
            "can_edit": bool(user.get("is_admin"))
                        or (owner or "") == (user.get("email") or "").lower()}


@app.post("/api/prereqs")
def add_prereq(body: PrereqBody, user: dict = Depends(current_user)):
    """Attach a prerequisite — a course this agent already holds, so its decks are here
    and nothing is uploaded twice."""
    from src import prereqs as prereq_rules
    course = _require_skill_author(user, body.course)
    src = _require_course(user, body.prereq)
    if src == course:
        raise HTTPException(status_code=400, detail={
            "message": "A course cannot be its own prerequisite."})
    if not db.add_prereq(course, src, added_by=user.get("email")):
        raise HTTPException(status_code=409, detail={
            "message": f"'{src}' is already a prerequisite of '{course}'."})
    return {"ok": True, "prereqs": db.prereqs(course),
            "report": prereq_rules.coverage_report(course)}


def _run_prereq_ingest(job_id: str, course: str, name: str, links: list[str]):
    """Fetch an external prerequisite's decks. Runs in the background, like a sync.

    Each link is a session of a course taught somewhere else. They are extracted exactly
    as this course's own decks are — same fetch, same extractor — and stored in the
    prerequisite substore, which is what keeps them out of "what this course has already
    taught".
    """
    def emit(msg: str, **progress):
        with _lock:
            if job_id in JOBS:
                JOBS[job_id]["logs"].append(msg)
                if progress:
                    JOBS[job_id]["progress"].update(progress)

    # STRUCTURED progress, not just log lines. Fetching a deck from Google Slides takes
    # seconds per link, and until this landed the client had nothing to show for it: the
    # POST returned immediately, the form closed, and the reader saw a silent page while
    # a dozen decks were pulled. The client must not have to parse log prose to find out
    # how far along it is.
    with _lock:
        JOBS[job_id]["progress"] = {"done": 0, "total": len(links), "slides": 0,
                                    "failed": 0, "stage": "starting"}

    ok, errors, slides = 0, [], 0
    emit(f"Fetching {len(links)} deck(s) for the prerequisite '{name}' …",
         stage="fetching")
    print(f"[prereq] {course!r}: reading {len(links)} deck(s) for {name!r}", flush=True)
    for i, link in enumerate(links, start=1):
        # RESUMABLE. A deck already stored under this prerequisite at this position, read
        # from this very link, is not fetched again — so posting the same list after an
        # interrupted read picks up where it stopped instead of spending minutes
        # re-downloading what is already there. Thirty links against a free instance that
        # sleeps is otherwise a race nobody can reliably win.
        # DISK OR MIRROR. Asking the disk alone loses this race after every restart —
        # see pptx_ingest.load_deck.
        have = pptx_ingest.load_deck(course, i, prereq=name)
        if have and (have.get("source_link") or "") == link:
            ok += 1
            slides += int(have.get("n_slides") or 0)
            emit(f"  session {i}: already read — kept.", done=ok, slides=slides,
                 stage=f"reading session {i} of {len(links)}")
            continue
        emit(f"  session {i}: fetching …", stage=f"reading session {i} of {len(links)}")
        try:
            _chash, data = gslides.content_hash(link)
            deck = gslides.extract_from_bytes(data, i, f"{name} — session {i}", link)
            data = None
            pptx_ingest.put_deck(course, i, deck, prereq=name)
            ok += 1
            n_slides = int(deck.get("n_slides") or 0)
            slides += n_slides
            emit(f"  session {i}: {n_slides or '?'} slide(s) extracted.",
                 done=ok, slides=slides)
            # RELEASE IT BEFORE FETCHING THE NEXT. Each link is a ~5 MB download that is
            # copied to strip its media and then parsed, and an 81-slide deck's extracted
            # text is not small either — on a 512 MB instance, holding the last one while
            # downloading the next is enough to matter. A 29-link read died at link 16
            # with the container restarting, which is what that looks like from outside.
            # Rebinding on the next iteration would free it eventually; this frees it
            # now, and collects every few decks rather than every one (the collector is
            # not free either). Best effort — it reduces the peak, it cannot promise the
            # host will not reclaim the instance anyway.
            del deck
            if i % 4 == 0:
                import gc
                gc.collect()
        except Exception as e:
            errors.append(f"session {i}: {e}")
            emit(f"  ⚠ session {i} could not be read: {e}", failed=len(errors))
            # Named in the server log too. A read that fails on the deployed instance is
            # otherwise only ever seen as one line in a browser the operator is not
            # looking at.
            print(f"[prereq] {course!r}: session {i} failed: {e!r}", flush=True)
    # Whatever came back is kept: a prerequisite half-indexed still tells the writer more
    # than none, and the failures are named rather than swallowed.
    if ok:
        try:
            db.kb_backup()
        except Exception:
            pass
    try:
        topics = len(prereqs_mod.assumed_topics(course))
    except Exception as e:
        print(f"[prereq] {course!r}: topic count failed: {e!r}", flush=True)
        topics = 0
    with _lock:
        JOBS[job_id].update(
            status="done" if ok else "error",
            error=None if ok else "None of the links could be read: " + "; ".join(errors),
            error_kind=None if ok else "read",
            progress={"done": ok, "total": len(links), "slides": slides,
                      "failed": len(errors), "stage": "done"},
            result={"prereq": name, "decks": ok, "errors": errors, "slides": slides,
                    "topics": topics})


@app.post("/api/prereqs/external")
def add_external_prereq(body: ExternalPrereqBody, user: dict = Depends(current_user)):
    """Declare a prerequisite taught SOMEWHERE ELSE — a name and its decks.

    The common case: the learners did a JavaScript course elsewhere and all anybody has
    is its slides. There is no course of its own in this agent to hang them on, so the
    decks belong to the course that declared it and go when it goes.

    Everything downstream is identical to an internal prerequisite: same assumed-knowledge
    block, same judge input, and deliberately NOT in the repetition lookup.
    """
    course = _require_skill_author(user, body.course)
    name = " ".join((body.name or "").split())
    links = [l.strip() for l in (body.links or []) if l.strip()]
    if not name:
        raise HTTPException(status_code=400, detail={"message": "Name the prerequisite."})
    if not links:
        raise HTTPException(status_code=400, detail={"message":
            "Give at least one deck link. A prerequisite taught elsewhere is known to "
            "this agent only through its slides — with none, there is nothing to assume."})
    if name in db.curriculum_session_counts():
        raise HTTPException(status_code=409, detail={"message":
            f"'{name}' is already a course in this agent — attach it as a prerequisite "
            f"directly instead, and its own decks are used."})
    # ALREADY ATTACHED IS NOT AN ERROR when the decks are what is being sent. Reading a
    # long list can be cut short — a free instance sleeping, a redeploy, a 502 — and the
    # prerequisite row commits before the first link is fetched, so what is left behind is
    # a prerequisite attached to a partial set of decks. Refusing the same list back was
    # refusing the only way to finish it: the alternative was to remove the prerequisite,
    # which DELETES the decks already read, and start the whole thing again.
    existing = next((p for p in db.prereqs(course) if p["prereq"] == name), None)
    if existing and (existing.get("kind") or "course") != "external":
        raise HTTPException(status_code=409, detail={
            "message": f"'{name}' is already a prerequisite of '{course}', as a course in "
                       f"this agent. Its own decks are used."})
    if not existing:
        db.add_prereq(course, name, added_by=user.get("email"), kind="external")
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None,
                        "error": None, "error_kind": None,
                        "progress": {"done": 0, "total": len(links), "slides": 0,
                                     "failed": 0, "stage": "queued"}}
    def _guarded():
        try:
            _run_prereq_ingest(job_id, course, name, links)
        except Exception as e:
            # A job left "running" spins the progress bar for ever and tells the reader
            # nothing. Whatever happened, the job ends and names itself.
            print(f"[prereq] {course!r}: ingest crashed: {e!r}", flush=True)
            with _lock:
                if job_id in JOBS:
                    JOBS[job_id].update(
                        status="error", error_kind="crash",
                        error=f"Reading the decks stopped unexpectedly: {e}",
                        progress={**(JOBS[job_id].get("progress") or {}),
                                  "stage": "failed"})

    threading.Thread(target=_guarded, daemon=True).start()
    return {"ok": True, "job_id": job_id, "prereqs": db.prereqs(course)}


@app.delete("/api/prereqs")
def remove_prereq(course: str, prereq: str, user: dict = Depends(current_user)):
    """Detach a prerequisite. Neither course is otherwise touched."""
    from src import prereqs as prereq_rules
    course = _require_skill_author(user, course)
    db.remove_prereq(course, prereq)
    return {"ok": True, "prereqs": db.prereqs(course),
            "report": prereq_rules.coverage_report(course)}


# ---- course skills ---------------------------------------------------------------
def _require_skill_author(user: dict, course: str) -> str:
    """Who may change what a course is written under: its owner, or an admin.

    Not every member of a team that shares the course. A skill governs every document
    that course will ever produce, which is a different power from being able to work on
    it — the same distinction the team-membership delegation draws.
    """
    course = _require_course(user, course)
    if user.get("is_admin"):
        return course
    owner = db.course_owner(course)
    if owner and owner == (user.get("email") or "").lower():
        return course
    raise HTTPException(status_code=403, detail={"message":
        f"Only {owner or 'an admin'} can change what '{course}' is written under. "
        f"Working on a course and deciding its rules are different things."})


@app.get("/api/skills")
def list_skills(course: str | None = None, include_retired: bool = False,
                user: dict = Depends(current_user)):
    """This course's skills, and whether the caller may change them."""
    course = _require_course(user, course)
    owner = db.course_owner(course)
    return {"course": course,
            "skills": db.skills(course, include_retired=include_retired),
            "approved": len(db.approved_skills(course)),
            "can_edit": bool(user.get("is_admin"))
                        or (owner or "") == (user.get("email") or "").lower(),
            "owner": owner}


@app.post("/api/skills")
def add_skill(body: SkillBody, user: dict = Depends(current_user)):
    """Path A — write a skill yourself. The agent ARTICULATES it; it starts as a DRAFT.

    What the author types is a note to themselves; what the writer needs is an
    instruction. "From my requirements" has always closed that gap and this path did not,
    so the same author's rules reached the prompt at two different grades depending on
    which button they pressed — one of them verbatim, typos included, carrying precedence
    over the style guide. The articulation is the same contract as path B: state the
    intent properly, invent nothing, and keep the author's own words attached so the
    approval is of a rewrite they can check.

    The draft still needs approving, and the author can Edit it to anything they like —
    so the model is a drafting aid here, never the last word.
    """
    from src import skills as skill_rules
    course = _require_skill_author(user, body.course)
    ok, why = skill_rules.validate_check(body.check)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": why})
    if not (body.text or "").strip():
        raise HTTPException(status_code=400, detail={
            "message": "A skill needs some text."})
    # NEVER FATAL. The model being unreachable must not lose the instruction the author
    # just wrote — it is stored in their own words instead, which is exactly what this
    # path did before, and they can edit it once the model is back.
    drafted = None
    try:
        drafted = skill_rules.articulate(body.text)
    except Exception as e:
        print(f"[skills] articulation failed for {course!r}: {e!r}", flush=True)
    text = (drafted or {}).get("text") or body.text
    kind = (drafted or {}).get("kind") or body.kind
    sid = db.add_skill(course, text, kind=kind, source="user",
                       created_by=user.get("email"), check=body.check,
                       source_quote=(drafted or {}).get("source_quote"),
                       source_quotes=(drafted or {}).get("source_quotes"))
    if not sid:
        raise HTTPException(status_code=400, detail={
            "message": "A skill needs some text."})
    return {"ok": True, "id": sid, "articulated": bool(drafted),
            "skills": db.skills(course)}


@app.post("/api/skills/from-requirements")
def skills_from_requirements(body: SkillFromRequirementsBody,
                             user: dict = Depends(current_user)):
    """Path B — rough requirements become atomic DRAFT skills, each quoting its source.

    The agent formalises; it does not invent. A proposal that cannot quote the words it
    came from is dropped before it is ever offered for approval — see
    skills.from_requirements.
    """
    from src import skills as skill_rules
    course = _require_skill_author(user, body.course)
    try:
        drafts = skill_rules.from_requirements(body.requirements)
    except skill_rules.ModelUnavailable as e:
        # NOT the author's fault, and it must not be reported as if it were.
        print(f"[skills] drafting failed for {course!r}: {e}", flush=True)
        raise HTTPException(status_code=502, detail={"message":
            "The drafting model could not be reached, so nothing was read from your "
            "text — it is still there, try again. If this keeps happening, write the "
            "skill yourself under \u201cWrite one\u201d; that path needs no model."})
    if not drafts:
        raise HTTPException(status_code=400, detail={"message":
            "Nothing could be drawn from that. Say what the course needs in plain "
            "sentences — each draft has to quote the words it came from, so anything "
            "the model could not trace back to your text is discarded."})
    skill_rules.store_drafts(course, drafts, created_by=user.get("email"))
    return {"ok": True, "drafts": len(drafts), "skills": db.skills(course)}


@app.post("/api/skills/import")
def import_skills(body: SkillImportBody, user: dict = Depends(current_user)):
    """Path C — copy another course's approved skills in, as drafts."""
    course = _require_skill_author(user, body.course)
    src = _require_course(user, body.from_course)
    n = db.import_skills(src, course, user.get("email"))
    return {"ok": True, "imported": n, "from": src, "skills": db.skills(course)}


@app.post("/api/skills/{skill_id}/approve")
def approve_skill(skill_id: int, course: str | None = None,
                  user: dict = Depends(current_user)):
    c = _require_skill_author(user, course)
    if not any(s["id"] == skill_id for s in db.skills(c, include_retired=True)):
        raise HTTPException(status_code=404, detail={
            "message": "No such skill on this course."})
    db.approve_skill(skill_id, user.get("email"))
    return {"ok": True, "skills": db.skills(c)}


@app.post("/api/skills/{skill_id}/edit")
def edit_skill(skill_id: int, body: SkillBody, user: dict = Depends(current_user)):
    """Change a skill's wording. It goes back to DRAFT — an approval is of the words
    that were approved."""
    from src import skills as skill_rules
    c = _require_skill_author(user, body.course)
    if not any(s["id"] == skill_id for s in db.skills(c, include_retired=True)):
        raise HTTPException(status_code=404, detail={
            "message": "No such skill on this course."})
    ok, why = skill_rules.validate_check(body.check)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": why})
    if not db.edit_skill(skill_id, body.text, check=body.check):
        raise HTTPException(status_code=400, detail={"message": "A skill needs text."})
    return {"ok": True, "skills": db.skills(c)}


@app.delete("/api/skills/{skill_id}")
def retire_skill(skill_id: int, course: str | None = None,
                 user: dict = Depends(current_user)):
    """Retire a skill. The row is KEPT — a finished document was written under it."""
    c = _require_skill_author(user, course)
    if not any(s["id"] == skill_id for s in db.skills(c, include_retired=True)):
        raise HTTPException(status_code=404, detail={
            "message": "No such skill on this course."})
    db.retire_skill(skill_id, user.get("email"))
    return {"ok": True, "skills": db.skills(c)}


@app.get("/api/course-profile")
def get_course_profile(course: str | None = None, user: dict = Depends(current_user)):
    """What THIS course counts as a good document, and what it inherits.

    Three things, deliberately: the resolved profile (what actually applies), the raw
    overrides (what this course has said), and the harness defaults (what "inherit"
    means). A form showing only the first cannot tell the user which values are theirs.
    """
    from src import profiles as profile_rules
    course = _require_course(user, course)
    return {"course": course,
            "profile": profile_rules.for_course(course),
            "overrides": db.course_profile(course),
            "defaults": profile_rules.harness_defaults()}


@app.post("/api/course-profile")
def save_course_profile(body: CourseProfileBody, user: dict = Depends(current_user)):
    """Set a course's overrides. Rejected as a whole if any part is invalid.

    All-or-nothing on purpose: a profile half-applied is a course being graded by rules
    nobody chose. The reason comes back in the message, because "invalid" on its own
    gives a curriculum author nothing to act on.
    """
    from src import profiles as profile_rules
    course = _require_course(user, body.course)
    ok, cleaned, why = profile_rules.validate(body.profile)
    if not ok:
        raise HTTPException(status_code=400, detail={"message": why})
    if not db.set_course_profile(course, cleaned):
        raise HTTPException(status_code=500, detail={
            "message": "Could not store the profile."})
    return {"ok": True, "course": course,
            "profile": profile_rules.for_course(course),
            "overrides": db.course_profile(course)}


@app.get("/api/curriculum")
def get_curriculum(course: str | None = None, user: dict = Depends(current_user)):
    course = _require_course(user, course)
    rows = _curriculum_rows(course)
    return {"course": course, "rows": rows,
            "imported_from": sync.last_link(),
            "pending": sum(1 for r in rows
                           if (r.get("ppt_link") or "") and not r["extracted"])}


@app.post("/api/curriculum")
def save_curriculum(body: CurriculumSaveBody, course: str | None = None,
                    user: dict = Depends(current_user)):
    """Create or update rows. Only the rows sent are touched — nothing is deleted."""
    course = _require_course(user, course or body.course)
    # Saving rows for a name nobody has claimed is how a course is created by hand
    # (rather than by import), so this is a creation path and records the creator.
    _claim_course(user, course)
    saved = 0
    for row in body.rows:
        ok = db.curriculum_upsert(
            course, row.session_no, topic=row.topic or "",
            session_name=row.session_name or "",
            key_takeaways=row.key_takeaways or [],
            ppt_link=row.ppt_link)
        # Only when the caller actually sent one — a row saved from the table carries
        # no budget fields, and writing None over an existing override would silently
        # discard it every time the curriculum was saved.
        if row.max_pages is not None or row.max_slides is not None:
            db.set_session_settings(course, row.session_no,
                                    max_pages=row.max_pages, max_slides=row.max_slides)
        saved += 1 if ok else 0
    # Keep the on-disk projection in step, so the offline loaders and the eval harness
    # see the edit without waiting for a sync.
    # Course memory follows the curriculum: a row whose link was just cleared must not
    # keep an extracted deck, or the session stays hidden from the generate list and the
    # writer goes on treating that deck as material already taught.
    sync.prune_orphan_decks(course)
    # ONE read, shared by the cache projection and the reply (rows + sessions).
    fresh = db.curriculum(course)
    sync.write_course_cache(course, rows=fresh)
    return {"saved": saved, **_curriculum_reply(course, fresh)}


@app.post("/api/curriculum/insert")
def insert_curriculum_row(body: CurriculumInsertBody, course: str | None = None,
                          user: dict = Depends(current_user)):
    """Insert a session AT a position, moving the rest down.

    A curriculum is an ordered list: session 1 is taught first. The insert button used
    to hand a new row the next FREE number instead, so inserting at the TOP of a
    34-session course produced "Session 35" sitting above Session 1 — which is not a
    curriculum, it is a list with a number stuck on the wrong end.

    Three things move together, and they have to move in one operation or the course is
    left inconsistent:
      · the rows themselves (highest number first, or they overwrite each other);
      · each row's EXTRACTED DECK, which is filed on disk under its session number —
        left behind, Session 6 would read Session 5's deck as "already taught";
      · each row's per-session page/slide override, which is a column on the row and so
        travels with it for free.
    Run HISTORY is deliberately NOT renumbered: a finished document records what was
    generated, under the number it was generated for, and rewriting that would falsify
    the record rather than correct it.
    """
    course = _require_course(user, course or body.course)
    _claim_course(user, course)
    at = int(body.at_session_no)
    if at < 1:
        raise HTTPException(status_code=400,
                            detail={"message": "A session number starts at 1."})
    moved = db.curriculum_shift_from(course, at, by=1)
    if moved:
        try:
            pptx_ingest.renumber_decks(course, moved)
            # …and move them in the DB mirror too, or the rename survives only until the
            # next restart. On the deployed instance the decks live on an EPHEMERAL disk
            # and are mirrored into kb_files, which kb_restore writes back whenever a
            # file is missing — which, after a spin-down, is all of them. Renaming
            # session_02.json to session_03.json on disk alone would therefore be undone
            # on the next boot while the renumbered curriculum rows stayed put, and the
            # new session 2 would inherit the old session 2's deck as "already taught".
            db.kb_rename_decks(course, moved)
        except Exception as e:
            print(f"[curriculum] deck renumber failed after insert at {at}: {e!r}")
    db.curriculum_upsert(course, at, topic=body.topic or "",
                         session_name=body.session_name or "",
                         key_takeaways=body.key_takeaways or [],
                         ppt_link=body.ppt_link)
    fresh = db.curriculum(course)
    sync.write_course_cache(course, rows=fresh)
    return {"course": course, "inserted": at, "shifted": len(moved),
            **_curriculum_reply(course, fresh)}


@app.delete("/api/curriculum/{session_no}")
def delete_curriculum_row(session_no: int, course: str | None = None,
                          user: dict = Depends(current_user)):
    """Remove a session and CLOSE THE GAP behind it.

    The mirror image of inserting: a curriculum is an ordered list, so removing session
    5 of 34 makes the old 6 the new 5, not a course that jumps from 4 to 6. Everything
    moves together for the same reasons as the insert — the rows, their extracted decks,
    and the cloud mirror those decks are restored from.

    Run HISTORY is deliberately untouched: a finished document records what was
    generated under the number it was generated for, and renumbering it would falsify
    the record. This is about what FUTURE runs read.
    """
    course = _require_course(user, course)
    db.curriculum_delete(course, session_no)
    # The deleted row's own deck goes first: its curriculum row is gone, so it is now an
    # orphan, and it must not be sitting on the number the next session is about to take.
    # prune_orphan_decks will not do it — it only touches sessions the curriculum still
    # lists — so the removal is explicit.
    try:
        pptx_ingest.drop_deck(course, session_no)
        db.kb_forget(pptx_ingest.kb_rel(course, session_no))
    except Exception as e:
        print(f"[curriculum] could not drop deck for session {session_no}: {e!r}")
    sync.prune_orphan_decks(course)
    moved = db.curriculum_shift_from(course, int(session_no) + 1, by=-1)
    if moved:
        try:
            pptx_ingest.renumber_decks(course, moved)
            # see insert_curriculum_row: not optional
            db.kb_rename_decks(course, moved)
        except Exception as e:
            print(f"[curriculum] deck renumber failed after deleting {session_no}: {e!r}")
    fresh = db.curriculum(course)
    sync.write_course_cache(course, rows=fresh)
    return {"ok": True, "removed": int(session_no), "shifted": len(moved),
            **_curriculum_reply(course, fresh)}


def _run_ingest(job_id: str, force: bool, sessions: list[int] | None,
                course: str | None = None):
    def on_event(msg: str):
        with _lock:
            JOBS[job_id]["logs"].append(msg)
    try:
        res = sync.ingest_decks(course, force=force, only_sessions=sessions,
                                verbose=True, on_event=on_event)
        with _lock:
            JOBS[job_id].update(status="done", result={
                "sessions": _session_list(course),
                "changelog": res.changelog,
                "errors": res.errors,
                "extraction_warnings": res.extraction_warnings,
                "counts": {"sessions": res.sessions, "ingested": res.decks_ingested,
                           "cached": res.decks_cached},
            })
    except Exception as e:
        with _lock:
            JOBS[job_id].update(status="error", error=str(e), error_kind="read")


@app.post("/api/curriculum/ingest")
def ingest_curriculum_decks(body: IngestBody, user: dict = Depends(current_user)):
    """Fetch the decks this course still needs — and only those."""
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None,
                        "error": None, "error_kind": None}
    threading.Thread(target=_run_ingest,
                     args=(job_id, body.force, body.sessions,
                           _require_course(user, body.course)), daemon=True).start()
    return {"job_id": job_id}


def _session_list(course: str | None = None, rows: list[dict] | None = None):
    """Sessions that still need a TR doc.

    A session whose deck has been ingested has already been recorded, so it is course
    MEMORY rather than work to be done, and it is left out of the generate dropdown.
    (Briefly changed to list everything with an "already recorded" label, after a
    session appeared to vanish once a deck was attached to it — but that session had a
    deck precisely because one had just been pasted in, so the exclusion was doing its
    job. Reverted: the filter is the intended behaviour, and the dropdown stays a list
    of sessions that need writing.)
    """
    # WHAT COUNTS AS "already recorded" is the CURRICULUM's answer, not whatever deck
    # files happen to sit on disk. Reading the disk meant a deck extracted earlier kept
    # a session out of this list even after its link had been removed from the row —
    # the row said "no deck" while the session stayed invisible, with no way to put it
    # back. The curriculum is the source of truth, so it decides here too; the disk scan
    # remains only as the fallback for a process with no curriculum rows (offline evals).
    #
    # `rows` lets a caller that has already fetched the curriculum pass it in. Both
    # halves of this used to read the same table independently — and every caller had
    # ALSO just read it — so selecting a course queried the curriculum four times over.
    course = (course or "").strip() or app_settings.course_name() or "default"
    rows = db.curriculum(course) if rows is None else rows
    if not rows:
        # The on-disk cache is the fallback for a process with NO database (the offline
        # evals), and only that. On a real instance it is the projection of whichever
        # course was synced last, so reaching for it whenever THIS course happens to
        # have no rows would list one course's sessions under another's name — which is
        # what a user with no course of their own would have been shown.
        try:
            if db.curriculum_courses():
                return []
        except Exception:
            pass
        cached = course_loader.load_sessions_from_cache()
        if not cached:
            return []
        have = pptx_ingest.deck_session_numbers(course)
        return [{"number": s.number, "name": s.name, "takeaways": s.key_takeaways}
                for s in cached if s.number not in have]
    have_decks = {r["session_no"] for r in rows if (r.get("ppt_link") or "").strip()}
    return [{"number": r["session_no"], "name": r.get("session_name", ""),
             "takeaways": r.get("key_takeaways", [])}
            for r in rows if r["session_no"] not in have_decks]


@app.get("/api/sessions")
def sessions(course: str | None = None, user: dict = Depends(current_user)):
    """Sessions still needing a doc, for a course the caller is allowed to open.

    This had no auth dependency at all — the only /api route besides health and the
    auth handshake that did not — so an unauthenticated request naming any course in
    the query string got its session list back.
    """
    return {"sessions": _session_list(_require_course(user, course))}


# --------------------------------------------------------------------------- #
# doc rendering helper
#
# There is no /api/generate any more. It ran the one-shot pipeline — a whole TR doc
# drafted, graded and revised in one background job, with nothing seen by a human
# until it was finished. Every doc now comes from the GUIDED endpoints below, where
# each chunk is reviewed and approved before assembly.
# --------------------------------------------------------------------------- #
def _read_markdown(docx_path: str) -> str:
    md = Path(docx_path).with_suffix(".md")
    return md.read_text(encoding="utf-8") if md.exists() else ""


def _run_eval_sets(job_id: str, session_no: int, use_llm: bool, enforce_time: bool):
    try:
        from evals import run_sets
        sessions = course_loader.load_sessions(None)
        _, cur, _ = course_loader.neighbours(session_no, sessions)
        out = config.harness()["output"]
        safe = out["docx_filename"].format(N=cur.number, SessionName=cur.name).replace("/", "-")
        doc_path = config.DATA_ROOT / out["dir"] / (safe.rsplit(".", 1)[0] + ".doc.json")
        if not doc_path.exists():
            raise RuntimeError("No generated doc found for this session — generate it first.")
        doc = json.loads(doc_path.read_text(encoding="utf-8"))
        report = run_sets.run_on_doc(doc, cur, use_llm=use_llm, enforce_time=enforce_time)
        with _lock:
            JOBS[job_id].update(status="done", result=report)
    except Exception as e:
        with _lock:
            JOBS[job_id].update(status="error", error=str(e))


@app.post("/api/eval-sets")
def eval_sets(body: EvalSetsBody, user: dict = Depends(current_user)):
    if body.use_llm and config.api_key() is None:
        raise HTTPException(status_code=400, detail={"message": "No API key configured in .env"})
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None, "error": None}
    threading.Thread(target=_run_eval_sets,
                     args=(job_id, body.session_no, body.use_llm, body.enforce_time),
                     daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str):
    with _lock:
        job = JOBS.get(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Unknown job")
        return dict(job)


# --------------------------------------------------------------------------- #
# guided generation: generate ALL chunks -> review each -> finalize
# --------------------------------------------------------------------------- #
def _guided_log(gid: str, msg: str):
    with _lock:
        if gid in GUIDED:
            GUIDED[gid]["logs"].append(msg)
    try:
        db.update_stage(gid, msg.strip()[:120])   # live stage for the admin view
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# Guided state is checkpointed to the DB after every mutation and rehydrated on
# demand, so a server restart mid-review (a redeploy, or a free host spinning the
# instance down while the human reviews chunks) no longer orphans the run with
# "Unknown guided session" and throw away the chunks already generated.
#
# Only JSON-safe fields are stored. prev/cur/nxt are Session objects, rebuilt from
# session_no on rehydrate; base_context is a plain string and IS stored (rebuilding
# it would re-run the KB sync + RAG retrieval).
# --------------------------------------------------------------------------- #
_GUIDED_PERSIST_KEYS = (
    "status", "session_no", "base_context", "total", "index", "labels", "chunks",
    "regen_index", "use_judge", "enforce_time", "logs", "result", "error",
    "last_error", "user_email", "budgets",
    # WHICH CHUNKS THE REVIEWER HAS TICKED. These lived only in React state, so a reload
    # — or the free host spinning the instance down mid-review, which is exactly the window
    # this checkpoint exists for — threw away every tick and the reviewer had to read and
    # approve all of them again. They are the review itself; they belong on the server.
    "approved_chunks",
    # Standing reviewer notes ("apply this to every chunk after this one"). They govern
    # every later redraft in the run, so a restart mid-review must not lose them — the
    # reviewer would have no way to know the instruction had stopped applying.
    "standing_notes",
    # The reviewer's conversation with the agent about these chunks. It is part of the
    # review, exactly as the approval ticks are: the reason a section was approved may
    # live entirely in an exchange about it, and losing that to a redeploy or the free
    # instance sleeping mid-review would leave the tick with nothing behind it.
    "chat",
    # Persisted so an unfinished run can NAME itself in the resume list without the
    # server loading its whole state or re-deriving the title from a course that may
    # have been re-synced since (see db.unfinished_guided).
    "session_title",
    # The curriculum this document is being written FROM. A run must keep reading the
    # course it started on: without this, resuming after a restart re-read whichever
    # course happened to be selected then, so a doc could be finished out of a
    # different curriculum than it was begun in.
    "course",
)


def _guided_snapshot(state: dict) -> dict:
    return {k: state.get(k) for k in _GUIDED_PERSIST_KEYS}


def _guided_save(gid: str) -> None:
    """Checkpoint the run. The snapshot is taken under the lock, then written
    OUTSIDE it — a cloud DB write is network I/O and must not block polling."""
    with _lock:
        state = GUIDED.get(gid)
        if not state:
            return
        snap = _guided_snapshot(state)
    db.save_guided(gid, snap, user_email=snap.get("user_email"),
                   session_no=snap.get("session_no"))


def _guided_rehydrate(gid: str) -> dict | None:
    """Rebuild an in-flight guided run from its DB checkpoint after a restart.

    Returns the live state (already installed in GUIDED) or None if there is no
    checkpoint for this id. Work that was interrupted mid-flight is resumed:
      • generating_all -> the generation thread is relaunched; it continues from the
        last chunk that was checkpointed, so nothing is regenerated.
      • regenerating / assembling -> back to `reviewing`, since the thread doing
        that work died with the old process. The user just clicks again.
    """
    snap = db.load_guided(gid)
    if not snap:
        return None
    session_no = snap.get("session_no")
    try:
        sessions = course_loader.load_sessions(None, course=snap.get("course"))
        prev, cur, nxt = course_loader.neighbours(session_no, sessions)
    except Exception as e:
        # Don't fail silently: without this line a failed restore looks identical to
        # "no such run", which is exactly what made the original bug hard to see.
        print(f"[guided] cannot restore {gid} (session {session_no}): {e!r}")
        return None

    resume_generation = False
    with _lock:
        if gid in GUIDED:            # another request rehydrated it first
            return GUIDED[gid]
        state = dict(snap)
        state.update(prev=prev, cur=cur, nxt=nxt)
        state.setdefault("chunks", [])
        state.setdefault("logs", [])
        state["logs"] = list(state["logs"]) + [
            "⟳ Server restarted — this guided run was restored from its last checkpoint."]
        state["regen_index"] = None
        if state.get("status") == "generating_all":
            if len(state["chunks"]) >= (state.get("total") or 0):
                state["status"] = "reviewing"
            else:
                resume_generation = True
        elif state.get("status") in ("regenerating", "assembling"):
            state["status"] = "reviewing"
            state["logs"].append(
                "The step running when the server restarted did not finish — "
                "please click it again.")
        state["index"] = len(state["chunks"])
        GUIDED[gid] = state

    _guided_save(gid)
    if resume_generation:
        threading.Thread(target=_guided_generate_all, args=(gid,), daemon=True).start()
    return GUIDED[gid]


def _guided_require(gid: str) -> dict:
    """The live state for `gid`, restoring it from the DB checkpoint if this process
    doesn't have it. 404 only when there is genuinely no such run anywhere."""
    with _lock:
        state = GUIDED.get(gid)
    if state:
        return state
    state = _guided_rehydrate(gid)
    if state:
        return state
    raise HTTPException(
        status_code=404,
        detail={"kind": "guided_gone",
                "message": ("This guided run is no longer available on the server and "
                            "could not be restored. Start a new guided run.")})


def _guided_db_error(gid: str, e: Exception):
    """Mark this guided run as failed in the DB (so it still shows in the dashboard),
    keeping whatever it had already spent — a failed run is not a free one."""
    try:
        db.finish_run(gid, status="error", error=str(e),
                      cost=llm.usage_totals(gid), calls=llm.usage_records(gid))
    except Exception:
        pass


def _guided_step_failed(gid: str, e: Exception, what: str) -> None:
    """A REVIEW-PHASE step (regenerate / finalize) failed — recover, don't die.

    These two steps used to set status='error', which is a TERMINAL state: the UI
    hides the whole review panel for it, so the chunks (one paid LLM call each)
    became unreachable and neither Regenerate nor 'Create final TR Doc' could be
    clicked again — the run was over. But nothing about the run is actually broken:
    one LLM call failed (truncated output, unparseable JSON, a transient HTTP
    error — see logs/llm_debug.log, this happens regularly). So we go back to
    'reviewing' with the previous chunk intact and report the failure as a
    NON-FATAL `last_error` the user can act on by clicking again.

    status='error' is now reserved for the initial generate-all phase, where there
    is no earlier state worth returning to.
    """
    msg = f"{what} failed: {e}"
    with _lock:
        state = GUIDED.get(gid)
        if not state:
            return
        state.update(status="reviewing", regen_index=None, last_error=msg)
    _guided_log(gid, f"⚠ {msg} — nothing was lost; you can try again.")
    _guided_save(gid)


def _fragment_slides(fragment: dict) -> int:
    """Slide count of one guided chunk (0 for the opening)."""
    sec = (fragment or {}).get("section", fragment) or {}
    return len(sec.get("slides") or [])


def _chunk_section(chunk: dict) -> dict:
    """The section dict of a chunk's fragment. Guided fragments come as
    {"section": {...}} but a bare section dict has been seen too, so accept both — the
    same way patcher and pipeline do."""
    frag = (chunk or {}).get("fragment") or {}
    sec = frag.get("section", frag)
    return sec if isinstance(sec, dict) else {}


def _renumber_slides(state: dict) -> list[int]:
    """Number every slide in the run 1..N in document order, and carry each chunk's
    coverage references with them. Returns the chunk indices whose numbering moved.

    WHY IT LIVES HERE. Each chunk numbers its own slides against the chunks that existed
    when it was written, and pipeline.assemble_doc renumbers the whole document at the
    end — which is fine when nothing structural changes during review. Splitting a slide
    IS structural: every slide after it moves, in this chunk and in all the ones after
    it, and the reviewer is reading those numbers on screen while they work. Leaving the
    fix to assembly would show them numbering that disagrees with the document they are
    about to produce.

    Coverage references are remapped alongside, because a coverage entry pointing at a
    slide number that no longer means the same slide is a hard guardrail failure at
    finalize, not a cosmetic nit.
    """
    moved = []
    next_n = 1
    for i, c in enumerate(state.get("chunks") or []):
        slides = [x for x in (_chunk_section(c).get("slides") or []) if isinstance(x, dict)]
        if not slides:
            continue                    # the opening has no slides
        before = [x.get("n") for x in slides]
        assigned = list(range(next_n, next_n + len(slides)))
        next_n += len(slides)
        # The old->new map is built BEFORE anything is written: old and new numbers share
        # one namespace, so renumbering in place would remap through values just assigned.
        old_new = {}
        for old, new in zip(before, assigned):
            if old is not None and old not in old_new:
                old_new[old] = new
        for x, n in zip(slides, assigned):
            x["n"] = n
        frag = c.get("fragment") or {}
        for sub in ((frag.get("coverage") or {}).get("sub_concepts") or []):
            if not isinstance(sub, dict) or sub.get("slide") in (None, ""):
                continue
            try:
                old = int(sub["slide"])
            except (TypeError, ValueError):
                old = sub["slide"]
            if old in old_new:
                sub["slide"] = old_new[old]
        if before != assigned:
            moved.append(i)
            c["markdown"] = docx_writer.chunk_to_markdown(c["kind"], frag)
    return moved


def _slide_budget_state(state: dict, index: int) -> tuple[int, int]:
    """(slides_used, sections_left) for the section chunk at `index`.

    'Used' counts every OTHER section chunk that currently exists, so the number is
    right in both directions: while generating forward only the earlier sections exist,
    and when re-drafting section 3 of 5 the four others do — in which case this chunk is
    the only one left to fit whatever the ceiling still has room for.
    """
    used = sections = 0
    for i, c in enumerate(state["chunks"]):
        if i == index or c.get("kind") != "section":
            continue
        sections += 1
        used += _fragment_slides(c.get("fragment"))
    n_takeaways = state["total"] - 1          # chunk 0 is the opening
    return used, max(1, n_takeaways - sections)


def _chunk_spec(state: dict, index: int):
    """(kind, instruction) for the chunk at `index`: 0 = opening, else takeaway.

    A section instruction carries its SLIDE BUDGET, computed from what the other
    sections have already spent — see context_builder.chunk_slide_allowance. Built per
    call, never cached with base_context, so a re-draft sees the current usage.
    """
    cur, prev = state["cur"], state["prev"]
    if index == 0:
        return "opening", context_builder.opening_instruction(cur, prev)
    used, left = _slide_budget_state(state, index)
    return "section", context_builder.takeaway_instruction(
        state.get("course") or "", cur, index - 1, slides_used=used, sections_left=left,
        enforce_time=state.get("enforce_time", True),
        budgets=state.get("budgets"))


def _chunk_allowance(state: dict, index: int) -> int:
    """The slide allowance the instruction for `index` states (for the over-budget log)."""
    used, left = _slide_budget_state(state, index)
    return context_builder.chunk_slide_allowance(
        state["cur"], slides_used=used, sections_left=left,
        enforce_time=state.get("enforce_time", True))


def _approved_digest(prior: list[dict]) -> str:
    """What the NEXT chunk needs to know about the chunks already written — and no more.

    Every chunk used to receive the full JSON of every chunk before it, so a six-takeaway
    run re-sent the same material five times over: measured at ~25,000 tokens per run,
    at full price, since it changes on every call and so can never be cached. And it
    grows with the SQUARE of the takeaway count, so a longer course pays worst.

    A chunk needs exactly two things from its predecessors: what has already been
    covered (so it does not repeat it) and which slide numbers are taken (so its own
    continue correctly). It does not need their prose, tables, analogies, visual
    guidance or speaker notes — which is the other ~90% of the bulk.
    """
    if not prior:
        return ""
    lines = []
    for c in prior:
        frag = c.get("fragment") or {}
        if c.get("kind") == "opening" or "section" not in frag:
            agenda = frag.get("agenda") or []
            if agenda:
                lines.append("Opening: recap + agenda written ("
                             + f"{len(agenda)} agenda items).")
            continue
        sec = frag.get("section") or {}
        slides = sec.get("slides") or []
        nums = [s.get("n") for s in slides if s.get("n") is not None]
        span = f"slides {min(nums)}-{max(nums)}" if nums else "no slides"
        titles = "; ".join(f"{s.get('n')}. {s.get('title', '')} [{s.get('role', '')}]"
                           for s in slides)
        covered = ", ".join(
            str(sub.get("name")) for sub in ((frag.get("coverage") or {}).get("sub_concepts") or [])
            if isinstance(sub, dict) and sub.get("name"))
        lines.append(f"SECTION “{sec.get('name', '')}” — {span}\n"
                     f"    {titles}\n"
                     f"    already covered: {covered}")
    return ("\n".join(lines)
            + "\n(Summary only — the full text of these sections is already written and "
              "approved. Do not repeat any of it; continue your slide numbering after "
              "the highest number above.)")


def _gen_one(gid: str, index: int, prior: list[dict], reason: str | None = None) -> dict:
    """Generate one chunk, and FIX what we can already see is wrong with it.

    The agent was detecting repeated bullets in a chunk and then… telling the reviewer
    about it, asking them to press Regenerate. That is the wrong division of labour: a
    defect the machine can state precisely is a defect the machine should fix, and a
    human's attention is worth more than one extra call. The reviewer is here to judge
    teaching, not to relay a checklist back to the model.

    So a chunk whose bullets restate their own paragraph is sent back before anyone is
    asked to look at it, with BOTH sides of every collision quoted — the bullet and the
    paragraph sentence it duplicates.

    Three things were wrong with the first version of this, all of them visible in the
    reviewer's log as "the rewrite did not improve on it — keeping the first version":
      · the model was shown a 60-character excerpt of the bullet and never the sentence
        it collided with, so it could not reliably avoid colliding again;
      · one attempt, all-or-nothing — a rewrite that fixed three of four defects was
        thrown away whole because it was not strictly better on the count;
      · when the rewrite failed, the duplication simply shipped to the reviewer.
    Now: the evidence is quoted, we keep the BEST attempt rather than the last, and
    anything still duplicating after the attempts is DROPPED — a bullet that only says
    what the paragraph already said loses nothing when it goes. Dropping stops short of
    taking a list below its minimum length; those few reach the reviewer flagged, which
    is the honest outcome for a case the machine cannot fix without inventing content.
    """
    state = GUIDED[gid]
    kind, instruction = _chunk_spec(state, index)
    # The opening is DERIVED, not generated. Its two fields are the curriculum's own
    # lines copied verbatim (hard rules 3 and 4, both already gates), so a model call
    # here bought nothing and cost ~34,000 prompt tokens — the most expensive call in
    # the run after the sections themselves. See context_builder.build_opening.
    if kind == "opening":
        fragment = context_builder.build_opening(state["cur"], state["prev"])
        if reason:
            _guided_log(gid, "The recap and agenda are copied verbatim from the "
                             "curriculum — that is the rule they are graded on, so "
                             "there is nothing here for a rewrite to change. To alter "
                             "them, edit the session's key takeaways in Curriculum.")
        return {"kind": kind, "fragment": fragment,
                "markdown": docx_writer.chunk_to_markdown(kind, fragment)}
    approved_json = _approved_digest(prior)
    fragment = generator.generate_chunk(
        state["base_context"], instruction, approved_json, reason,
        # THIS RUN's course, never the instance-wide "active course": the rules and the
        # authored brief a document is written under are the course's, and a guided run
        # spans a long review during which anyone else may select a different one.
        course=state.get("course") or None)

    hits = _chunk_repetition_hits(fragment)
    if hits and index > 0:
        rep_cfg = config.harness()["constraints"].get("repetition", {})
        attempts = int(rep_cfg.get("auto_fix_attempts", 2))
        _guided_log(gid, f"Chunk {index + 1}: {len(hits)} bullet(s) repeat their "
                         f"paragraph — fixing before review.")
        for attempt in range(attempts):
            if not hits:
                break
            fix = _repetition_fix_instruction(hits)
            try:
                # PATCH, don't re-draft. Re-generating the chunk to fix three bullets
                # cost a full chunk's OUTPUT — measured at 25,143 completion tokens and
                # $0.27 on one Session 32 repair, more than the chunk it was repairing.
                # A patch names the slides and replaces their content blocks, so the
                # output is the few lines being changed. It also cannot disturb the
                # slides the defect was not about, which a re-draft always could.
                from src import patcher
                patch = generator.generate_patch(
                    state["base_context"], kind, fragment, fix,
                    course=state.get("course") or None)
                retry, _scope = patcher.apply(kind, fragment, patch)
            except Exception as e:
                _guided_log(gid, f"Chunk {index + 1}: auto-fix skipped ({e}).")
                break
            retry_hits = _chunk_repetition_hits(retry)
            # Keep whichever version repeats itself least — including this one when it
            # ties, since a fresh draft that fixed the same number of defects has at
            # least been written against the explicit instruction.
            if len(retry_hits) <= len(hits):
                fragment, hits = retry, retry_hits
                _guided_log(gid, f"Chunk {index + 1}: repetition down to {len(hits)} "
                                 f"after rewrite {attempt + 1}.")
            else:
                _guided_log(gid, f"Chunk {index + 1}: rewrite {attempt + 1} was worse "
                                 f"({len(retry_hits)}) — keeping the better version.")
        if hits and rep_cfg.get("drop_unfixable_bullets", True):
            fragment, dropped, kept = _drop_repeating_bullets(fragment, hits)
            if dropped:
                _guided_log(gid, f"Chunk {index + 1}: removed {dropped} bullet(s) that "
                                 f"only restated the paragraph — the paragraph already "
                                 f"makes those points.")
            hits = _chunk_repetition_hits(fragment)
        if hits:
            _guided_log(gid, f"Chunk {index + 1}: {len(hits)} repeated bullet(s) could "
                             f"not be fixed without emptying a list — flagged for you "
                             f"below.")

    markdown = docx_writer.chunk_to_markdown(kind, fragment)
    return {"kind": kind, "fragment": fragment, "markdown": markdown}


def _guided_record_cost(gid: str) -> None:
    """Persist this guided run's spend SO FAR, without finishing the run.

    Cost used to reach the DB only via finish_run, so a run the reviewer abandoned
    mid-review showed $0.0000 in the dashboard while having paid for one LLM call per
    chunk — three such Session-30 runs were sitting there. Called after every chunk and
    every regeneration, so the number is never further behind than one call.
    """
    try:
        db.update_cost(gid, llm.usage_totals(gid), llm.usage_records(gid))
    except Exception:
        pass


def _guided_slide_budget_note(gid: str, index: int, allowance: int) -> None:
    """Log whether the chunk just produced fits its slide allowance and the ceiling."""
    if index == 0 or not allowance:
        return
    with _lock:
        state = GUIDED.get(gid)
        if not state or index >= len(state["chunks"]):
            return
        got = _fragment_slides(state["chunks"][index].get("fragment"))
        total = sum(_fragment_slides(c.get("fragment")) for c in state["chunks"])
        ceiling = context_builder.slide_ceiling(state.get("enforce_time", True),
                                                state.get("budgets"))
        label = state["labels"][index]
    if got > allowance:
        _guided_log(gid, f"⚠ Chunk {index + 1} used {got} slides against a budget of "
                         f"{allowance} — {total}/{ceiling} of the document's slide "
                         f"ceiling is now spent. Regenerate it with 'cut to {allowance} "
                         f"slides, group related sub-concepts' if the later sections "
                         f"end up squeezed: {label}")
    if total > ceiling:
        _guided_log(gid, f"⚠ The chunks so far already total {total} slides, over the "
                         f"{ceiling}-slide ceiling — the assembled doc will fail the "
                         f"slide, recording-time and page gates unless a chunk is cut.")


def _guided_repetition_note(gid: str, index: int, fragment: dict | None) -> None:
    """Say it in the live log when a chunk's bullets restate their own paragraph.

    The reviewer is the cheapest place to fix this: regenerating one section costs a
    fraction of a repair pass over the assembled document, and it happens while the
    review panel is still open. Left to finalize, the same defect arrives as a hard
    guardrail failure with only one bounded repair round to clear all of it.
    """
    if index == 0:
        return
    hits = _chunk_repetition(fragment)
    if not hits:
        return
    _guided_log(gid, f"⚠ Chunk {index + 1}: {len(hits)} bullet(s) repeat the paragraph "
                     f"above them, which spends page budget without teaching anything "
                     f"new. Regenerate with 'rewrite the bullets to carry what the "
                     f"paragraph does not say — steps, values, conditions, trade-offs' "
                     f"if you want it fixed now: " + "; ".join(hits[:3]))


def _guided_generate_all(gid: str):
    """Generate every chunk up front, then move to the review phase."""
    llm.use_meter(gid)      # this thread's LLM spend belongs to this guided run
    try:
        while True:
            with _lock:
                state = GUIDED.get(gid)
                if not state:
                    return
                i, total = len(state["chunks"]), state["total"]
                if i >= total:
                    break
                prior = [c["fragment"] for c in state["chunks"]]
            # Say which chunks cost a model call and which do not — the opening is
            # copied from the curriculum, and "Generating" would misreport that.
            verb = "Building" if i == 0 else "Generating"
            _guided_log(gid, f"{verb} chunk {i + 1}/{total}: {GUIDED[gid]['labels'][i]} …")
            with _lock:
                allowance = _chunk_allowance(GUIDED[gid], i) if i else 0
            chunk = _gen_one(gid, i, prior)
            with _lock:
                GUIDED[gid]["chunks"].append(chunk)
                GUIDED[gid]["index"] = len(GUIDED[gid]["chunks"])
            # Say it HERE if a section overspent its share of the slide ceiling. The
            # reviewer can then regenerate that one chunk surgically, while the run is
            # still open; discovering it at finalize is discovering it too late, because
            # the assembled doc fails the slide, time and page gates at once and the
            # review panel is gone by then.
            _guided_slide_budget_note(gid, i, allowance)
            _guided_repetition_note(gid, i, chunk.get("fragment"))
            # Checkpoint per chunk: an LLM call each, so a restart must never cost
            # more than the one chunk that was in flight.
            _guided_save(gid)
            _guided_record_cost(gid)
        with _lock:
            GUIDED[gid]["status"] = "reviewing"
        _guided_log(gid, "All chunks generated — review each, then create the final doc.")
        _guided_save(gid)
    except Exception as e:
        # Terminal here (unlike regenerate/finalize): a run whose chunks are
        # incomplete has no earlier state worth returning the user to.
        with _lock:
            if gid in GUIDED:
                GUIDED[gid].update(status="error", error=str(e))
        _guided_save(gid)
        _guided_db_error(gid, e)


def _patch_one(gid: str, index: int, reason: str) -> tuple[dict, dict]:
    """SURGICAL regeneration of one chunk: the model returns a patch, we apply it.

    Returns (chunk, scope_summary). Raises so the caller can fall back to a full
    re-draft; every failure reason is logged, never swallowed.
    """
    from src import patcher
    with _lock:
        state = GUIDED[gid]
        prev_fragment = state["chunks"][index]["fragment"]
        base_context = state["base_context"]
    kind, _ = _chunk_spec(GUIDED[gid], index)
    patch = generator.generate_patch(base_context, kind, prev_fragment, reason,
                                     course=state.get("course") or None)
    fragment, summary = patcher.apply(kind, prev_fragment, patch)
    markdown = docx_writer.chunk_to_markdown(kind, fragment)
    return {"kind": kind, "fragment": fragment, "markdown": markdown}, summary


def _unapprove(state: dict, indices) -> None:
    """Drop the reviewer's tick from chunks whose content has just changed.

    An approval is of the text that was on screen. Once a chunk is regenerated that text
    is gone, so the tick cannot stand — and now that the server holds the ticks, the
    server is what has to drop them rather than trusting the client to.
    """
    drop = set(indices)
    state["approved_chunks"] = sorted(set(state.get("approved_chunks") or []) - drop)


def _standing_notes(state: dict, index: int) -> list[str]:
    """Reviewer notes that were marked "apply to every chunk after this one", and whose
    range covers `index`.

    They are re-asserted on every later regeneration of a covered chunk, not applied once
    and forgotten: they are instructions about how the document is to be written, so a
    chunk redrafted afterwards for some other reason must still obey them. Duplicates are
    dropped so a note repeated by the reviewer is not sent twice.
    """
    out = []
    for note in state.get("standing_notes") or []:
        if not isinstance(note, dict):
            continue
        text = str(note.get("reason") or "").strip()
        if text and index > int(note.get("from_index", 0)) and text not in out:
            out.append(text)
    return out


def _with_standing(state: dict, index: int, reason: str | None) -> str | None:
    """`reason` plus any standing notes that cover this chunk, as one instruction.

    The note being applied right now is itself a standing note covering the chunks after
    it, so it is dropped from the standing block — it is already the primary instruction,
    and stating the same rule twice in one prompt is noise that grows with every note the
    reviewer adds.
    """
    primary = (reason or "").strip()
    standing = [t for t in _standing_notes(state, index) if t != primary]
    if not standing:
        return reason
    block = ("Apply these standing review instructions, which the reviewer gave for "
             "this document as a whole:\n"
             + "\n".join(f"- {t}" for t in standing))
    return f"{reason.strip()}\n\n{block}" if (reason or "").strip() else block


def _apply_to_following(gid: str, from_index: int, reason: str) -> None:
    """Carry one reviewer note into every chunk after `from_index`.

    Sequential on purpose. Each chunk is patched against the note on its own, so a
    failure on one does not cost the others, and `regen_index` moves as it goes so the
    reviewer can see which chunk is in flight rather than watching a single spinner for a
    minute. PATCH first, exactly as a single regeneration does — a standing note is
    usually narrow ("drop the analogies", "shorter headings") and re-drafting six chunks
    from it would throw away every slide the reviewer had already accepted.
    """
    with _lock:
        state = GUIDED.get(gid)
        total = len(state["chunks"]) if state else 0
    rcfg = config.harness().get("regeneration", {}) or {}
    for j in range(from_index + 1, total):
        with _lock:
            state = GUIDED.get(gid)
            if not state:
                return
            state["regen_index"] = j
            label = state["labels"][j]
            before_md = state["chunks"][j]["markdown"]
            session_no = state["session_no"]
            prior = [c["fragment"] for c in state["chunks"][:j]]
            allowance = _chunk_allowance(state, j) if j else 0
            # Earlier standing notes still apply to this chunk, so they travel with the
            # new one rather than being undone by the redraft that answers it.
            effective = _with_standing(state, j, reason)
        _guided_log(gid, f"Applying that note to chunk {j + 1} as well: {label} …")
        chunk = scope = None
        try:
            if rcfg.get("mode", "patch") == "patch":
                try:
                    chunk, scope = _patch_one(gid, j, effective)
                except Exception as e:
                    if not rcfg.get("fallback_to_full", True):
                        raise
                    _guided_log(gid, f"⚠ Chunk {j + 1}: could not patch it ({e}) — "
                                     f"re-drafting the whole chunk.")
                    chunk = scope = None
            if chunk is None:
                chunk = _gen_one(gid, j, prior, effective)
                scope = {"mode": "full", "note": "whole chunk re-drafted"}
        except Exception as e:
            # One chunk failing must not abandon the rest, and the chunk that was there
            # is still in place — so this is a note in the log, not a dead run.
            _guided_log(gid, f"⚠ Chunk {j + 1} could not be updated ({e}) — it is "
                             f"unchanged. Regenerate it on its own if you still need it.")
            continue
        try:
            from src import regen_log
            regen_log.record(session_no, reason, before_md, chunk["markdown"], scope=scope)
        except Exception:
            pass
        with _lock:
            if gid not in GUIDED:
                return
            GUIDED[gid]["chunks"][j] = chunk
            _unapprove(GUIDED[gid], [j])
            # Same reason as in _guided_regenerate: a patch can change the slide count,
            # and the reviewer is reading those numbers while this runs.
            renumbered = _renumber_slides(GUIDED[gid])
        if [i for i in renumbered if i != j]:
            _guided_log(gid, f"Chunk {j + 1} changed length — the slides after it were "
                             f"renumbered.")
        _guided_record_cost(gid)
        _guided_slide_budget_note(gid, j, allowance)
        with _lock:
            _frag = GUIDED.get(gid, {}).get("chunks", [{}])[j].get("fragment") \
                if gid in GUIDED and j < len(GUIDED[gid]["chunks"]) else None
        _guided_repetition_note(gid, j, _frag)
        _guided_save(gid)


def _guided_regenerate(gid: str, index: int, reason: str,
                       apply_to_following: bool = False):
    """Regenerate a single chunk in place (given the chunks before it) during review.

    With `apply_to_following`, the same note is then carried into every chunk after this
    one, and remembered as a STANDING instruction so a later re-draft of any of them
    still obeys it. See _apply_to_following and _standing_notes.
    """
    llm.use_meter(gid)      # a regeneration is part of THIS run's cost, in a new thread
    try:
        with _lock:
            prior = [c["fragment"] for c in GUIDED[gid]["chunks"][:index]]
            session_no = GUIDED[gid]["session_no"]
            before_md = GUIDED[gid]["chunks"][index]["markdown"]   # pre-regeneration content
        # Self-evolution: a human reason for regenerating is durable feedback —
        # remember it so future sessions of this course avoid the same issue.
        try:
            from src import learning
            # THIS RUN's course. Without it the correction is filed against the
            # instance-wide "active course" — a course this reviewer may not even be
            # working on — so it would govern documents there for ever and never reach
            # the one they were actually correcting.
            with _lock:
                run_course = (GUIDED.get(gid) or {}).get("course") or None
            learning.record_feedback(session_no, reason, source="regeneration",
                                     course=run_course)
        except Exception:
            pass
        _guided_log(gid, f"Regenerating chunk {index + 1}: {GUIDED[gid]['labels'][index]} …")
        # The reviewer's own words, kept separate from the instruction actually sent.
        # _with_standing composes the two per chunk, so passing the COMPOSED text on to
        # the fan-out would have it compose again and repeat every earlier standing note.
        note = reason
        with _lock:
            state = GUIDED[gid]
            allowance = _chunk_allowance(state, index) if index else 0
            if apply_to_following:
                # Recorded BEFORE this chunk is regenerated. _standing_notes covers only
                # chunks strictly after `from_index`, so registering it first cannot make
                # this chunk receive the same instruction twice.
                state.setdefault("standing_notes", []).append(
                    {"from_index": index, "reason": note})
            # Any standing note set earlier still governs this chunk.
            reason = _with_standing(state, index, note) or note

        # PATCH FIRST. A reviewer note is almost always narrow, and re-drafting the whole
        # chunk from it threw away the slides they were happy with. A patch touches only
        # what it names, so the rest is not merely "asked to stay the same" — it is never
        # regenerated at all.
        rcfg = config.harness().get("regeneration", {}) or {}
        chunk = scope = None
        # The opening is derived from the curriculum, so both paths that can produce it
        # must derive it — otherwise a patch here would reword an agenda that is
        # required to be verbatim, and the run would fail at finalize for obeying the
        # reviewer. _gen_one rebuilds it and says why in the log.
        if index == 0:
            chunk = _gen_one(gid, 0, prior, reason)
            scope = {"mode": "derived",
                     "note": "recap and agenda are copied from the curriculum"}
        elif rcfg.get("mode", "patch") == "patch":
            try:
                chunk, scope = _patch_one(gid, index, reason)
                warn_at = rcfg.get("warn_above_changed_share", 0.5)
                _guided_log(
                    gid,
                    f"Applied a surgical patch: changed slide(s) "
                    f"{scope.get('slides_changed') or '—'}"
                    + (f", removed {scope['slides_removed']}" if scope.get("slides_removed") else "")
                    + (f", added {scope['slides_added']}" if scope.get("slides_added") else "")
                    + f"; left {len(scope.get('slides_untouched') or [])} slide(s) untouched.")
                if warn_at and scope.get("changed_share", 0) > warn_at:
                    _guided_log(
                        gid,
                        f"⚠ That patch touched {scope['changed_share']:.0%} of the section — "
                        f"broader than a targeted fix usually needs. Check the slides you "
                        f"had already accepted.")
            except Exception as e:
                if not rcfg.get("fallback_to_full", True):
                    raise
                _guided_log(gid, f"⚠ Could not apply a surgical patch ({e}) — falling back "
                                 f"to re-drafting the whole chunk.")
                chunk = scope = None
        if chunk is None:
            chunk = _gen_one(gid, index, prior, reason)
            scope = {"mode": "full", "note": "whole chunk re-drafted"}

        # Log the before/reason/after so the feedback_regeneration_adherence and
        # regeneration_scope_discipline evals can score it.
        try:
            from src import regen_log
            regen_log.record(session_no, reason, before_md, chunk["markdown"], scope=scope)
        except TypeError:      # older regen_log signature (no scope) — keep the event
            regen_log.record(session_no, reason, before_md, chunk["markdown"])
        except Exception:
            pass
        with _lock:
            GUIDED[gid]["chunks"][index] = chunk
            # The text the reviewer ticked is gone, so the tick goes with it.
            _unapprove(GUIDED[gid], [index])
            # A patch may ADD or REMOVE a slide, and a full re-draft comes back at
            # whatever length it likes — so the numbering has to be redone here, exactly
            # as it is after a split. It used to be left to assembly, which meant the
            # review pane showed "Slide None" for an added slide and stale numbers in
            # every later chunk until the document was finished. Now that the reviewer
            # can act on those numbers, they have to be true while they are reading them.
            renumbered = _renumber_slides(GUIDED[gid])
            # Stay in "regenerating" while the note is carried forward, or the UI would
            # see "reviewing", stop polling, and show stale chunks while the rest are
            # still being rewritten underneath it.
            if not apply_to_following:
                GUIDED[gid]["status"] = "reviewing"
                GUIDED[gid]["regen_index"] = None
        _guided_record_cost(gid)      # a regeneration is real spend on this run
        _guided_log(gid, "Chunk updated.")
        _after = [i + 1 for i in renumbered if i != index]
        if _after:
            _guided_log(gid, f"That changed the slide count, so chunk(s) "
                             f"{', '.join(str(x) for x in _after)} were renumbered to "
                             f"follow it.")
        # A patch may ADD slides, and a full re-draft is a fresh roll of the dice on the
        # count, so re-check this chunk against the ceiling exactly as generation does.
        _guided_slide_budget_note(gid, index, allowance)
        with _lock:
            _frag = (GUIDED.get(gid, {}).get("chunks") or [{}])[index].get("fragment") \
                if index < len(GUIDED.get(gid, {}).get("chunks") or []) else None
        _guided_repetition_note(gid, index, _frag)
        _guided_save(gid)
        if apply_to_following:
            with _lock:
                following = len(GUIDED.get(gid, {}).get("chunks") or []) - index - 1
            if following > 0:
                _guided_log(gid, f"Carrying that note into the {following} chunk(s) "
                                 f"after this one.")
                _apply_to_following(gid, index, note)
            with _lock:
                if gid in GUIDED:
                    GUIDED[gid]["status"] = "reviewing"
                    GUIDED[gid]["regen_index"] = None
            _guided_log(gid, "Every following chunk has been updated with that note."
                             if following > 0 else
                             "There are no chunks after this one to apply it to.")
            _guided_save(gid)
    except Exception as e:
        # The chunk that was there before is still in place, so this is recoverable:
        # back to review with a message, NOT a terminal error that strands the run.
        _guided_step_failed(gid, e, f"Regenerating chunk {index + 1}")


def _guided_finalize(gid: str):
    """Assemble all chunks, grade once, render the final .docx."""
    llm.use_meter(gid)      # the judge (and any trim pass) bills to this run
    try:
        with _lock:
            state = GUIDED[gid]
            chunks = state["chunks"]
            cur, nxt, session_no = state["cur"], state["nxt"], state["session_no"]
            use_judge = state["use_judge"]
            enforce_time = state.get("enforce_time", True)
            state_budgets = state.get("budgets") or {}
            # The reviewer's standing instructions go with the document. finalize's
            # repair pass edits slides they already approved, so it must not be the one
            # part of the run that has never heard of them.
            standing = [n.get("reason") for n in (state.get("standing_notes") or [])
                        if isinstance(n, dict) and str(n.get("reason") or "").strip()]
            # The curriculum this run was written FROM. finalize grades against it and
            # reads its decks; without it, grading fell back to whichever course the
            # instance had selected, which need not be this one.
            run_course = state.get("course")
        opening = chunks[0]["fragment"]
        sections = [c["fragment"].get("section", c["fragment"]) for c in chunks[1:]]
        # Each takeaway chunk also reports the sub-concepts it covers and the slide
        # teaching each one; assemble_doc folds those into the doc-level coverage_map
        # (remapping slide numbers, since assembly renumbers the document).
        coverage = [c["fragment"].get("coverage") or {} for c in chunks[1:]]
        doc = pipeline.assemble_doc(cur, nxt, opening, sections, coverage)
        result = pipeline.finalize(session_no, doc, use_judge=use_judge,
                                   enforce_time=enforce_time, run_id=gid,
                                   budgets=state_budgets, standing_notes=standing,
                                   course=run_course,
                                   on_event=lambda m: _guided_log(gid, m))
        final = result.get("final") or result["history"][-1]
        # Persist the rendered outputs BEFORE surfacing the result. A guided run has
        # already cost a long human review by this point, so its document must not be
        # recoverable only from the instance disk.
        outputs.persist(gid, result.get("docx"))
        with _lock:
            GUIDED[gid].update(status="done", result={
                "run_id": gid,
                "session_no": session_no,
                # The course this document was written FROM. Feedback given on the
                # finished doc is filed against it, so it must travel with the result
                # rather than being inferred from whatever the page has selected — after
                # resuming somebody else's run those are not the same course.
                "course": run_course or "",
                "accepted": final["accepted"],
                "time": final["time"],
                "pages": final.get("pages"),
                "judge": final.get("judge"),
                "issues": final.get("issues", []),
                "docx_name": Path(result["docx"]).name,
                "markdown": _read_markdown(result["docx"]),
                "cost": result.get("cost"),
            })
        _guided_save(gid)
        cost = result.get("cost") or {}
        try:
            db.finish_run(
                gid, status="done", accepted=final.get("accepted"),
                rubric=(final.get("judge") or {}).get("weighted_total"),
                est_minutes=final.get("time", {}).get("estimated_minutes"),
                est_pages=(final.get("pages") or {}).get("estimated_pages"),
                rounds=len(result.get("history", [])),
                slides=final.get("time", {}).get("slide_count"),
                cost=cost.get("totals"), calls=cost.get("calls"),
                docx_path=result.get("docx"))
        except Exception:
            pass
    except Exception as e:
        # Assembly/grading failed, but every approved chunk is still here — send the
        # user back to review so they can fix a chunk and click Create again.
        _guided_step_failed(gid, e, "Creating the final TR doc")
        _guided_db_error(gid, e)


def _chunk_repetition(fragment: dict) -> list[str]:
    """Bullets in this chunk that restate the paragraph above them.

    Run per chunk and shown at REVIEW time, because that is the only moment the fix is
    cheap: the reviewer can regenerate this one section for a few cents, whereas the
    same defect found at finalize costs a repair pass over the whole assembled document
    — and one bounded repair round cannot reliably clear five of them at once.

    Same measure as the guardrail (constraints.content.bullet_echo_overlap), so the
    reviewer is warned about exactly what would fail the run later.
    """
    return [h["summary"] for h in _chunk_repetition_hits(fragment)]


def _chunk_repetition_hits(fragment: dict) -> list[dict]:
    """The same detection, keeping the EVIDENCE rather than just a count.

    Each hit carries the slide, the offending bullet IN FULL, and the exact paragraph
    clause it duplicates. The repair pass used to be handed only a 60-character excerpt
    of the bullet — "62% of 'Direction reverses only at the physical end or last pend'
    is already in the paragraph above it" — so the model had to guess which sentence it
    was colliding with, and its rewrite often collided with the same one again. That is
    the loop the reviewer kept seeing end in "the rewrite did not improve on it".
    """
    from guardrails.guardrails import _norm_tokens
    import re as _re
    c = config.harness()["constraints"].get("content", {})
    if not c.get("no_bullet_echoes_lead_in", False):
        return []
    thr = float(c.get("bullet_echo_overlap", 0.5))
    out: list[dict] = []
    section = (fragment or {}).get("section") or {}
    for s in section.get("slides") or []:
        blocks = s.get("content") or []
        clauses = [x.strip() for b in blocks if b.get("type") == "text"
                   for x in _re.split(r"[;.!?]", str(b.get("text") or ""))
                   if len(_norm_tokens(x)) >= 3]
        if not clauses:
            continue
        for bi, b in enumerate(blocks):
            if b.get("type") != "bullets":
                continue
            for ii, it in enumerate(b.get("items") or []):
                bt = _norm_tokens(it)
                if len(bt) < 3:
                    continue
                best, source = 0.0, None
                for cl in clauses:
                    shared = bt & _norm_tokens(cl)
                    ratio = len(shared) / len(bt)
                    if len(shared) >= 2 and ratio > best:
                        best, source = ratio, cl
                if best >= thr:
                    out.append({
                        "slide": s.get("n", "?"), "block": bi, "item": ii,
                        "bullet": str(it), "paragraph": source or "", "overlap": best,
                        "summary": (f"Slide {s.get('n', '?')} · {best:.0%} of "
                                    f"\"{str(it)[:60]}\" is already in the paragraph "
                                    f"above it"),
                    })
    return out


def _drop_repeating_bullets(fragment: dict, hits: list[dict]) -> tuple[dict, int, int]:
    """Delete the bullets that still only restate their paragraph. (frag, dropped, kept)

    The last resort, and a safe one: by definition these lines carry nothing the slide
    does not already say, so removing them costs no teaching — it gives back the page
    budget they were spending. Two limits, so this can never damage a slide:
      · a list is never taken below constraints.content.min_bullet_items (a two-item
        list is a bulleted sentence and would fail its own gate);
      · a list is never emptied.
    Whatever those limits protect stays, and is reported to the reviewer instead.
    """
    import copy
    c = config.harness()["constraints"].get("content", {})
    floor = int(c.get("min_bullet_items", 3) or 0)
    frag = copy.deepcopy(fragment)
    by_slide: dict = {}
    for h in hits:
        by_slide.setdefault(h["slide"], []).append(h)
    dropped = kept = 0
    for s in ((frag or {}).get("section") or {}).get("slides") or []:
        for h in by_slide.get(s.get("n"), []):
            blocks = s.get("content") or []
            if h["block"] >= len(blocks):
                continue
            items = blocks[h["block"]].get("items") or []
            # Match on text, not index: an earlier drop in the same list shifts them.
            try:
                at = items.index(h["bullet"])
            except ValueError:
                continue
            if len(items) - 1 < max(floor, 1):
                kept += 1
                continue
            items.pop(at)
            dropped += 1
    return frag, dropped, kept


def _repetition_fix_instruction(hits: list[dict]) -> str:
    """The repair order, quoting BOTH sides of every collision.

    Naming the paragraph clause is the whole point: the model cannot avoid restating a
    sentence it was never shown. Each item also states the only two acceptable outcomes
    — replace the bullet with information the paragraph does not carry, or delete it —
    so "reword it" (which keeps the same content and fails again) is off the table.
    """
    lines = []
    for h in hits[:8]:
        lines.append(
            f"  Slide {h['slide']}, bullet: \"{h['bullet']}\"\n"
            f"     duplicates this sentence of the SAME slide's paragraph: "
            f"\"{h['paragraph']}\"\n"
            f"     ({h['overlap']:.0%} of the bullet's words are already in it)")
    return (
        "REPETITION TO FIX. On the slides below, a bullet says what the paragraph on "
        "the same slide already says. The document has a hard page ceiling, so a line "
        "that repeats is a line that cannot teach anything:\n"
        + "\n".join(lines)
        + "\n\nFor EACH one, do exactly one of these two things:\n"
          "  (a) REPLACE the bullet with a specific the paragraph does not state — a "
          "step of the procedure, a value, a condition or edge case, a trade-off, a "
          "failure mode, where it is used; or\n"
          "  (b) DELETE the bullet and let the paragraph carry that point alone.\n"
          "Rewording the same point in different words is NOT one of the options — it "
          "leaves the duplication in place. Do not touch anything else in this section: "
          "keep every other slide, title, table and bullet exactly as it is.")


def _guided_view(state: dict) -> dict:
    """JSON-safe snapshot (Session objects and base_context are kept server-side)."""
    labels = state["labels"]
    chunks = [{"label": labels[i], "markdown": c["markdown"],
               "repetition": _chunk_repetition(c.get("fragment")),
               # The slides this chunk holds, so the reviewer can name one to SPLIT
               # without the UI having to parse them back out of the markdown.
               "slides": [{"n": x.get("n"), "title": x.get("title") or ""}
                          for x in (_chunk_section(c).get("slides") or [])
                          if isinstance(x, dict)]}
              for i, c in enumerate(state["chunks"])]
    return {
        "status": state["status"],
        # Which session this run is for. Needed when a run is resumed from the server's
        # list on a browser that never started it: the page has to move the session
        # selector to the run being resumed, and it cannot know the number otherwise.
        "session_no": state.get("session_no"),
        "session_title": state.get("session_title"),
        # The course this run is being written FROM. The page has its own idea of the
        # selected course, and after resuming somebody else's run they are not the same
        # — so anything filed from inside this run (a skill promoted out of the chat)
        # has to use the run's, not the page's.
        "course": state.get("course") or "",
        "index": state["index"],
        "total": state["total"],
        "enforce_time": state.get("enforce_time", True),
        "labels": labels,
        "chunks": chunks,
        "regen_index": state.get("regen_index"),
        # The chunks the reviewer has ticked, and whether that is all of them. The client
        # used to be the only holder of this, so it was also the only judge of whether the
        # final doc could be created.
        "approved_chunks": sorted(state.get("approved_chunks") or []),
        "all_approved": bool(state.get("chunks")) and len(
            set(state.get("approved_chunks") or [])
            & set(range(len(state["chunks"])))) == len(state["chunks"]),
        # Notes the reviewer marked "apply to every chunk after this one". Shown back so
        # a standing instruction is visible rather than invisible state that quietly
        # governs every later redraft.
        "standing_notes": [n for n in (state.get("standing_notes") or [])
                           if isinstance(n, dict)],
        # The Q&A about each chunk, oldest first. `pending` marks a question whose
        # answer is still being written, so the panel can show it immediately rather
        # than swallowing what the reviewer typed until the model comes back.
        "chat": [m for m in (state.get("chat") or []) if isinstance(m, dict)],
        "chat_pending": bool(state.get("chat_pending")),
        # What the answer in flight is doing right now — a real transition, not a timer.
        "chat_stage": state.get("chat_stage") or None,
        "result": state.get("result"),
        "error": state.get("error"),
        # A step that failed but left the run usable (see _guided_step_failed). The
        # UI shows this as a warning INSIDE the review panel, not as a dead end.
        "last_error": state.get("last_error"),
        "logs": state.get("logs", []),
    }


@app.post("/api/guided/start")
def guided_start(body: GuidedStartBody, user: dict = Depends(current_user)):
    if config.api_key() is None:
        raise HTTPException(status_code=400, detail={"message": "No API key configured in .env"})
    # Read the curriculum of the course THIS REQUEST names. Falling back to the
    # process-wide "selected course" made the content depend on whoever selected last:
    # two people generating for different courses at the same time got each other's
    # sessions, and the run was still stamped with the course it was asked for.
    run_course = _require_course(user, body.course)
    sessions = course_loader.load_sessions(None, course=run_course)
    prev, cur, nxt = course_loader.neighbours(body.session_no, sessions)
    labels = ["Opening (recap + agenda)"] + [
        f"Key takeaway {i + 1}: {kt[:70]}" for i, kt in enumerate(cur.key_takeaways)]
    gid = uuid.uuid4().hex[:12]
    # Start token/cost accounting for THIS guided doc (chunks + regens + judge),
    # mirroring what pipeline.run() does for one-shot generations. Keyed by `gid`: the
    # old process-wide reset meant starting a second guided run erased the first one's
    # accounting, so whichever finished next reported the wrong figure.
    from src import llm
    llm.reset_usage(gid)
    # The 40-minute toggle applies to guided runs exactly as it does to one-shot:
    # ON  -> every chunk is generated under the hard time limit and the doc is graded on it;
    # OFF -> chunks are generated in DEPTH MODE and recording time is never graded.
    # The budgets THIS document is held to: the course's own, or this session's
    # override, or the harness default. Resolved once and carried on the run, so the
    # prompt, the gates and the repair pass all speak about the same numbers.
    from src import budgets as budget_rules
    run_budgets = budget_rules.for_session(run_course, body.session_no)
    from src import profiles as profile_rules
    run_profile = profile_rules.for_course(run_course)
    base_context = (context_builder.build_guided_base(run_course, prev, cur, nxt,
                                                      run_profile)
                    + context_builder.time_mode_block(body.enforce_time, guided=True,
                                                      budgets=run_budgets))
    with _lock:
        GUIDED[gid] = {
            "status": "generating_all", "session_no": body.session_no,
            "session_title": cur.name,
            "prev": prev, "cur": cur, "nxt": nxt,
            "base_context": base_context,
            "total": 1 + len(cur.key_takeaways), "index": 0, "labels": labels,
            "chunks": [], "regen_index": None, "use_judge": body.use_judge,
            "enforce_time": body.enforce_time, "user_email": user.get("email"),
            "budgets": run_budgets, "course": run_course,
            "logs": [], "result": None, "error": None,
        }
    # Checkpoint before any chunk is generated, so even a restart during the very
    # first LLM call leaves a resumable run rather than an orphaned id.
    _guided_save(gid)
    # Record the run in the DB up-front (status=running) so guided generations
    # show live and persist in the dashboard, exactly like one-shot runs.
    try:
        email = user.get("email")
        db.create_run(gid, user_email=email, course=run_course,
                      team_id=_run_team(user, body.team_id, run_course),
                      session_no=body.session_no, title=cur.name,
                      enforce_time=body.enforce_time)
    except Exception:
        pass
    threading.Thread(target=_guided_generate_all, args=(gid,), daemon=True).start()
    return {"guided_id": gid}


@app.get("/api/guided/resumable")
def guided_resumable(user: dict = Depends(current_user)):
    """Guided runs this user started and never finished.

    The resume offer used to come only from the browser's localStorage, so it existed
    in the one browser that started the run: sign in from another machine, clear site
    data or use a private window, and a run holding several already-paid-for chunks was
    unreachable while the server still had its checkpoint. This asks the server instead,
    so the offer follows the USER rather than the browser.

    Checkpoints older than the purge window (db.purge_guided, 72h) are gone for good and
    correctly do not appear here.
    """
    try:
        return {"runs": db.unfinished_guided(user.get("email"))}
    except Exception:
        return {"runs": []}


@app.post("/api/guided/{gid}/discard")
def guided_discard(gid: str, user: dict = Depends(current_user)):
    """Stop offering this unfinished run. Recorded server-side, so it stays discarded.

    Previously the browser just forgot the id, and the next page load asked the server
    for unfinished runs and was handed the same one back — the prompt kept returning
    with no way to dismiss it for good.
    """
    # Checked against the checkpoint rather than through _guided_require_mine, which
    # 404s on an id the purge window has already collected — dismissing a stale offer
    # has to keep working.
    try:
        snap = db.load_guided(gid) or {}
    except Exception:
        snap = {}
    owner = (snap.get("user_email") or "").lower()
    if owner and not user.get("is_admin") and owner != (user.get("email") or "").lower():
        raise HTTPException(status_code=403, detail={
            "message": "That unfinished run belongs to someone else."})
    with _lock:
        GUIDED.pop(gid, None)          # drop the in-memory copy too, if it is loaded
    return {"ok": db.discard_guided(gid)}


def _guided_require_mine(gid: str, user: dict) -> dict:
    """The run, if this person is entitled to it.

    These two endpoints had NO auth dependency: a guided id is the whole document —
    every generated chunk, in full — and anyone holding one could read it and spend
    somebody else's tokens regenerating chunks of it. Entitlement is the run's owner,
    an admin, or anyone who may open the course the run belongs to (a team-mate working
    in the same workspace, which is the point of a shared workspace).

    A checkpoint written before runs recorded an email has no owner to compare against;
    those fall back to the course check rather than locking their own author out.
    """
    state = _guided_require(gid)       # restores from the checkpoint if needed
    email = (user.get("email") or "").lower()
    owner = (state.get("user_email") or "").lower()
    if user.get("is_admin") or (owner and owner == email):
        return state
    if db.can_use_course(email, state.get("course") or "", is_admin=False):
        return state
    raise HTTPException(status_code=403, detail={"message":
        "That generation belongs to someone else, and not to a course shared with a "
        "team you are on."})


@app.get("/api/guided/{gid}")
def guided_state(gid: str, user: dict = Depends(current_user)):
    state = _guided_require_mine(gid, user)
    with _lock:
        return _guided_view(state)


@app.post("/api/guided/{gid}/regenerate")
def guided_regenerate(gid: str, body: RegenerateBody,
                      user: dict = Depends(current_user)):
    state = _guided_require_mine(gid, user)
    with _lock:
        if state["status"] != "reviewing":
            raise HTTPException(
                status_code=409,
                detail=f"Another step ({state['status']}) is still running — "
                       f"wait for it to finish, then regenerate.")
        if not (0 <= body.index < len(state["chunks"])):
            raise HTTPException(status_code=400, detail="Chunk index out of range.")
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400,
                            detail="A reason is required to regenerate a chunk.")
    with _lock:
        state["status"] = "regenerating"
        state["regen_index"] = body.index
        state["last_error"] = None      # this attempt supersedes the previous failure
    _guided_save(gid)
    threading.Thread(target=_guided_regenerate,
                     args=(gid, body.index, reason, bool(body.apply_to_following)),
                     daemon=True).start()
    return {"ok": True, "apply_to_following": bool(body.apply_to_following)}


def _run_doc_chat(gid: str, index: int, question: str, use_web: bool) -> None:
    """Answer one reviewer question, on a thread. Never raises out of the thread.

    The reviewer's own message is already on the record before this starts (see the
    endpoint), so a failure here costs them the ANSWER, never the question — they can
    ask again without retyping, and the failed turn says what went wrong instead of
    disappearing.
    """
    from src import doc_chat
    llm.use_meter(gid)      # a question is part of THIS run's cost, in a new thread
    try:
        with _lock:
            state = GUIDED.get(gid)
            if not state:
                return
            # A SNAPSHOT, taken under the lock and used outside it. The model call takes
            # seconds and must not hold the lock that every poll needs; and the pack has
            # to describe the document as it was when the question was asked, not as it
            # may be a regeneration later.
            snapshot = dict(state)
        # LIVE STAGES, from the work actually happening. The panel polls the guided view
        # anyway, so the stage rides along on it — no new endpoint, no new polling.
        def on_stage(name, detail):
            with _lock:
                st = GUIDED.get(gid)
                if st is not None:
                    st["chat_stage"] = {"name": name, "detail": detail,
                                        "index": index, "at": db._now()}

        answer = doc_chat.ask(snapshot, index, question, use_web=use_web,
                              on_stage=on_stage)
        msg = {"id": uuid.uuid4().hex[:8], "index": index, "role": "agent",
               "text": answer["text"], "web": answer.get("web", False),
               "suggested_feedback": answer.get("suggested_feedback") or "",
               # A standing preference the conversation settled, kept apart from the
               # one-off fix above. Offered as a DRAFT course skill, never applied.
               "suggested_rule": answer.get("suggested_rule") or "",
               # What was consulted (assembled by code, checkable) and what it cited on
               # the web (parsed from its own answer). Different kinds of claim, kept
               # apart on purpose.
               "consulted": answer.get("consulted") or [],
               "sources": answer.get("sources") or [],
               "at": db._now()}
    except Exception as e:
        print(f"[chat] {gid} chunk {index}: {e!r}", flush=True)
        msg = {"id": uuid.uuid4().hex[:8], "index": index, "role": "agent",
               "failed": True, "at": db._now(),
               "text": f"That question could not be answered just now — {e}. "
                       f"Nothing about the document changed. Ask again when you are "
                       f"ready; your question is still above."}
    with _lock:
        state = GUIDED.get(gid)
        if state is not None:
            chat = state.setdefault("chat", [])
            chat.append(msg)
            # Bounded, so a long review cannot grow the checkpoint without limit.
            if len(chat) > doc_chat.MAX_TURNS_KEPT:
                del chat[:len(chat) - doc_chat.MAX_TURNS_KEPT]
            state["chat_pending"] = False
            state["chat_stage"] = None
    _guided_save(gid)


@app.post("/api/guided/{gid}/ask")
def guided_ask(gid: str, body: AskBody, user: dict = Depends(current_user)):
    """Ask the agent about one section — why it wrote what it wrote.

    READ-ONLY, and that is the whole design. The reviewer already has a way to change a
    section: reject it with a reason, which is the right lever once they have decided
    something is wrong and the wrong one while they are still working out whether it is.
    This is for that earlier moment. It cannot edit, regenerate or approve anything, so
    asking a question can never cost the reviewer work they had already accepted — and
    if the answer does not convince them, regeneration is exactly where it always was.

    Available during review and after the document is built. A finished document is the
    one people go back and query, and refusing then would be refusing the question at
    the moment it is most often asked.
    """
    state = _guided_require_mine(gid, user)
    question = " ".join((body.question or "").split())
    if not question:
        raise HTTPException(status_code=400, detail={"message": "Ask something."})
    from src import doc_chat as _chat
    with _lock:
        chunks = state.get("chunks") or []
        if body.index != _chat.WHOLE_DOC and not (0 <= body.index < len(chunks)):
            raise HTTPException(status_code=400, detail={
                "message": "That section is not in this run."})
        if not chunks:
            raise HTTPException(status_code=400, detail={
                "message": "There is nothing written yet to ask about."})
        if state.get("chat_pending"):
            raise HTTPException(status_code=409, detail={
                "message": "The last question is still being answered — one at a time, "
                           "so the answers stay in order."})
        # ON THE RECORD BEFORE THE CALL. If the model is unreachable the reviewer must
        # still see what they asked, rather than watching their typing vanish.
        chat = state.setdefault("chat", [])
        chat.append({"id": uuid.uuid4().hex[:8], "index": body.index, "role": "user",
                     "text": question, "at": db._now()})
        state["chat_pending"] = True
        state["chat_stage"] = {"name": "queued", "detail": "starting", "index": body.index,
                               "at": db._now()}
        view = _guided_view(state)
    _guided_save(gid)
    threading.Thread(target=_run_doc_chat,
                     args=(gid, body.index, question, bool(body.use_web)),
                     daemon=True).start()
    return view


@app.post("/api/guided/{gid}/approve")
def guided_approve_chunk(gid: str, body: ApproveChunkBody,
                         user: dict = Depends(current_user)):
    """Tick (or un-tick) one chunk as reviewed.

    These ticks ARE the review, and they used to exist only in the reviewer's browser: a
    reload, a second machine, or the instance spinning down mid-review threw all of them
    away and the whole document had to be read and approved again. Worse, the client was
    the only judge of whether every chunk had been ticked — the one condition that lets a
    document be created at all.

    When the last one goes in, the moment is stamped on the run (db.mark_review_done). It
    is a distinct step from pressing Create final TR Doc, and the gap between the two is
    where a reviewer finished reading and then stopped.
    """
    state = _guided_require_mine(gid, user)
    with _lock:
        total = len(state.get("chunks") or [])
        if not (0 <= body.index < total):
            raise HTTPException(status_code=400,
                                detail={"message": "Chunk index out of range."})
        ticked = set(state.get("approved_chunks") or [])
        if body.approved:
            ticked.add(body.index)
        else:
            ticked.discard(body.index)
        state["approved_chunks"] = sorted(ticked)
        complete = len(ticked & set(range(total))) == total
        view = _guided_view(state)
    if complete:
        try:
            db.mark_review_done(gid)
        except Exception as e:
            print(f"[guided] could not record that {gid} was fully reviewed: {e!r}")
        _guided_log(gid, "Every chunk approved — the document can be created.")
    _guided_save(gid)
    return view


@app.post("/api/guided/{gid}/split")
def guided_split_slide(gid: str, body: SplitSlideBody,
                       user: dict = Depends(current_user)):
    """Split one slide of one chunk into two, and renumber the whole run.

    Deterministic and synchronous — NO model call. The reviewer has already accepted this
    content; a slide that carries too much for one slide needs its content divided, not
    rewritten, and a re-draft would be free to change the slides either side of it. The
    second half inherits the fields every slide must carry and can be polished with an
    ordinary Regenerate afterwards.

    Every slide after the split moves — in this chunk AND in all the later ones — so the
    run is renumbered here rather than at assembly, because the reviewer is reading those
    numbers on screen while they work.
    """
    from src import patcher            # imported per-call, like every other user of it
    state = _guided_require_mine(gid, user)
    con = config.harness()["constraints"]
    with _lock:
        if state["status"] != "reviewing":
            raise HTTPException(
                status_code=409,
                detail={"message": f"Another step ({state['status']}) is still running — "
                                   f"wait for it to finish, then split the slide."})
        if not (0 <= body.index < len(state["chunks"])):
            raise HTTPException(status_code=400,
                                detail={"message": "Chunk index out of range."})
        chunk = state["chunks"][body.index]
        if chunk.get("kind") != "section":
            raise HTTPException(status_code=400, detail={"message":
                "The opening chunk has no slides — its recap and agenda come from the "
                "curriculum."})
        try:
            fragment, summary = patcher.split_slide(
                chunk["fragment"], body.slide_n,
                title_max_words=(con.get("headings", {}) or {}).get("title_max_words", 8),
                min_bullet_items=(con.get("content", {}) or {}).get("min_bullet_items", 3),
                intro_role="concept_intro",
                continuation_role="mechanism")
        except patcher.PatchError as e:
            raise HTTPException(status_code=400, detail={"message": str(e)})
        allowance = _chunk_allowance(state, body.index) if body.index else 0
        chunk["fragment"] = fragment
        chunk["markdown"] = docx_writer.chunk_to_markdown(chunk["kind"], fragment)
        # This chunk now has a slide it did not have; the reviewer has not seen it. The
        # later chunks were only RENUMBERED, so their approvals stand — re-asking for
        # sign-off on text nobody changed would be noise.
        _unapprove(state, [body.index])
        moved = _renumber_slides(state)
        view = _guided_view(state)
    _guided_log(gid, f"Split slide {body.slide_n} of chunk {body.index + 1} into two — "
                     f"its content was divided, not rewritten."
                     + (f" The second slide inherited its "
                        f"{', '.join(summary['inherited_fields'])} from the first; "
                        f"regenerate it if that wording does not fit."
                        if summary.get("inherited_fields") else ""))
    if summary.get("coverage_refs_added"):
        _guided_log(gid, f"The coverage map now points at both halves — a slide nothing "
                         f"in it references fails the 'teaches nothing the agenda "
                         f"promised' gate.")
    if not summary.get("prose_on_both_halves"):
        _guided_log(gid, "⚠ Only one of the two slides carries a prose paragraph: this "
                         "slide's prose could not be divided. The document is graded on "
                         "the share of slides that have one, so regenerate the pair with "
                         "'give each of these two slides its own framing sentence' if the "
                         "mix gate complains at finalize.")
    if summary.get("role_changed"):
        _guided_log(gid, f"The second slide was given the role "
                         f"'{summary['role_changed']}' and no analogy: an analogy is "
                         f"required only on a first introduction and banned everywhere "
                         f"else, and the same one may not appear on two slides.")
    later = [i + 1 for i in moved if i != body.index]
    if later:
        _guided_log(gid, f"Renumbered the slides in chunk(s) "
                         f"{', '.join(str(x) for x in later)} to follow it.")
    _guided_slide_budget_note(gid, body.index, allowance)
    _guided_save(gid)
    with _lock:
        return {**_guided_view(GUIDED[gid]), "split": summary,
                "renumbered_chunks": moved}


@app.post("/api/guided/{gid}/finalize")
def guided_finalize(gid: str, user: dict = Depends(current_user)):
    """Assemble, grade and render the approved document.

    Reaching this endpoint IS the human approval: the button that calls it is disabled
    until every chunk has been ticked in the review panel. That was previously recorded
    nowhere — the ticks lived in the browser and the dashboard fell back to the GRADERS'
    verdict, which is why it read "Approved: 0" against seventeen finished documents.
    """
    state = _guided_require_mine(gid, user)
    with _lock:
        if state["status"] != "reviewing":
            raise HTTPException(
                status_code=409,
                detail=f"Another step ({state['status']}) is still running — "
                       f"wait for it to finish, then create the final doc.")
        if not state.get("chunks"):
            raise HTTPException(status_code=409,
                                detail="This run has no generated chunks to assemble.")
        # Checked HERE, not only by the disabled button. Reaching this endpoint IS the
        # human approval of the document, and it may only be claimed for a document that
        # was actually reviewed chunk by chunk — which the server now knows.
        _ticked = set(state.get("approved_chunks") or []) & set(range(len(state["chunks"])))
        if len(_ticked) != len(state["chunks"]):
            missing = [i + 1 for i in range(len(state["chunks"])) if i not in _ticked]
            raise HTTPException(status_code=409, detail={"message":
                f"Chunk(s) {', '.join(str(m) for m in missing)} have not been approved. "
                f"Every chunk has to be reviewed before the document is created."})
        state["status"] = "assembling"
        state["last_error"] = None
    _guided_save(gid)
    # Stamp the approval BEFORE the assembly runs, so it survives a failure in
    # rendering or grading: the person approved the content, and that fact is not
    # contingent on what the graders say about it afterwards.
    try:
        db.mark_approved(gid, user.get("email"))
    except Exception:
        pass
    threading.Thread(target=_guided_finalize, args=(gid,), daemon=True).start()
    return {"ok": True}


@app.get("/api/extraction-check")
def extraction_check(course: str | None = None,
                     user: dict = Depends(current_user)):
    return pptx_ingest.completeness_report(_require_course(user, course))


@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody, user: dict = Depends(current_user)):
    """Teach the agent from a finished document, outside Guided mode.

    Until now `learning.record_feedback` was reachable ONLY from a guided-mode
    regeneration, so a reviewer working in one-shot mode had no way to give feedback at
    all — the corrections that should have become durable rules were simply never
    captured. (The UI even had a 'feedback' source label that nothing could produce.)

    The distilled rule is returned so the reviewer can SEE what the agent took away
    from their note, and delete it if the distillation missed the point.
    """
    from src import learning
    reason = (body.reason or "").strip()
    if len(reason) < 5:
        raise HTTPException(status_code=400, detail={"message":
            "Say what should change, in a sentence — it becomes a rule applied to every "
            "future document in this course."})
    before = {r.get("text") for r in learning.rules()}
    fb_course = _require_course(user, body.course) if body.course else None
    try:
        learning.record_feedback(body.session_no, reason, source="feedback",
                                 course=fb_course)
    except Exception as e:
        raise HTTPException(status_code=502, detail={"message": f"Could not record that: {e}"})
    rules = learning.rules()
    added = next((r for r in reversed(rules) if r.get("text") not in before), None)
    # No new rule means it folded into an existing one (a restatement) — which is the
    # dedupe working, not a failure, so report the reinforced rule instead.
    if added is None:
        added = max(rules, key=lambda r: (r.get("hits") or 1)) if rules else None
        return {"ok": True, "merged": True, "rule": added,
                "message": "Folded into an existing rule and raised its priority."}
    return {"ok": True, "merged": False, "rule": added,
            "message": "Learned — this will be applied to every future doc in this course."}


@app.get("/api/learned-rules")
def learned_rules(user: dict = Depends(current_user)):
    """Every stored rule, each flagged with whether it applies to the ACTIVE course.

    `rules` is the full store (so nothing is hidden from the reviewer) and `applies`
    marks the subset actually injected right now — a subject-matter rule learned on
    another course is listed but not applied. Indices line up with the DELETE route.
    """
    from src import learning
    course = app_settings.course_name()
    # Compare by text, not identity: applicable_rules() re-reads the store, so its
    # dicts are different objects from rules()'.
    applies = {r.get("text") for r in learning.applicable_rules(course)}
    # `gated` explains the most common reason a rule is listed but not applied: a
    # deterministic guardrail now enforces it better than the prose could, so it is no
    # longer injected OR checked by the judge (which used to re-adjudicate it and
    # occasionally invent a violation). Nothing is hidden — the rule stays visible.
    return {
        "course": course,
        "rules": [{**r, "applies": r.get("text") in applies,
                   "gated": learning.gate_for(r)}
                  for r in learning.rules()],
    }


@app.delete("/api/learned-rules")
def clear_learned_rules(user: dict = Depends(require_admin)):
    from src import learning
    learning._save({"rules": []})
    return {"ok": True}


@app.post("/api/learned-rules/migrate")
def migrate_learned_rules(user: dict = Depends(require_admin)):
    """Re-distil + scope the rule store IN PLACE (the deployed equivalent of
    `python3 -m src.learning`).

    Needed because learned_rules.json is runtime data and gitignored, so a deploy
    does NOT carry the locally-migrated store: the instance restores the older
    snapshot from kb_files, whose rules are the raw reviewer notes with no `scope`.
    Under the new code those are treated as house style AND injected at system level
    with precedence over the style guide — so raw, typo-laden, slide-specific notes
    would carry more weight than before, on every course. This endpoint distils them
    into standalone rules and classifies each as house vs subject-matter.

    Idempotent: rules that already have `raw` are not re-distilled and rules that
    already have `scope` are not re-classified, so calling it twice is a no-op.
    Costs a few cheap judge-model calls (one per unmigrated rule).
    """
    from src import learning
    distil = learning.distil_existing()
    scope = learning.scope_existing(app_settings.course_name())
    return {"ok": True, "distil": distil, "scope": scope,
            "rules": learning.rules()}


class RuleScopeBody(BaseModel):
    scope: str                       # "global" (house style) | "course" (subject matter)
    course: str | None = None        # which course a course-scoped rule belongs to


@app.post("/api/learned-rules/{index}/scope")
def set_learned_rule_scope(index: int, body: RuleScopeBody,
                           user: dict = Depends(current_user)):
    """Re-classify ONE rule as house style or subject matter.

    The classification is made by a model at distil time and it gets it wrong: the live
    store has "Remove working code examples; rely on pseudocode and conceptual
    explanation instead" marked HOUSE STYLE, learned from "remove slide 8 and 12, working
    examples are not needed for this topic" — a note about one topic, now a standing
    instruction for every course on the instance, including ones created next year.

    Until now the only lever was DELETE, which is the wrong one in both directions: a
    house rule wrongly scoped to a course cannot be promoted at all, and demoting a
    wrongly-global rule meant destroying it for the course that did ask for it. This
    moves it instead. The rule's text, its raw note and its hit count are untouched —
    only where it applies changes.
    """
    from src import learning
    scope = (body.scope or "").strip().lower()
    if scope not in (learning.GLOBAL, learning.COURSE):
        raise HTTPException(status_code=400, detail={"message":
            f"Scope must be {learning.GLOBAL!r} (applies to every course) or "
            f"{learning.COURSE!r} (applies only to its own)."})
    data = learning._load()
    rs = data.get("rules", [])
    if not (0 <= index < len(rs)):
        raise HTTPException(status_code=404, detail={"message": "No such rule."})
    rule = rs[index]
    if scope == learning.COURSE:
        # A course rule needs a course, or it applies to nothing and quietly disappears
        # from every prompt. Keep the one it was learned on unless told otherwise.
        target = (body.course or rule.get("course") or "").strip()
        if not target:
            raise HTTPException(status_code=400, detail={"message":
                "This rule does not record which course it was learned on, so it cannot "
                "be narrowed to one without naming it."})
        rule["course"] = target
    rule["scope"] = scope
    learning._save(data)
    return {"ok": True, "rule": rule, "rules": learning.rules()}


@app.delete("/api/learned-rules/{index}")
def delete_learned_rule(index: int, user: dict = Depends(current_user)):
    """Drop ONE rule. These are injected with precedence over the style guide now, so
    a rule that was distilled too narrowly (one that names a specific topic, say)
    would otherwise be pushed at every future session with no way to retract it."""
    from src import learning
    data = learning._load()
    rs = data.get("rules", [])
    if not (0 <= index < len(rs)):
        raise HTTPException(status_code=404, detail="No such rule.")
    removed = rs.pop(index)
    learning._save(data)
    return {"ok": True, "removed": removed.get("text"), "remaining": len(rs)}


def _rollup(runs: list) -> dict:
    # FOUR different counts, and they are genuinely different numbers:
    #   total_runs   every attempt, including ones that failed, were abandoned, or are
    #                still running;
    #   docs_built   the attempts that actually produced a document (status 'done').
    #                This is what a card labelled "Docs built" means — an abandoned run
    #                is not a document — and the team panel had no such number to read,
    #                so it read one that does not exist and showed 0 against real work;
    #   approved_docs a PERSON signed it off, which is what the label says and what the
    #                reviewer expects to go up when they press Create final TR Doc;
    #   gates_passed_docs the GRADERS' verdict, reported alongside rather than instead.
    return {
        "total_runs": len(runs),
        "docs_built": len([r for r in runs
                           if r.get("outcome") in ("completed", "approved")]),
        "approved_docs": len([r for r in runs if r.get("approved")]),
        "gates_passed_docs": len([r for r in runs if r.get("gates_passed")]),
        "total_cost": round(sum((r.get("cost") or {}).get("cost", 0) or 0 for r in runs), 6),
        "total_tokens": sum((r.get("cost") or {}).get("total_tokens", 0) or 0 for r in runs),
    }


def _group_by_course(runs: list) -> list:
    by: dict = {}
    for r in runs:
        by.setdefault(r.get("course") or "Uncategorised", []).append(r)
    return [{"course": c, "runs": rs, "summary": _rollup(rs)}
            for c, rs in sorted(by.items())]


# ---- the signed-in user's own data (agent app) ----
@app.get("/api/dashboard")
def dashboard(user: dict = Depends(current_user)):
    """The signed-in user's OWN runs + roll-up (agent app cost dashboard)."""
    runs = db.runs(user_email=user.get("email"))
    return {"runs": runs, "summary": _rollup(runs), "is_admin": user.get("is_admin", False)}


@app.get("/api/my/history")
def my_history(user: dict = Depends(current_user)):
    """The user's complete generation history, grouped by course, with the docx
    filename so the UI can offer downloads of the final outputs."""
    runs = db.runs(user_email=user.get("email"))
    return {"courses": _group_by_course(runs), "summary": _rollup(runs)}


@app.get("/api/my/teams")
def my_teams(user: dict = Depends(current_user)):
    """Teams the user belongs to, each with EVERY doc the team has produced.

    Scoped by the team's COURSE, not by the team_id stamped on a run. That stamp is
    written at generation time from team_for_user_course(), so it is null for a run
    made before the team existed, before the person was added to it, or by someone who
    is on no team at all — and every one of those runs was invisible to the team even
    though it is exactly the shared work they need to see. The course is the thing a
    team owns, so the course is what gathers the history.

    Each run carries who made it, so the list reads as a team feed rather than an
    anonymous pile.
    """
    email = (user.get("email") or "").lower()
    out = []
    for t in db.teams_for_user(email):
        members = t.get("members", [])
        # The courses are already on `t` — passing them avoids re-querying them per team.
        runs = db.team_runs(t["id"], t.get("courses"))
        # Whether THIS person may change who is on the team, decided server-side: the
        # team page offers the add/remove controls off this flag, and a client must never
        # be the one deciding what it is allowed to do.
        t = {**t, "can_manage": bool(user.get("is_admin"))
                                or (t.get("owner_email") or "") == email}
        out.append({"team": t, "courses": _group_by_course(runs),
                    "summary": _rollup(runs), "members": members,
                    "contributors": sorted({r.get("user_email") for r in runs
                                            if r.get("user_email")})})
    return {"teams": out}


# ---- admin analytics + live tracking (separate admin app) ----
@app.get("/api/admin/overview")
def admin_overview(user: dict = Depends(require_admin)):
    return {
        "summary": db.summary(),
        "daily": db.timeseries("day"),
        "weekly": db.timeseries("week"),
        "monthly": db.timeseries("month"),
        "per_user": db.per_user(),
        "live": db.live_runs(),
        "connectors": _connectors(),
    }


@app.get("/api/admin/runs")
def admin_runs(user: dict = Depends(require_admin), course: str | None = None,
               user_email: str | None = None, status: str | None = None):
    # page_limit travels with the rows so the runs table can flag over-length docs
    # against the harness ceiling even when the admin never opened the Overview tab.
    return {"runs": db.runs(course=course, user_email=user_email, status=status),
            "page_limit": config.harness()["constraints"]["pages"]["max"]}


@app.get("/api/admin/live")
def admin_live(user: dict = Depends(require_admin)):
    return {"live": db.live_runs()}


@app.get("/api/admin/users")
def admin_users(user: dict = Depends(require_admin)):
    return {"users": db.users(), "per_user": db.per_user()}


def _connectors() -> list:
    """Health of the external integrations the pipeline depends on."""
    m = config.harness()["model"]
    c = sync.last_link()
    try:
        warns = len(pptx_ingest.completeness_report(
            app_settings.course_name() or "default").get("decks", []))
    except Exception:
        warns = None
    return [
        {"name": "LLM provider", "detail": f"{m.get('provider')} · {m.get('generator')}",
         "ok": config.api_key() is not None},
        {"name": "Judge model", "detail": m.get("judge"), "ok": config.api_key() is not None},
        {"name": "Curriculum Sheet", "detail": "linked" if c else "not linked", "ok": bool(c)},
        {"name": "Google Slides ingest", "detail": f"{warns} deck(s) known" if warns is not None else "n/a",
         "ok": True},
        {"name": "Google Sign-In", "detail": "configured" if config.google_client_id() else "not configured",
         "ok": config.google_client_id() is not None or config.auth_disabled()},
    ]


@app.get("/api/admin/courses")
def admin_list_courses(user: dict = Depends(require_admin)):
    """Every course on the instance, with what an admin needs before deleting one.

    Deliberately more than /api/courses carries. Deleting a course removes the curriculum
    a team may be working from, so the decision needs the facts that make it reversible or
    not: how many sessions would go, WHO created it, which teams work from it, and how many
    documents have already been built (those are KEPT — see db.delete_course — and saying
    so is what stops the button looking like it destroys finished work).

    `sessions` of 0 with `docs_built` of 0 is what a course created by accident looks
    like — a name that got claimed by a request that never meant to create anything.

    Six queries flat, whatever the number of courses: teams, curriculum counts, owners and
    the run log are each read once and grouped here.
    """
    all_teams = db.teams()
    counts = db.curriculum_session_counts()
    owners = db.course_owners()
    runs = db.runs(limit=100000)

    by_course: dict = {}
    for r in runs:
        by_course.setdefault(r.get("course") or "", []).append(r)

    names = set(counts) | set(owners)
    for t in all_teams:
        names.update(t.get("courses") or [])
    # A course with no curriculum rows left but a run history still existed once, and its
    # DOCUMENTS still exist — deleting a course deliberately keeps them. An admin needs to
    # see those, so the name stays on this list. It must not look like a live course
    # though: `state` below says which it is, because a deleted course sitting in the list
    # under a Delete button reads as a delete that did not work.
    names.update(c for c in by_course if c)

    out = []
    for name in sorted(names):
        rs = by_course.get(name, [])
        owning = [t for t in all_teams if name in (t.get("courses") or [])]
        # THREE STATES, and they are not the same thing:
        #   live          it has a curriculum — a course you can open and work on;
        #   history_only  its curriculum has been DELETED, and the documents it produced
        #                 are still here (that is the point: deleting a course does not
        #                 un-generate its docs). Nothing left to delete;
        #   empty         no curriculum and no runs — a name claimed by a request that
        #                 never meant to create anything, and worth clearing out.
        state = ("live" if counts.get(name)
                 else "history_only" if rs
                 else "empty")
        out.append({
            "name": name,
            "state": state,
            "sessions": counts.get(name, 0),
            "created_by": owners.get(name),
            "unclaimed": name not in owners and not owning,
            "teams": [{"id": t["id"], "name": t["name"]} for t in owning],
            "members": sorted({m for t in owning for m in (t.get("members") or [])}),
            "total_runs": len(rs),
            "docs_built": len([r for r in rs
                               if r.get("outcome") in ("completed", "approved")]),
            "contributors": sorted({r.get("user_email") for r in rs if r.get("user_email")}),
            "last_activity": max((r.get("ts") or "" for r in rs), default=None),
            "total_cost": round(sum((r.get("cost") or {}).get("cost", 0) or 0 for r in rs), 6),
        })
    return {"courses": out}


# ---- team management -------------------------------------------------------------
#
# Creating, renaming, re-coursing and deleting a team is the ADMIN's. MEMBERSHIP is not:
# it is delegated to the team's course owner, who the admin names when the team is
# created. Adding a colleague to a team is a routine, low-stakes act, and routing every
# one of them through a single admin account meant, in practice, that people did not get
# added at all.
def _team_email(raw: str | None) -> str:
    """A member/owner email, normalised and checked against the allowed domain.

    The domain check is the point: `add_member` writes whatever it is given and
    membership is matched by exact string, so 'alice' or 'alice@gmail.com' silently
    creates a member row that can never match a signed-in user. The team then looks
    populated and the person it was for sees nothing.
    """
    email = (raw or "").strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail={
            "message": "Give a full email address."})
    domain = (config.auth().get("allowed_domain") or "").lower()
    if domain and not email.endswith("@" + domain):
        raise HTTPException(status_code=400, detail={
            "message": f"Only @{domain} addresses can be on a team — '{email}' would "
                       f"never match a signed-in user."})
    return email


def _require_team_manager(user: dict, team_id: int) -> dict:
    """The team, if this person may change who is on it: an admin, or its course owner.

    An ordinary member may not. Seeing a team's work and deciding who else sees it are
    different powers, and only one of them is delegated.
    """
    all_teams = db.teams()
    team = next((t for t in all_teams if t.get("id") == int(team_id)), None)
    if team is None:
        raise HTTPException(status_code=404, detail={"message": "No such team."})
    if db.can_manage_team(user.get("email"), int(team_id),
                          is_admin=user.get("is_admin", False), all_teams=all_teams):
        return team
    owner = team.get("owner_email")
    raise HTTPException(status_code=403, detail={"message":
        f"Only an admin or {team['name']}'s course owner can change who is on it."
        if owner else
        f"{team['name']} has no course owner yet, so only an admin can change who is "
        f"on it. Ask an admin to assign one."})


def _no_orphaning_the_owner(team: dict, email: str) -> None:
    """Refuse to remove the person the team is owned BY.

    Otherwise the team is left with an owner who is not on it — still able to manage
    members, but unable to open the workspace they are responsible for. Re-assigning
    the owner is the way to change this, and that is the admin's call.
    """
    if (team.get("owner_email") or "") == email:
        raise HTTPException(status_code=409, detail={"message":
            f"{email} is this team's course owner, so they cannot be removed from it. "
            f"Ask an admin to assign a different owner first."})


def _team_name(raw: str | None, *, all_teams: list[dict] | None = None,
               allow_id: int | None = None) -> str:
    """A team name that is usable, and not already taken.

    The name is how a person PICKS a workspace — the switcher lists names, not ids — so
    two teams called the same thing leaves them choosing blind. Rejected on both the
    create and the rename path, because a guard one of them can walk around is no guard.
    `allow_id` is the team being renamed, which is allowed to keep its own name (so
    correcting only the capitalisation still works).
    """
    name = " ".join((raw or "").split())          # collapse stray whitespace
    if not name:
        raise HTTPException(status_code=400, detail={"message": "Give the team a name."})
    if len(name) > 80:
        raise HTTPException(status_code=400, detail={
            "message": "That name is too long — 80 characters at most."})
    for t in (db.teams() if all_teams is None else all_teams):
        if t.get("id") == allow_id:
            continue
        if (t.get("name") or "").strip().lower() == name.lower():
            raise HTTPException(status_code=409, detail={
                "message": f"A team called '{t['name']}' already exists. Names are how "
                           f"people pick a workspace, so two of them cannot share one."})
    return name


@app.post("/api/admin/teams/{team_id}/name")
def admin_rename_team(team_id: int, body: TeamNameBody,
                      user: dict = Depends(require_admin)):
    """Rename a team.

    Only the label changes. Membership, the courses it owns, its course owner and every
    document in its history all key off the team's id or its courses, never its name — so
    there is nothing to cascade, and a name that was typed wrong or has since changed can
    simply be corrected.
    """
    all_teams = db.teams()
    if not any(t.get("id") == int(team_id) for t in all_teams):
        raise HTTPException(status_code=404, detail={"message": "No such team."})
    name = _team_name(body.name, all_teams=all_teams, allow_id=int(team_id))
    ok = db.rename_team(int(team_id), name)
    return {"ok": ok, "id": int(team_id), "name": name}


@app.get("/api/admin/teams")
def admin_list_teams(user: dict = Depends(require_admin)):
    return {"teams": db.teams(), "users": [u["email"] for u in db.users()]}


@app.post("/api/admin/teams")
def admin_create_team(body: TeamCreateBody, user: dict = Depends(require_admin)):
    """Create a team, naming its COURSE OWNER — which is required, not optional.

    The owner is recorded three ways, all at once, because they are three consequences
    of the same decision:
      · on the team, as who may add and remove its members;
      · as a member, so they can actually open the workspace they run;
      · as the owner of the team's course, so it is on their individual shelf and they
        can open its curriculum whether or not the team survives.

    That last write REPLACES any owner the course already had. It is an explicit admin
    assignment, and the reply says whose it was, so a mistake is visible rather than
    silent.
    """
    owner = _team_email(body.owner)
    name = _team_name(body.name)
    course = (body.course or "").strip() or None
    previous = db.course_owner(course) if course else None
    tid = db.create_team(name, course, user.get("email"), owner_email=owner)
    if course:
        db.set_course_owner(course, owner)
    return {"id": tid, "owner": owner, "course": course,
            "replaced_owner": previous if previous and previous != owner else None}


@app.post("/api/admin/teams/{team_id}/owner")
def admin_set_team_owner(team_id: int, body: MemberBody,
                         user: dict = Depends(require_admin)):
    """(Re)assign a team's course owner. Admin only — this is what makes the delegation
    below a delegation rather than something anyone can grant themselves."""
    owner = _team_email(body.email)
    team = next((t for t in db.teams() if t.get("id") == int(team_id)), None)
    if team is None:
        raise HTTPException(status_code=404, detail={"message": "No such team."})
    db.set_team_owner(int(team_id), owner)
    # …and of every course the team holds, so the new owner can open them individually
    # too. Same override as at creation, for the same reason.
    for c in (team.get("courses") or []):
        db.set_course_owner(c, owner)
    return {"ok": True, "owner": owner, "courses": team.get("courses") or []}


@app.post("/api/admin/teams/{team_id}/members")
def admin_add_member(team_id: int, body: MemberBody, user: dict = Depends(require_admin)):
    db.add_member(team_id, _team_email(body.email))
    return {"ok": True}


@app.delete("/api/admin/teams/{team_id}/members/{email}")
def admin_remove_member(team_id: int, email: str, user: dict = Depends(require_admin)):
    team = next((t for t in db.teams() if t.get("id") == int(team_id)), None)
    if team:
        _no_orphaning_the_owner(team, email.strip().lower())
    db.remove_member(team_id, email.strip().lower())
    return {"ok": True}


# ---- membership, delegated to the team's course owner ------------------------------
@app.post("/api/teams/{team_id}/members")
def team_add_member(team_id: int, body: MemberBody, user: dict = Depends(current_user)):
    """Add someone to a team. Admin or the team's course owner.

    Anyone added sees the team's courses and its whole shared history, including work
    done before they arrived — that is what the team workspace is for.
    """
    _require_team_manager(user, team_id)
    email = _team_email(body.email)
    db.add_member(int(team_id), email)
    try:
        db.upsert_user(email)      # so they show up in the admin user list before first sign-in
    except Exception:
        pass
    return {"ok": True, "members": sorted(
        next((t.get("members") or [] for t in db.teams() if t["id"] == int(team_id)), []))}


@app.delete("/api/teams/{team_id}/members/{email}")
def team_remove_member(team_id: int, email: str, user: dict = Depends(current_user)):
    """Remove someone from a team. Admin or the team's course owner.

    They lose the team's courses from their shelf; anything they generated stays in the
    team's history, because it is the team's work and deleting the record would falsify
    it.
    """
    team = _require_team_manager(user, team_id)
    target = email.strip().lower()
    _no_orphaning_the_owner(team, target)
    db.remove_member(int(team_id), target)
    return {"ok": True, "members": sorted(
        next((t.get("members") or [] for t in db.teams() if t["id"] == int(team_id)), []))}


@app.post("/api/admin/teams/{team_id}/course")
def admin_set_course(team_id: int, body: CourseBody, user: dict = Depends(require_admin)):
    db.set_team_course(team_id, body.course)
    # The team's owner owns its course. Claimed rather than overridden here: setting a
    # team's course is a correction (usually a spelling), and it must not quietly take a
    # course away from whoever created it.
    owner = db.team_owner(int(team_id))
    if owner and (body.course or "").strip():
        db.claim_course(body.course.strip(), owner)
    return {"ok": True}


@app.delete("/api/admin/teams/{team_id}")
def admin_delete_team(team_id: int, user: dict = Depends(require_admin)):
    db.delete_team(team_id)
    return {"ok": True}


@app.post("/api/gdoc/{session_no}")
def create_gdoc(session_no: int, body: GdocBody, user: dict = Depends(current_user)):
    """Upload the generated .docx to the SIGNED-IN user's Google Drive as a native
    Google Doc and return its link. The file is created with the user's own Drive
    token, so the user owns it and is the only editor — edit access is theirs alone.

    Resolved through src.outputs like the download, and for the same reason: this used
    to re-derive the filename from the synced course, so it failed on exactly the docs
    the download failed on — leaving a reviewer with no way to get the document out."""
    got = _resolve_output(session_no, body.run_id, body.name, user=user)
    from src import gdrive
    # The Drive title comes from the OUTPUT's own filename, not from the current
    # curriculum, so a re-synced sheet cannot mislabel the uploaded document either.
    title = got.filename.rsplit(".", 1)[0]
    try:
        if got.path is not None:
            res = gdrive.upload_as_gdoc(got.path, title, body.access_token)
        else:
            res = gdrive.upload_as_gdoc_bytes(got.read_bytes(), title, body.access_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail={"message": f"Google Drive upload failed: {e}"})
    return {"id": res.get("id"), "link": res.get("webViewLink"), "name": res.get("name")}


@app.get("/admin")
def admin_page():
    """Serve the standalone admin app (it authenticates via Google itself and
    talks to the /api/admin/* endpoints). Also hostable separately."""
    p = config.ROOT / "admin-frontend" / "index.html"
    if not p.exists():
        raise HTTPException(status_code=404, detail="Admin app not found.")
    return FileResponse(str(p), media_type="text/html")


_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _resolve_output(session_no: int, run_id: str | None, name: str | None,
                    kind: str = "docx", user: dict | None = None):
    """Locate a run's rendered output, or raise a 404 that says what was searched.

    Every download path goes through here. It never re-derives the filename from the
    currently-synced course as its first move — that is what made Download and Create
    Google Doc both fail on a finished document (see src/outputs.py).

    It is also where a document's OWNERSHIP is checked. These endpoints identify an
    output by run id or exact filename, so course scoping in the UI does not reach them:
    a run id or a filename from someone else's document fetched it in full. The run row
    says which course it belongs to, and that is the same question the rest of the app
    asks. An output no run row matches (rendered before runs were recorded, or found
    only on disk) has nobody to attribute it to and is left as it was.
    """
    if user is not None and not user.get("is_admin"):
        row = db.run_for_output(run_id, name)
        if row and not db.can_use_course(user.get("email"), row.get("course") or "",
                                         is_admin=False):
            raise HTTPException(status_code=403, detail={"message":
                "That document was generated for a course you cannot open."})
    got = outputs.resolve(session_no, run_id=run_id, filename=name, kind=kind)
    if got is None:
        raise HTTPException(status_code=404, detail={"message":
            f"Could not find the generated {kind} for session {session_no}. Searched: "
            f"{outputs.describe_attempts(session_no, run_id=run_id, filename=name)}. "
            f"If the document was generated on an earlier deploy its file may have been "
            f"cleared — regenerate it, or copy it from the preview below."})
    return got


@app.get("/api/download/{session_no}")
def download(session_no: int, run_id: str | None = None, name: str | None = None,
             user: dict = Depends(current_user)):
    """Download the rendered .docx.

    `run_id` and `name` are optional but preferred: they identify the output exactly,
    whereas the session number alone has to be resolved against a curriculum that may
    have been re-synced since the doc was generated.
    """
    got = _resolve_output(session_no, run_id, name, user=user)
    if got.path is not None:
        return FileResponse(str(got.path), filename=got.filename, media_type=_DOCX_MIME)
    # Recovered from the DB because the instance disk no longer has it.
    return Response(
        content=got.read_bytes(), media_type=_DOCX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{got.filename}"'})


@app.get("/api/preview/{session_no}")
def preview(session_no: int, run_id: str | None = None, name: str | None = None,
            user: dict = Depends(current_user)):
    """The Markdown of a generated doc, resolved the same way as the download.

    The last-resort escape hatch: a reviewer whose .docx cannot be produced for ANY
    reason can still retrieve the full document as text rather than losing the work.
    The result payload carries this while the page is open; this survives a reload."""
    got = _resolve_output(session_no, run_id, name, kind="md", user=user)
    return {"session_no": session_no, "filename": got.filename,
            "markdown": got.read_bytes().decode("utf-8", "replace"),
            "source": got.source}


# Serve the built React frontend (Vite output) at the site root. This is mounted
# LAST so every /api/* and /admin route above is matched first; anything else
# (/, /assets/*, etc.) is served from frontend/dist. html=True returns index.html
# for the root. In local `npm run dev` the Vite server serves the UI instead, and
# this mount is simply unused. If frontend/dist is missing (never built), skip the
# mount so the API/admin still boot.
class _SpaStatic(StaticFiles):
    """StaticFiles that sets the cache policy a hashed-asset SPA needs.

    Starlette sends only etag/last-modified, no Cache-Control. With no explicit
    policy browsers apply HEURISTIC caching and may reuse index.html without
    revalidating — and since index.html is what names the content-hashed bundle, a
    stale copy keeps requesting the OLD JS/CSS. That is why a deploy could look like
    it "did not reflect" even though the server was already serving the new files
    (verified: the live bundle was byte-identical to the local build while the
    browser still rendered the previous UI).

      index.html (and any SPA fallback) -> no-cache: always revalidate. Cheap, because
        the ETag makes the revalidation a 304.
      /assets/<name>-<hash>.<ext>       -> immutable for a year: the filename changes
        whenever the content does, so it can never go stale.
    """

    async def get_response(self, path: str, scope):
        resp = await super().get_response(path, scope)
        p = (path or "").replace("\\", "/").lower()
        if p.startswith("assets/") and "-" in p.rsplit("/", 1)[-1]:
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            resp.headers["Cache-Control"] = "no-cache"
        return resp


_FRONTEND_DIST = config.ROOT / "frontend" / "dist"
if (_FRONTEND_DIST / "index.html").exists():
    app.mount("/", _SpaStatic(directory=str(_FRONTEND_DIST), html=True), name="frontend")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
