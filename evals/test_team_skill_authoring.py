"""Every member of a team that shares a course may author that course's skills.

    python -m evals.test_team_skill_authoring     # no API key needed, ~5 seconds

WHY THIS EXISTS. Skills used to be the course owner's alone, so a team-mate generating
session 14 could not write down what session 14 needed. Now anyone who may OPEN a course
may add, approve, edit and retire its skills — for one session or the whole course — and
lift a session skill into the course set. Prerequisites stay with the owner, and someone
outside the team still cannot touch any of it.

Structure: bob owns "Team Course" and shares it with a team {bob, alice}; carol is on
no team. The database is a throwaway under TR_DATA_DIR.
"""
import json
import os
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_team_skills_test_")
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
from src import db                                # noqa: E402
from src import skills as skill_rules             # noqa: E402

PORT = 8794
BASE = f"http://127.0.0.1:{PORT}/api"

ALICE = {"email": "alice@nxtwave.co.in", "name": "Alice", "is_admin": False}
BOB = {"email": "bob@nxtwave.co.in", "name": "Bob", "is_admin": False}
CAROL = {"email": "carol@nxtwave.co.in", "name": "Carol", "is_admin": False}
ADMIN = {"email": "admin@nxtwave.co.in", "name": "Admin", "is_admin": True}
COURSE = "Team Course"

CURRENT = dict(ALICE)
server.app.dependency_overrides[server.current_user] = lambda: dict(CURRENT)
server._guided_generate_all = lambda gid: None
# No model in this test: the articulation step is a drafting aid, and without it the
# skill is stored as typed — which is all the permission checks need.
skill_rules.articulate = lambda text, model=None: None


def as_user(u):
    CURRENT.clear()
    CURRENT.update(u)


db.init()
for u in (ALICE, BOB, CAROL, ADMIN):
    db.upsert_user(u["email"], u["name"], u["is_admin"])


def http(method, path, body=None):
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


def q(s):
    return urllib.parse.quote(s)


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

# --- bob's course, shared with a team alice is on ------------------------------------
as_user(BOB)
http("POST", "/courses/select", {"course": COURSE})
http("POST", "/curriculum", {"course": COURSE, "rows": [
    {"session_no": n, "topic": "T", "session_name": f"session {n}",
     "key_takeaways": ["a takeaway"]} for n in (1, 2)]})
as_user(ADMIN)
http("POST", "/admin/teams", {"name": "Crew", "course": None, "owner": BOB["email"]})
tid = next(t["id"] for t in db.teams() if t["name"] == "Crew")
as_user(BOB)
http("POST", f"/teams/{tid}/members", {"email": ALICE["email"]})
st, _ = http("POST", f"/teams/{tid}/courses", {"course": COURSE})
assert st == 200, st

print("\n== a team member who is NOT the owner can author skills ==")
as_user(ALICE)
st, r = http("GET", f"/skills?course={q(COURSE)}")
check("GET /skills -> 200", st == 200, f"got {st}")
check("…and she is told she may edit", r.get("can_edit") is True, str(r.get("can_edit")))

st, r = http("POST", "/skills", {"course": COURSE, "text": "Open with a live demo.",
                                 "scope": "session", "session": 2})
check("alice adds a SESSION skill -> 200", st == 200, f"got {st}: {r}")
sid = r.get("id")
st, r = http("POST", "/skills", {"course": COURSE, "text": "Use Indian rupee examples."})
check("alice adds a COURSE skill -> 200", st == 200, f"got {st}: {r}")
cid = r.get("id")

st, r = http("POST", f"/skills/{sid}/approve?course={q(COURSE)}")
check("alice approves a skill -> 200", st == 200, f"got {st}")
st, r = http("POST", f"/skills/{cid}/edit", {"course": COURSE,
                                              "text": "Use Indian examples, in rupees."})
check("alice edits a skill -> 200", st == 200, f"got {st}")

print("\n== …and can lift a session skill into the course set ==")
st, r = http("POST", f"/skills/{sid}/promote?course={q(COURSE)}")
check("promote -> 200", st == 200, f"got {st}: {r}")
row = next((s for s in r.get("skills", []) if s["id"] == sid), {})
check("…it now applies to the whole course", row.get("scope") == "course", str(row))
check("…with no session left on it", not row.get("session_ref"), str(row))
check("…and its approval stands, since its words did not change",
      row.get("status") == "approved", str(row.get("status")))
st, r = http("POST", f"/skills/{sid}/promote?course={q(COURSE)}")
check("promoting a course skill again -> 400", st == 400, f"got {st}")

st, r = http("DELETE", f"/skills/{cid}?course={q(COURSE)}")
check("alice retires a skill -> 200", st == 200, f"got {st}")

print("\n== prerequisites stay with the owner ==")
st, r = http("POST", "/prereqs", {"course": COURSE, "prereq": "Anything"})
check("alice adding a prerequisite -> 403", st == 403, f"got {st}")
st, r = http("GET", f"/prereqs?course={q(COURSE)}")
check("…and the prereqs panel tells her she cannot edit it",
      r.get("can_edit") is False, str(r.get("can_edit")))

print("\n== someone outside the team still cannot touch it ==")
as_user(CAROL)
st, r = http("POST", "/skills", {"course": COURSE, "text": "Carol was here."})
check("carol adding a skill -> 403", st == 403, f"got {st}")
st, r = http("POST", f"/skills/{sid}/promote?course={q(COURSE)}")
check("carol promoting a skill -> 403", st == 403, f"got {st}")

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
