"""Start a real server, make real HTTP requests, and check what comes back.

    python -m evals.test_endpoints        # no API key needed, ~5 seconds

WHY THIS EXISTS. /api/guided/start — the one call that begins a document, so the entire
product is behind it — went out broken:

    AttributeError on /api/guided/start: 'GuidedStartBody' object has no attribute 'course'

Every suite passed. The Python tests exercise generation by calling the pipeline
directly and never post to the endpoint; the UI harness stubs the response, so the
frontend was tested against a server that only existed in the stub. Nothing anywhere
ran the actual handler, so a request field the handler read but the model never
declared was invisible until a person clicked the button.

So this runs the real app: uvicorn on a port, real JSON over real HTTP, the real
handler, a real database. Two things are faked, both deliberately:
  · AUTH — current_user is overridden, because signing in needs Google.
  · THE GENERATION THREAD — stubbed, because a real one costs money and minutes. It is
    stubbed AFTER the handler has fully run, so everything this is meant to catch
    (body parsing, curriculum lookup, budget resolution, the run row) is real.

The database is a throwaway under TR_DATA_DIR, seeded with a small curriculum, so this
never reads or writes anything of the user's.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Point the app at a disposable data dir BEFORE importing it — config.DATA_ROOT and the
# DB path are decided at import time.
TMP = tempfile.mkdtemp(prefix="tr_endpoint_test_")
os.environ["TR_DATA_DIR"] = TMP
os.environ.pop("TURSO_DATABASE_URL", None)      # never touch the cloud DB from a test
os.environ.pop("TURSO_AUTH_TOKEN", None)

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


import server                                    # noqa: E402
from src import db, course_loader                # noqa: E402

PORT = 8791
BASE = f"http://127.0.0.1:{PORT}/api"
USER = {"email": "tester@nxtwave.co.in", "name": "Tester", "is_admin": False}
COURSE = "Endpoint Test Course"
OTHER = "Some Other Course"

# --- fakes: auth, and the generation thread (see module docstring) ----------------
server.app.dependency_overrides[server.current_user] = lambda: USER
STARTED = []
server._guided_generate_all = lambda gid: STARTED.append(gid)

# --- a small curriculum for each of two courses, so "which course did it read?" is
#     an answerable question rather than a coincidence ------------------------------
db.init()
db.upsert_user(USER["email"], USER["name"])
for course, prefix in ((COURSE, "Right"), (OTHER, "Wrong")):
    for n in (30, 31, 32):
        db.curriculum_upsert(course, n, topic=f"{prefix} topic",
                             session_name=f"{prefix} session {n}",
                             key_takeaways=[f"{prefix} takeaway A for {n}",
                                            f"{prefix} takeaway B for {n}"])


def http(method, path, body=None):
    """Return (status, parsed_json). Never raises on a 4xx/5xx — the status is the
    thing under test."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw or "{}")
        except json.JSONDecodeError:
            return e.code, {"raw": raw}


def detail(payload):
    d = payload.get("detail", payload)
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


# --- boot the real server ---------------------------------------------------------
import uvicorn                                   # noqa: E402

cfg = uvicorn.Config(server.app, host="127.0.0.1", port=PORT, log_level="error")
srv = uvicorn.Server(cfg)
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.1)
else:
    print("server did not start"); sys.exit(1)

print("\n== the server answers ==")
st, _ = http("GET", "/health")
check("GET /health -> 200", st == 200, f"got {st}")

print("\n== POST /api/guided/start ==")
st, r = http("POST", "/guided/start",
             {"session_no": 32, "use_judge": True, "enforce_time": True,
              "team_id": None, "course": COURSE})
msg = detail(r)
# Named separately from the 200 check: an AttributeError here is a request-model bug,
# and saying so beats "expected 200, got 500".
check("no missing-attribute error", "has no attribute" not in msg, f"-> {msg}")
check("-> 200", st == 200, f"got {st}: {msg}")
gid = r.get("guided_id")
check("returns a guided_id", bool(gid), f"got {r}")

print("\n== the run reads the course the REQUEST named, not a global ==")
state = server.GUIDED.get(gid or "", {})
cur = state.get("cur")
check("session is the one asked for", getattr(cur, "number", None) == 32,
      f"got {getattr(cur, 'number', None)}")
check("content comes from the requested course",
      getattr(cur, "name", "").startswith("Right"), f"got {getattr(cur, 'name', None)!r}")
check("the run remembers its course", state.get("course") == COURSE,
      f"got {state.get('course')!r}")
check("generation was actually launched", STARTED == [gid], f"got {STARTED}")

print("\n== a second course at the same time gets ITS OWN sessions ==")
# The regression this guards: sessions were loaded from a process-wide "selected
# course", so two people generating at once got each other's curriculum.
st2, r2 = http("POST", "/guided/start", {"session_no": 32, "course": OTHER})
other = server.GUIDED.get(r2.get("guided_id", ""), {}).get("cur")
check("second run -> 200", st2 == 200, f"got {st2}: {detail(r2)}")
check("second run reads the other course",
      getattr(other, "name", "").startswith("Wrong"), f"got {getattr(other, 'name', None)!r}")
check("first run is unaffected", getattr(cur, "name", "").startswith("Right"))

print("\n== the run row is stamped with that course ==")
rows = {x["id"]: x for x in db.runs()}
check("run recorded", gid in rows, f"got {list(rows)}")
check("stamped with the requested course", rows.get(gid, {}).get("course") == COURSE,
      f"got {rows.get(gid, {}).get('course')!r}")

print("\n== a checkpointed run restores from the course it started on ==")
server._guided_save(gid)
snap = db.load_guided(gid)
check("course is checkpointed", snap.get("course") == COURSE, f"got {snap.get('course')!r}")

print("\n== finalize records the HUMAN approval ==")
# The dashboard read "Approved: 0" against seventeen finished documents, because the
# only approval it could see was the GRADERS' verdict — the reviewer's per-chunk ticks
# lived in React state and were never sent anywhere. Reaching finalize IS the approval:
# the button is disabled until every chunk is ticked.
_row = next((x for x in db.runs() if x["id"] == gid), {})
check("a fresh run is not approved yet", _row.get("approved") is False,
      f"got {_row.get('approved')}")
server.GUIDED[gid]["status"] = "reviewing"
server.GUIDED[gid]["chunks"] = [{"kind": "opening", "fragment": {}, "markdown": "x"}]
server._guided_finalize = lambda g: None          # don't assemble/grade/render here
st, r = http("POST", f"/guided/{gid}/finalize")
check("POST finalize -> 200", st == 200, f"got {st}: {detail(r)}")
_row = next((x for x in db.runs() if x["id"] == gid), {})
check("the run is now approved", _row.get("approved") is True, f"got {_row}")
check("…by the person who pressed it", _row.get("approved_by") == USER["email"],
      f"got {_row.get('approved_by')}")
check("while the run is still assembling, outcome stays 'running'",
      _row.get("outcome") == "running", f"got {_row.get('outcome')}")
# …and once it finishes, the outcome reflects the PERSON's sign-off, not the graders'.
# gates_passed=False here on purpose: that combination — approved by a human, still
# flagged by a grader — is the normal case, and is exactly what used to show as 0.
db.finish_run(gid, status="done", accepted=False)
_row = next((x for x in db.runs() if x["id"] == gid), {})
check("a finished, human-approved run reads 'approved'",
      _row.get("outcome") == "approved", f"got {_row.get('outcome')}")
check("…even though the graders did NOT pass it",
      _row.get("gates_passed") is False and _row.get("approved") is True, str(_row))
check("the graders' verdict is reported separately",
      "gates_passed" in _row, str(sorted(_row)[:12]))
st, hist = http("GET", "/my/history")
check("the history roll-up counts it",
      (hist.get("summary") or {}).get("approved_docs", 0) >= 1,
      str(hist.get("summary")))
check("…and reports gates separately",
      "gates_passed_docs" in (hist.get("summary") or {}), str(hist.get("summary")))

print("\n== other endpoints answer over HTTP ==")
for method, path in (("GET", "/status"), ("GET", "/workspaces"), ("GET", "/courses"),
                     ("GET", f"/curriculum?course={COURSE.replace(' ', '%20')}"),
                     ("GET", "/guided/resumable"), ("GET", "/my/teams"),
                     ("GET", "/my/history"), ("GET", "/learned-rules"),
                     ("GET", "/template-guide"), ("GET", "/bootstrap")):
    st, r = http(method, path)
    check(f"{method} {path} -> 2xx", 200 <= st < 300, f"got {st}: {detail(r)}")

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
