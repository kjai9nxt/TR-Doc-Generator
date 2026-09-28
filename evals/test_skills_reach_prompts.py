"""Course skills and session skills reach EVERY prompt that writes or grades a document.

    python -m evals.test_skills_reach_prompts     # no API key needed, ~5 seconds

WHY THIS EXISTS. Skills are the foundation of the generator: a course's owner and team
write down how the course is taught, and every document is written and graded under it.
The routing is spread over half a dozen call sites — guided chunks, chunk regeneration,
the finalize repair (patch and full re-draft), one-shot generation and revision, and the
judge — and a site that forgets to pass the course or the session drops the skills
SILENTLY: the document still generates, it just ignores the brief.

So this captures the actual text sent to the model on each path and checks, by unique
marker, that:
  · an approved COURSE skill reaches every session;
  · an approved SESSION skill reaches its own session and no other;
  · a draft and a retired skill reach nothing;
  · a session skill promoted to the course reaches every session afterwards;
  · a skill approved in the MIDDLE of a guided run reaches the next chunk.

Everything runs against a throwaway database with the model stubbed out.
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

TMP = tempfile.mkdtemp(prefix="tr_skills_prompts_test_")
os.environ["TR_DATA_DIR"] = TMP
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)
os.environ.setdefault("OPENROUTER_API_KEY", "test-key-not-used")

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
from src import db, llm, generator, pipeline, course_loader   # noqa: E402
from src import skills as skill_rules             # noqa: E402
from graders import skill_report                  # noqa: E402

# --- the model, stubbed: record what it was sent, answer with an empty object --------
CALLS: list[dict] = []


def fake_complete(system, user, *, model, max_tokens, temperature, retries=3,
                  label="", system_extra="", cached_context=""):
    CALLS.append({"label": label,
                  "text": "\n".join([system or "", system_extra or "",
                                     cached_context or "", user or ""])})
    return "{}"


llm.complete = fake_complete
skill_rules.articulate = lambda text, model=None: None

OWNER = {"email": "owner@nxtwave.co.in", "name": "Owner", "is_admin": False}
CURRENT = dict(OWNER)
server.app.dependency_overrides[server.current_user] = lambda: dict(CURRENT)
COURSE = "PromptCourse"

db.init()
db.upsert_user(OWNER["email"], OWNER["name"], False)

PORT = 8795
BASE = f"http://127.0.0.1:{PORT}/api"


def http(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


import uvicorn                                   # noqa: E402

srv = uvicorn.Server(uvicorn.Config(server.app, host="127.0.0.1", port=PORT,
                                    log_level="error"))
threading.Thread(target=srv.run, daemon=True).start()
for _ in range(100):
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/health", timeout=1).read()
        break
    except Exception:
        time.sleep(0.1)
else:
    print("server did not start"); sys.exit(1)

http("POST", "/courses/select", {"course": COURSE})
st, _ = http("POST", "/curriculum", {"course": COURSE, "rows": [
    {"session_no": n, "topic": "T", "session_name": f"Session {n} topic",
     "key_takeaways": [f"takeaway {n}a", f"takeaway {n}b"]} for n in (1, 2, 3)]})
assert st == 200, st


def add(text, *, scope="course", session=None, approve=True):
    st, r = http("POST", "/skills", {"course": COURSE, "text": text, "scope": scope,
                                     "session": session})
    assert st == 200, (st, r)
    if approve:
        st, _ = http("POST", f"/skills/{r['id']}/approve?course={COURSE}")
        assert st == 200, st
    return r["id"]


COURSE_M = "MARKER_COURSE_ALPHA use rupee prices in every example."
S2_M = "MARKER_SESSION2_BETA open session two with a live demo."
S3_M = "MARKER_SESSION3_GAMMA close session three with a quiz."
DRAFT_M = "MARKER_DRAFT_DELTA never approved."
RETIRED_M = "MARKER_RETIRED_EPSILON approved then retired."
PROMO_M = "MARKER_PROMOTED_ZETA written for session two, then lifted to the course."

add(COURSE_M)
add(S2_M, scope="session", session=2)
add(S3_M, scope="session", session=3)
add(DRAFT_M, approve=False)
rid = add(RETIRED_M)
http("DELETE", f"/skills/{rid}?course={COURSE}")
pid = add(PROMO_M, scope="session", session=2)

MARKS = {"course": "MARKER_COURSE_ALPHA", "s2": "MARKER_SESSION2_BETA",
         "s3": "MARKER_SESSION3_GAMMA", "draft": "MARKER_DRAFT_DELTA",
         "retired": "MARKER_RETIRED_EPSILON", "promo": "MARKER_PROMOTED_ZETA"}


def seen(text):
    return {k for k, m in MARKS.items() if m in text}


def expect(path, text, want):
    got = seen(text)
    check(f"{path}: carries exactly {sorted(want)}", got == set(want),
          f"got {sorted(got)}")


sessions = course_loader.load_sessions(None, course=COURSE)
prev, cur2, nxt = course_loader.neighbours(2, sessions)
_, cur3, _ = course_loader.neighbours(3, sessions)
DOC = {"session_no": 2, "session_title": "x", "recap": {}, "agenda": {},
       "sections": [], "key_takeaways": [], "upcoming_session": {}, "closing": {}}


def captured(fn):
    CALLS.clear()
    try:
        fn()
    except Exception:
        pass                    # the stub's "{}" is not a document; the prompt is what counts
    return "\n".join(c["text"] for c in CALLS), len(CALLS)


print("\n== every writing path, session 2 ==")
S2 = {"course", "s2", "promo"}
for name, fn in [
    ("guided chunk", lambda: generator.generate_chunk("BASE", "write the chunk",
                                                       course=COURSE, session=2)),
    ("chunk regeneration patch", lambda: generator.generate_patch(
        "BASE", "section", {"slides": []}, "tighten it", course=COURSE, session=2)),
    ("finalize repair patch", lambda: generator.repair_patch(
        json.dumps(DOC), ["too long"], base_context="BASE", course=COURSE, session=2)),
    ("finalize full re-draft", lambda: generator.revise(
        "PROMPT", json.dumps(DOC), ["too long"], course=COURSE, session=2)),
    ("one-shot generation", lambda: generator.generate("PROMPT", course=COURSE,
                                                        session=2)),
]:
    text, n = captured(fn)
    check(f"{name}: the model was called", n > 0, f"{n} calls")
    expect(name, text, S2)

print("\n== the repair prompt finalize builds carries them too ==")
blk = pipeline.context_builder.course_skills_block(COURSE, 2)
expect("course_skills_block(session 2)", blk, S2)

print("\n== the judge grades against the same set ==")
text, n = captured(lambda: pipeline.evaluate(
    DOC, cur2, False, False, use_judge=True, enforce_time=False, budgets=None,
    course=COURSE, profile=None))
judge = "\n".join(c["text"] for c in CALLS if c["label"].startswith("judge"))
check("the judge was called", bool(judge), str([c["label"] for c in CALLS]))
expect("judge prompt (session 2)", judge, S2)
rows = skill_report.build(DOC, course=COURSE, session=cur2).get("skills") or []
expect("skill report rows (session 2)", " ".join(r["text"] for r in rows), S2)

print("\n== a different session gets its own, not session 2's ==")
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=3))
expect("guided chunk, session 3", text, {"course", "s3"})
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=1))
expect("guided chunk, session 1", text, {"course"})

print("\n== promoting a session skill makes it reach every session ==")
st, _ = http("POST", f"/skills/{pid}/promote?course={COURSE}")
check("promote -> 200", st == 200, f"got {st}")
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=3))
expect("guided chunk, session 3, after promotion", text, {"course", "s3", "promo"})
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=1))
expect("guided chunk, session 1, after promotion", text, {"course", "promo"})

print("\n== the real guided run, end to end over HTTP ==")
# The server's own path — /guided/start, its generation thread, _gen_one — not the
# generator functions called by hand. The stub's empty answer makes each chunk fail, so
# only the first model call is inspected: that is the prompt the writer was given.
CALLS.clear()
st, r = http("POST", "/guided/start", {"session_no": 2, "course": COURSE,
                                       "enforce_time": False})
check("POST /guided/start -> 200", st == 200, f"got {st}: {r}")
for _ in range(100):
    if any(c["label"].startswith("generate_chunk") for c in CALLS):
        break
    time.sleep(0.1)
chunk_calls = [c for c in CALLS if c["label"].startswith("generate_chunk")]
check("the guided thread called the writer", bool(chunk_calls),
      str([c["label"] for c in CALLS]))
if chunk_calls:
    expect("guided run chunk prompt (session 2)", chunk_calls[0]["text"],
           {"course", "s2", "promo"})

print("\n== a skill approved DURING a run reaches the next chunk ==")
# The base context is frozen at /guided/start; skills are read per call so an edit made
# while the run is in review still governs what is written next.
late = add("MARKER_LATE_ETA approved mid-run.", scope="session", session=2,
           approve=False)
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=2))
check("…a draft does not", "MARKER_LATE_ETA" not in text)
http("POST", f"/skills/{late}/approve?course={COURSE}")
text, _ = captured(lambda: generator.generate_chunk("BASE", "write", course=COURSE,
                                                     session=2))
check("…once approved, the very next call carries it", "MARKER_LATE_ETA" in text)

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
