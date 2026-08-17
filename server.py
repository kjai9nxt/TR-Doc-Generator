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
                 outputs, llm)

app = FastAPI(title="TR Doc Generator API")

# Allow the Vite dev server (any localhost port) to call us during `npm run dev`.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1):\d+",
    allow_methods=["*"], allow_headers=["*"],
)

JOBS: dict[str, dict] = {}
GUIDED: dict[str, dict] = {}
_lock = threading.Lock()

# Load .env FIRST. src.db chooses its backend (local SQLite vs cloud Turso) from
# TURSO_DATABASE_URL on every call, so if .env were loaded lazily (by the first
# config lookup a request happens to make) the backend would flip mid-process: the
# schema created here would land in one database and later reads/writes in the other.
config.load_env()
db.init()   # create the schema on the configured backend + import the legacy JSON log
# On an ephemeral host (Render free + Turso) the disk is wiped on every restart,
# so bring the previously-synced knowledge base back from the DB. No-op locally.
try:
    _restored = db.kb_restore()
    if _restored:
        print(f"[startup] restored {_restored} knowledge-base file(s) from cloud storage")
except Exception as _e:
    print(f"[startup] knowledge-base restore skipped: {_e}")
# Retire learned rules that a deterministic gate now enforces (harness 1.29 turned
# three of them into hard guardrails). Idempotent, and needed at startup because the
# rule store is runtime data restored from the DB — a deployed instance would otherwise
# keep feeding those rules to the judge, which re-adjudicates them from prose and can
# fail a compliant doc on a violation that isn't there. See src/learning.retire_gated.
try:
    from src import learning as _learning
    _retired = _learning.retire_gated()
    if _retired:
        print(f"[startup] retired {_retired} learned rule(s) now enforced by a guardrail")
except Exception as _e:
    print(f"[startup] learned-rule retirement skipped: {_e}")
# Guided-run checkpoints are rehydrated lazily (on the first request for that id),
# not eagerly — we only drop the ones too old for anyone to come back to.
try:
    _purged = db.purge_guided(72)
    if _purged:
        print(f"[startup] purged {_purged} stale guided-run checkpoint(s)")
except Exception as _e:
    print(f"[startup] guided-checkpoint purge skipped: {_e}")


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class CurriculumRow(BaseModel):
    session_no: int
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


class RegenerateBody(BaseModel):
    index: int
    reason: str | None = None


class LoginBody(BaseModel):
    credential: str


class TeamCreateBody(BaseModel):
    name: str
    course: str | None = None


class MemberBody(BaseModel):
    email: str


class CourseBody(BaseModel):
    course: str


class FeedbackBody(BaseModel):
    session_no: int
    reason: str              # a plain-language correction; distilled into a durable rule


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


@app.post("/api/auth/login")
def auth_login(body: LoginBody):
    try:
        return auth.verify_credential(body.credential)
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
def _run_sync(job_id: str, course_link: str | None):
    def on_event(msg: str):
        with _lock:
            JOBS[job_id]["logs"].append(msg)
    try:
        res = sync.sync(course_link, verbose=True, on_event=on_event)
        with _lock:
            JOBS[job_id].update(status="done", result={
                "sessions": _session_list(),
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
    # Persist the course type + course name chosen at connect time so generation
    # (context_builder) can use them later.
    app_settings.save(course_type=body.course_type, course_name=body.course_name)
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None,
                        "error": None, "error_kind": None}
    threading.Thread(target=_run_sync,
                     args=(job_id, body.course_link), daemon=True).start()
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
def _course_for(user: dict, course: str | None) -> str:
    """Resolve which course a request is about.

    Explicit wins. The app_settings value is only a DEFAULT for a client that did not
    say — it is a single instance-wide setting, so treating it as the answer would mean
    one person switching course silently moved everybody else's.
    """
    return (course or "").strip() or app_settings.course_name() or "default"


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
    allowed = {c["name"] for c in db.courses_for_user(
        user.get("email"), is_admin=user.get("is_admin", False))}
    # A brand-new name is allowed (that is how a course is created); an EXISTING course
    # this user has no team for is not.
    if course in {c for c in db.curriculum_courses()} and allowed and course not in allowed:
        raise HTTPException(status_code=403, detail={
            "message": f"'{course}' belongs to a team you are not on. Ask an admin to "
                       f"add you to it."})
    app_settings.save(course_name=course, course_type=body.course_type)
    return {"course": course, "rows": _curriculum_rows(course),
            "sessions": _session_list(course), "imported_from": sync.last_link()}


def _curriculum_rows(course: str) -> list[dict]:
    """The course's rows, each tagged with whether its deck is REALLY held.

    Every response that returns rows goes through here. It used to be inlined in the
    GET only, so the POST and DELETE replies came back without `extracted` — and the
    dashboard, which renders "pending" whenever that flag is falsy, showed every single
    session as pending the moment you saved an edit. Nothing was being re-extracted;
    the rows had simply lost the field. One source, one shape, no drift.

    The flag is what the knowledge base ACTUALLY holds, not what the table believes: an
    ephemeral disk can lose the extracted text while the row still says "extracted",
    and that is exactly when a re-fetch is genuinely needed.
    """
    rows = db.curriculum(course)
    have_decks = {d["session_no"] for d in pptx_ingest.load_all_decks()
                  if d.get("session_no") is not None}
    for r in rows:
        r["extracted"] = r["session_no"] in have_decks
    return rows


@app.get("/api/curriculum")
def get_curriculum(course: str | None = None, user: dict = Depends(current_user)):
    course = _course_for(user, course)
    rows = _curriculum_rows(course)
    return {"course": course, "rows": rows,
            "imported_from": sync.last_link(),
            "pending": sum(1 for r in rows
                           if (r.get("ppt_link") or "") and not r["extracted"])}


@app.post("/api/curriculum")
def save_curriculum(body: CurriculumSaveBody, course: str | None = None,
                    user: dict = Depends(current_user)):
    """Create or update rows. Only the rows sent are touched — nothing is deleted."""
    course = _course_for(user, course or body.course)
    saved = 0
    for row in body.rows:
        ok = db.curriculum_upsert(
            course, row.session_no, topic=row.topic or "",
            session_name=row.session_name or "",
            key_takeaways=row.key_takeaways or [],
            ppt_link=row.ppt_link)
        saved += 1 if ok else 0
    # Keep the on-disk projection in step, so the offline loaders and the eval harness
    # see the edit without waiting for a sync.
    # Course memory follows the curriculum: a row whose link was just cleared must not
    # keep an extracted deck, or the session stays hidden from the generate list and the
    # writer goes on treating that deck as material already taught.
    sync.prune_orphan_decks(course)
    sync.write_course_cache(course)
    return {"saved": saved, "rows": _curriculum_rows(course)}


@app.delete("/api/curriculum/{session_no}")
def delete_curriculum_row(session_no: int, course: str | None = None,
                          user: dict = Depends(current_user)):
    course = _course_for(user, course)
    db.curriculum_delete(course, session_no)
    sync.prune_orphan_decks(course)
    sync.write_course_cache(course)
    return {"ok": True, "rows": _curriculum_rows(course)}


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
                           _course_for(user, body.course)), daemon=True).start()
    return {"job_id": job_id}


def _session_list(course: str | None = None):
    """Sessions that still need a TR doc.

    A session whose deck has been ingested has already been recorded, so it is course
    MEMORY rather than work to be done, and it is left out of the generate dropdown.
    (Briefly changed to list everything with an "already recorded" label, after a
    session appeared to vanish once a deck was attached to it — but that session had a
    deck precisely because one had just been pasted in, so the exclusion was doing its
    job. Reverted: the filter is the intended behaviour, and the dropdown stays a list
    of sessions that need writing.)
    """
    sessions = course_loader.load_sessions_from_cache()
    if not sessions:
        return []
    # WHAT COUNTS AS "already recorded" is the CURRICULUM's answer, not whatever deck
    # files happen to sit on disk. Reading the disk meant a deck extracted earlier kept
    # a session out of this list even after its link had been removed from the row —
    # the row said "no deck" while the session stayed invisible, with no way to put it
    # back. The curriculum is the source of truth, so it decides here too; the disk scan
    # remains only as the fallback for a process with no curriculum rows (offline evals).
    course = (course or "").strip() or app_settings.course_name() or "default"
    rows = db.curriculum(course)
    if rows:
        have_decks = {r["session_no"] for r in rows if (r.get("ppt_link") or "").strip()}
    else:
        have_decks = {d["session_no"] for d in pptx_ingest.load_all_decks()
                      if d.get("session_no") is not None}
    return [{"number": s.number, "name": s.name, "takeaways": s.key_takeaways}
            for s in sessions if s.number not in have_decks]


@app.get("/api/sessions")
def sessions(course: str | None = None):
    return {"sessions": _session_list(course)}


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
    "last_error", "user_email",
    # Persisted so an unfinished run can NAME itself in the resume list without the
    # server loading its whole state or re-deriving the title from a course that may
    # have been re-synced since (see db.unfinished_guided).
    "session_title",
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
        sessions = course_loader.load_sessions(None)
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
        cur, index - 1, slides_used=used, sections_left=left,
        enforce_time=state.get("enforce_time", True))


def _chunk_allowance(state: dict, index: int) -> int:
    """The slide allowance the instruction for `index` states (for the over-budget log)."""
    used, left = _slide_budget_state(state, index)
    return context_builder.chunk_slide_allowance(
        state["cur"], slides_used=used, sections_left=left,
        enforce_time=state.get("enforce_time", True))


def _gen_one(gid: str, index: int, prior: list[dict], reason: str | None = None) -> dict:
    """Generate one chunk given the prior chunks' fragments (for consistency)."""
    kind, instruction = _chunk_spec(GUIDED[gid], index)
    approved_json = json.dumps(prior, ensure_ascii=False) if prior else ""
    fragment = generator.generate_chunk(
        GUIDED[gid]["base_context"], instruction, approved_json, reason)
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
        ceiling = context_builder.slide_ceiling(state.get("enforce_time", True))
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
            _guided_log(gid, f"Generating chunk {i + 1}/{total}: {GUIDED[gid]['labels'][i]} …")
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
    patch = generator.generate_patch(base_context, kind, prev_fragment, reason)
    fragment, summary = patcher.apply(kind, prev_fragment, patch)
    markdown = docx_writer.chunk_to_markdown(kind, fragment)
    return {"kind": kind, "fragment": fragment, "markdown": markdown}, summary


def _guided_regenerate(gid: str, index: int, reason: str):
    """Regenerate a single chunk in place (given the chunks before it) during review."""
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
            learning.record_feedback(session_no, reason, source="regeneration")
        except Exception:
            pass
        _guided_log(gid, f"Regenerating chunk {index + 1}: {GUIDED[gid]['labels'][index]} …")
        with _lock:
            allowance = _chunk_allowance(GUIDED[gid], index) if index else 0

        # PATCH FIRST. A reviewer note is almost always narrow, and re-drafting the whole
        # chunk from it threw away the slides they were happy with. A patch touches only
        # what it names, so the rest is not merely "asked to stay the same" — it is never
        # regenerated at all.
        rcfg = config.harness().get("regeneration", {}) or {}
        chunk = scope = None
        if rcfg.get("mode", "patch") == "patch":
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
            GUIDED[gid]["status"] = "reviewing"
            GUIDED[gid]["regen_index"] = None
        _guided_record_cost(gid)      # a regeneration is real spend on this run
        _guided_log(gid, "Chunk updated.")
        # A patch may ADD slides, and a full re-draft is a fresh roll of the dice on the
        # count, so re-check this chunk against the ceiling exactly as generation does.
        _guided_slide_budget_note(gid, index, allowance)
        with _lock:
            _frag = (GUIDED.get(gid, {}).get("chunks") or [{}])[index].get("fragment") \
                if index < len(GUIDED.get(gid, {}).get("chunks") or []) else None
        _guided_repetition_note(gid, index, _frag)
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
        opening = chunks[0]["fragment"]
        sections = [c["fragment"].get("section", c["fragment"]) for c in chunks[1:]]
        # Each takeaway chunk also reports the sub-concepts it covers and the slide
        # teaching each one; assemble_doc folds those into the doc-level coverage_map
        # (remapping slide numbers, since assembly renumbers the document).
        coverage = [c["fragment"].get("coverage") or {} for c in chunks[1:]]
        doc = pipeline.assemble_doc(cur, nxt, opening, sections, coverage)
        result = pipeline.finalize(session_no, doc, use_judge=use_judge,
                                   enforce_time=enforce_time, run_id=gid,
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
    from guardrails.guardrails import _norm_tokens
    import re as _re
    c = config.harness()["constraints"].get("content", {})
    if not c.get("no_bullet_echoes_lead_in", False):
        return []
    thr = float(c.get("bullet_echo_overlap", 0.5))
    out = []
    section = (fragment or {}).get("section") or {}
    for s in section.get("slides") or []:
        blocks = s.get("content") or []
        clauses = [x for b in blocks if b.get("type") == "text"
                   for x in _re.split(r"[;.!?]", str(b.get("text") or ""))
                   if len(_norm_tokens(x)) >= 3]
        if not clauses:
            continue
        for b in blocks:
            if b.get("type") != "bullets":
                continue
            for it in b.get("items") or []:
                bt = _norm_tokens(it)
                if len(bt) < 3:
                    continue
                best = 0.0
                for cl in clauses:
                    shared = bt & _norm_tokens(cl)
                    if len(shared) >= 2:
                        best = max(best, len(shared) / len(bt))
                if best >= thr:
                    out.append(f"Slide {s.get('n', '?')} · {best:.0%} of \"{str(it)[:60]}\" "
                               f"is already in the paragraph above it")
    return out


def _guided_view(state: dict) -> dict:
    """JSON-safe snapshot (Session objects and base_context are kept server-side)."""
    labels = state["labels"]
    chunks = [{"label": labels[i], "markdown": c["markdown"],
               "repetition": _chunk_repetition(c.get("fragment"))}
              for i, c in enumerate(state["chunks"])]
    return {
        "status": state["status"],
        # Which session this run is for. Needed when a run is resumed from the server's
        # list on a browser that never started it: the page has to move the session
        # selector to the run being resumed, and it cannot know the number otherwise.
        "session_no": state.get("session_no"),
        "session_title": state.get("session_title"),
        "index": state["index"],
        "total": state["total"],
        "enforce_time": state.get("enforce_time", True),
        "labels": labels,
        "chunks": chunks,
        "regen_index": state.get("regen_index"),
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
    sessions = course_loader.load_sessions(None)
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
    base_context = (context_builder.build_guided_base(prev, cur, nxt)
                    + context_builder.time_mode_block(body.enforce_time, guided=True))
    with _lock:
        GUIDED[gid] = {
            "status": "generating_all", "session_no": body.session_no,
            "session_title": cur.name,
            "prev": prev, "cur": cur, "nxt": nxt,
            "base_context": base_context,
            "total": 1 + len(cur.key_takeaways), "index": 0, "labels": labels,
            "chunks": [], "regen_index": None, "use_judge": body.use_judge,
            "enforce_time": body.enforce_time, "user_email": user.get("email"),
            "logs": [], "result": None, "error": None,
        }
    # Checkpoint before any chunk is generated, so even a restart during the very
    # first LLM call leaves a resumable run rather than an orphaned id.
    _guided_save(gid)
    # Record the run in the DB up-front (status=running) so guided generations
    # show live and persist in the dashboard, exactly like one-shot runs.
    try:
        email = user.get("email")
        course = app_settings.course_name()
        db.create_run(gid, user_email=email, course=course,
                      team_id=db.team_for_user_course(email, course),
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


@app.get("/api/guided/{gid}")
def guided_state(gid: str):
    state = _guided_require(gid)      # restores from the checkpoint if needed
    with _lock:
        return _guided_view(state)


@app.post("/api/guided/{gid}/regenerate")
def guided_regenerate(gid: str, body: RegenerateBody):
    state = _guided_require(gid)
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
    threading.Thread(target=_guided_regenerate, args=(gid, body.index, reason),
                     daemon=True).start()
    return {"ok": True}


@app.post("/api/guided/{gid}/finalize")
def guided_finalize(gid: str):
    state = _guided_require(gid)
    with _lock:
        if state["status"] != "reviewing":
            raise HTTPException(
                status_code=409,
                detail=f"Another step ({state['status']}) is still running — "
                       f"wait for it to finish, then create the final doc.")
        if not state.get("chunks"):
            raise HTTPException(status_code=409,
                                detail="This run has no generated chunks to assemble.")
        state["status"] = "assembling"
        state["last_error"] = None
    _guided_save(gid)
    threading.Thread(target=_guided_finalize, args=(gid,), daemon=True).start()
    return {"ok": True}


@app.get("/api/extraction-check")
def extraction_check():
    return pptx_ingest.completeness_report()


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
    try:
        learning.record_feedback(body.session_no, reason, source="feedback")
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
    approved = [r for r in runs if r.get("accepted")]
    return {
        "total_runs": len(runs),
        "approved_docs": len(approved),
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
    email = user.get("email")
    out = []
    for t in db.teams_for_user(email):
        members = t.get("members", [])
        course = t.get("course")
        runs = db.runs(course=course) if course else db.runs(team_id=t["id"])
        # A team with no course set can only be identified by the stamp, so that case
        # keeps the old behaviour rather than showing nothing.
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
        warns = len(pptx_ingest.completeness_report().get("decks", []))
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


# ---- team management (admin-managed) ----
@app.get("/api/admin/teams")
def admin_list_teams(user: dict = Depends(require_admin)):
    return {"teams": db.teams(), "users": [u["email"] for u in db.users()]}


@app.post("/api/admin/teams")
def admin_create_team(body: TeamCreateBody, user: dict = Depends(require_admin)):
    tid = db.create_team(body.name, body.course, user.get("email"))
    return {"id": tid}


@app.post("/api/admin/teams/{team_id}/members")
def admin_add_member(team_id: int, body: MemberBody, user: dict = Depends(require_admin)):
    db.add_member(team_id, body.email.strip().lower())
    return {"ok": True}


@app.delete("/api/admin/teams/{team_id}/members/{email}")
def admin_remove_member(team_id: int, email: str, user: dict = Depends(require_admin)):
    db.remove_member(team_id, email.strip().lower())
    return {"ok": True}


@app.post("/api/admin/teams/{team_id}/course")
def admin_set_course(team_id: int, body: CourseBody, user: dict = Depends(require_admin)):
    db.set_team_course(team_id, body.course)
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
    got = _resolve_output(session_no, body.run_id, body.name)
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
                    kind: str = "docx"):
    """Locate a run's rendered output, or raise a 404 that says what was searched.

    Every download path goes through here. It never re-derives the filename from the
    currently-synced course as its first move — that is what made Download and Create
    Google Doc both fail on a finished document (see src/outputs.py).
    """
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
    got = _resolve_output(session_no, run_id, name)
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
    got = _resolve_output(session_no, run_id, name, kind="md")
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
