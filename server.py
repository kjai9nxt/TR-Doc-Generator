"""FastAPI backend for the TR Doc Generator React frontend.

Run:
    cd "/home/nxtwave/Desktop/TR Doc Generator"
    python server.py                 # serves the API on http://localhost:8000

Endpoints:
    GET  /api/status                 -> provider / model / key status
    GET  /api/template-guide         -> markdown of the required sheet templates
    POST /api/sync                    -> validate + sync both sheets (returns changelog/sessions)
    GET  /api/sessions               -> synced session list
    POST /api/generate                -> start a generation job -> {job_id}
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
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src import (config, sheets, sync, course_loader, pipeline, pptx_ingest,
                 context_builder, generator, docx_writer, app_settings, auth, db)

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

db.init()   # create the SQLite schema + one-time import of the legacy JSON log


# --------------------------------------------------------------------------- #
# models
# --------------------------------------------------------------------------- #
class SyncBody(BaseModel):
    course_link: str
    details_link: str
    reference_date: str | None = None      # recency baseline (default today)
    course_type: str | None = None         # "semester" | "interview"
    course_name: str | None = None         # grouping label for runs/teams


class GenerateBody(BaseModel):
    session_no: int
    use_judge: bool = True
    enforce_time: bool = True


class EvalSetsBody(BaseModel):
    session_no: int
    use_llm: bool = True
    enforce_time: bool = True


class GuidedStartBody(BaseModel):
    session_no: int
    use_judge: bool = True


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


class GdocBody(BaseModel):
    access_token: str        # short-lived Google Drive token from the frontend (GIS)


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
    m = config.harness()["model"]
    c, d = sync.last_links()
    return {
        "provider": m.get("provider"),
        "model": m.get("generator"),
        "key_ok": config.api_key() is not None,
        "saved_links": {"course": c, "details": d},
        "settings": app_settings.load(),
        "version": config.harness()["meta"]["version"],
    }


@app.get("/api/template-guide")
def template_guide():
    return {"markdown": sheets.guide_text()}


# --------------------------------------------------------------------------- #
# sync
# --------------------------------------------------------------------------- #
def _run_sync(job_id: str, course_link: str, details_link: str):
    def on_event(msg: str):
        with _lock:
            JOBS[job_id]["logs"].append(msg)
    try:
        res = sync.sync(course_link, details_link, verbose=True, on_event=on_event)
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
    # Persist the recency date + course type chosen at connect time so generation
    # (context_builder) can use them later.
    app_settings.save(reference_date=body.reference_date, course_type=body.course_type,
                      course_name=body.course_name)
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None,
                        "error": None, "error_kind": None}
    threading.Thread(target=_run_sync,
                     args=(job_id, body.course_link, body.details_link), daemon=True).start()
    return {"job_id": job_id}


def _session_list():
    # Only expose sessions that came from a real sheet SYNC (the synced cache).
    # Never fall back to the offline sample xlsx here — that would mislead the UI.
    sessions = course_loader.load_sessions_from_cache()
    if not sessions:
        return []
    # Sessions that already have an ingested PPT deck are PAST sessions (memory) —
    # exclude them from the dropdown; only offer sessions still needing a TR doc.
    have_decks = {d["session_no"] for d in pptx_ingest.load_all_decks()
                  if d.get("session_no") is not None}
    return [{"number": s.number, "name": s.name, "takeaways": s.key_takeaways}
            for s in sessions if s.number not in have_decks]


@app.get("/api/sessions")
def sessions():
    return {"sessions": _session_list()}


# --------------------------------------------------------------------------- #
# generate (background job + polling)
# --------------------------------------------------------------------------- #
def _run_generation(job_id: str, session_no: int, use_judge: bool, enforce_time: bool,
                    user_email: str | None = None):
    def on_event(msg: str):
        with _lock:
            JOBS[job_id]["logs"].append(msg)
        try:
            db.update_stage(job_id, msg.strip()[:120])   # live stage for the admin view
        except Exception:
            pass
    try:
        result = pipeline.run(session_no, use_judge=use_judge, do_sync=False,
                              enforce_time=enforce_time, on_event=on_event, user=user_email)
        final = result["history"][-1]
        cost = result.get("cost") or {}
        try:
            db.finish_run(
                job_id, status="done", accepted=final.get("accepted"),
                rubric=(final.get("judge") or {}).get("weighted_total"),
                est_minutes=final.get("time", {}).get("estimated_minutes"),
                rounds=len(result.get("history", [])),
                slides=final.get("time", {}).get("slide_count"),
                cost=cost.get("totals"), calls=cost.get("calls"),
                docx_path=result.get("docx"))
        except Exception:
            pass
        with _lock:
            JOBS[job_id].update(status="done", result={
                "session_no": session_no,
                "accepted": final["accepted"],
                "time": final["time"],
                "judge": final.get("judge"),
                "issues": final.get("issues", []),
                "docx_name": Path(result["docx"]).name,
                "markdown": _read_markdown(result["docx"]),
                "cost": result.get("cost"),
            })
    except Exception as e:
        try:
            db.finish_run(job_id, status="error", error=str(e))
        except Exception:
            pass
        with _lock:
            JOBS[job_id].update(status="error", error=str(e))


def _read_markdown(docx_path: str) -> str:
    md = Path(docx_path).with_suffix(".md")
    return md.read_text(encoding="utf-8") if md.exists() else ""


@app.post("/api/generate")
def generate(body: GenerateBody, user: dict = Depends(current_user)):
    if config.api_key() is None:
        raise HTTPException(status_code=400, detail={"message": "No API key configured in .env"})
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        JOBS[job_id] = {"status": "running", "logs": [], "result": None, "error": None}
    # Record the run in the DB up-front (status=running) so it shows live, with
    # the user's course + team attribution.
    try:
        email = user.get("email")
        course = app_settings.course_name()
        try:
            title = course_loader.get_session(body.session_no).name
        except Exception:
            title = f"Session {body.session_no}"
        db.create_run(job_id, user_email=email, course=course,
                      team_id=db.team_for_user_course(email, course),
                      session_no=body.session_no, title=title,
                      enforce_time=body.enforce_time)
    except Exception:
        pass
    threading.Thread(target=_run_generation,
                     args=(job_id, body.session_no, body.use_judge, body.enforce_time,
                           user.get("email")),
                     daemon=True).start()
    return {"job_id": job_id}


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


def _chunk_spec(state: dict, index: int):
    """(kind, instruction) for the chunk at `index`: 0 = opening, else takeaway."""
    cur, prev = state["cur"], state["prev"]
    if index == 0:
        return "opening", context_builder.opening_instruction(cur, prev)
    return "section", context_builder.takeaway_instruction(cur, index - 1)


def _gen_one(gid: str, index: int, prior: list[dict], reason: str | None = None) -> dict:
    """Generate one chunk given the prior chunks' fragments (for consistency)."""
    kind, instruction = _chunk_spec(GUIDED[gid], index)
    approved_json = json.dumps(prior, ensure_ascii=False) if prior else ""
    fragment = generator.generate_chunk(
        GUIDED[gid]["base_context"], instruction, approved_json, reason)
    markdown = docx_writer.chunk_to_markdown(kind, fragment)
    return {"kind": kind, "fragment": fragment, "markdown": markdown}


def _guided_generate_all(gid: str):
    """Generate every chunk up front, then move to the review phase."""
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
            chunk = _gen_one(gid, i, prior)
            with _lock:
                GUIDED[gid]["chunks"].append(chunk)
                GUIDED[gid]["index"] = len(GUIDED[gid]["chunks"])
        with _lock:
            GUIDED[gid]["status"] = "reviewing"
        _guided_log(gid, "All chunks generated — review each, then create the final doc.")
    except Exception as e:
        with _lock:
            GUIDED[gid].update(status="error", error=str(e))


def _guided_regenerate(gid: str, index: int, reason: str):
    """Regenerate a single chunk in place (given the chunks before it) during review."""
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
        chunk = _gen_one(gid, index, prior, reason)
        # Log the before/reason/after so the feedback_regeneration_adherence eval can score it.
        try:
            from src import regen_log
            regen_log.record(session_no, reason, before_md, chunk["markdown"])
        except Exception:
            pass
        with _lock:
            GUIDED[gid]["chunks"][index] = chunk
            GUIDED[gid]["status"] = "reviewing"
            GUIDED[gid]["regen_index"] = None
        _guided_log(gid, "Chunk updated.")
    except Exception as e:
        with _lock:
            GUIDED[gid].update(status="error", error=str(e), regen_index=None)


def _guided_finalize(gid: str):
    """Assemble all chunks, grade once, render the final .docx."""
    try:
        with _lock:
            state = GUIDED[gid]
            chunks = state["chunks"]
            cur, nxt, session_no = state["cur"], state["nxt"], state["session_no"]
            use_judge = state["use_judge"]
        opening = chunks[0]["fragment"]
        sections = [c["fragment"].get("section", c["fragment"]) for c in chunks[1:]]
        doc = pipeline.assemble_doc(cur, nxt, opening, sections)
        result = pipeline.finalize(session_no, doc, use_judge=use_judge,
                                   on_event=lambda m: _guided_log(gid, m))
        final = result["history"][-1]
        with _lock:
            GUIDED[gid].update(status="done", result={
                "session_no": session_no,
                "accepted": final["accepted"],
                "time": final["time"],
                "judge": final.get("judge"),
                "issues": final.get("issues", []),
                "docx_name": Path(result["docx"]).name,
                "markdown": _read_markdown(result["docx"]),
                "cost": result.get("cost"),
            })
    except Exception as e:
        with _lock:
            GUIDED[gid].update(status="error", error=str(e))


def _guided_view(state: dict) -> dict:
    """JSON-safe snapshot (Session objects and base_context are kept server-side)."""
    labels = state["labels"]
    chunks = [{"label": labels[i], "markdown": c["markdown"]}
              for i, c in enumerate(state["chunks"])]
    return {
        "status": state["status"],
        "index": state["index"],
        "total": state["total"],
        "labels": labels,
        "chunks": chunks,
        "regen_index": state.get("regen_index"),
        "result": state.get("result"),
        "error": state.get("error"),
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
    with _lock:
        GUIDED[gid] = {
            "status": "generating_all", "session_no": body.session_no,
            "prev": prev, "cur": cur, "nxt": nxt,
            "base_context": context_builder.build_guided_base(prev, cur, nxt),
            "total": 1 + len(cur.key_takeaways), "index": 0, "labels": labels,
            "chunks": [], "regen_index": None, "use_judge": body.use_judge,
            "logs": [], "result": None, "error": None,
        }
    threading.Thread(target=_guided_generate_all, args=(gid,), daemon=True).start()
    return {"guided_id": gid}


@app.get("/api/guided/{gid}")
def guided_state(gid: str):
    with _lock:
        state = GUIDED.get(gid)
        if not state:
            raise HTTPException(status_code=404, detail="Unknown guided session")
        return _guided_view(state)


@app.post("/api/guided/{gid}/regenerate")
def guided_regenerate(gid: str, body: RegenerateBody):
    with _lock:
        state = GUIDED.get(gid)
        if not state:
            raise HTTPException(status_code=404, detail="Unknown guided session")
        if state["status"] != "reviewing":
            raise HTTPException(status_code=409, detail="Not in the review phase.")
        if not (0 <= body.index < len(state["chunks"])):
            raise HTTPException(status_code=400, detail="Chunk index out of range.")
    reason = (body.reason or "").strip()
    if not reason:
        raise HTTPException(status_code=400,
                            detail="A reason is required to regenerate a chunk.")
    with _lock:
        state["status"] = "regenerating"
        state["regen_index"] = body.index
    threading.Thread(target=_guided_regenerate, args=(gid, body.index, reason),
                     daemon=True).start()
    return {"ok": True}


@app.post("/api/guided/{gid}/finalize")
def guided_finalize(gid: str):
    with _lock:
        state = GUIDED.get(gid)
        if not state:
            raise HTTPException(status_code=404, detail="Unknown guided session")
        if state["status"] != "reviewing":
            raise HTTPException(status_code=409, detail="Not in the review phase.")
        state["status"] = "assembling"
    threading.Thread(target=_guided_finalize, args=(gid,), daemon=True).start()
    return {"ok": True}


@app.get("/api/extraction-check")
def extraction_check():
    return pptx_ingest.completeness_report()


@app.get("/api/learned-rules")
def learned_rules(user: dict = Depends(current_user)):
    from src import learning
    return {"rules": learning.rules()}


@app.delete("/api/learned-rules")
def clear_learned_rules(user: dict = Depends(require_admin)):
    from src import learning
    learning._save({"rules": []})
    return {"ok": True}


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
    """Teams the user belongs to, each with the docs the team is building together
    (all members' runs) grouped by course."""
    email = user.get("email")
    out = []
    for t in db.teams_for_user(email):
        members = t.get("members", [])
        team_runs = [r for r in db.runs(team_id=t["id"])]
        # also fold in members' runs for the team's course (belt-and-suspenders)
        out.append({"team": t, "courses": _group_by_course(team_runs),
                    "summary": _rollup(team_runs), "members": members})
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
    return {"runs": db.runs(course=course, user_email=user_email, status=status)}


@app.get("/api/admin/live")
def admin_live(user: dict = Depends(require_admin)):
    return {"live": db.live_runs()}


@app.get("/api/admin/users")
def admin_users(user: dict = Depends(require_admin)):
    return {"users": db.users(), "per_user": db.per_user()}


def _connectors() -> list:
    """Health of the external integrations the pipeline depends on."""
    m = config.harness()["model"]
    c, d = sync.last_links()
    try:
        warns = len(pptx_ingest.completeness_report().get("decks", []))
    except Exception:
        warns = None
    return [
        {"name": "LLM provider", "detail": f"{m.get('provider')} · {m.get('generator')}",
         "ok": config.api_key() is not None},
        {"name": "Judge model", "detail": m.get("judge"), "ok": config.api_key() is not None},
        {"name": "Curriculum Sheet", "detail": "linked" if c else "not linked", "ok": bool(c)},
        {"name": "Session Details Sheet", "detail": "linked" if d else "not linked", "ok": bool(d)},
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
    token, so the user owns it and is the only editor — edit access is theirs alone."""
    out = config.harness()["output"]
    s = course_loader.get_session(session_no)
    fname = out["docx_filename"].format(N=s.number, SessionName=s.name).replace("/", "-")
    path = config.DATA_ROOT / out["dir"] / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail={"message": "Generate the doc first."})
    from src import gdrive
    try:
        res = gdrive.upload_as_gdoc(path, f"Session {s.number} _ {s.name}", body.access_token)
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


@app.get("/api/download/{session_no}")
def download(session_no: int, user: dict = Depends(current_user)):
    out = config.harness()["output"]
    s = course_loader.get_session(session_no)
    fname = out["docx_filename"].format(N=s.number, SessionName=s.name).replace("/", "-")
    path = config.DATA_ROOT / out["dir"] / fname
    if not path.exists():
        raise HTTPException(status_code=404, detail="Generate the doc first.")
    return FileResponse(
        str(path), filename=fname,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
