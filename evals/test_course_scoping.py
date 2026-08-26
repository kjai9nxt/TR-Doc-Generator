"""Who can see, open and edit which course — over real HTTP, with real users.

    python -m evals.test_course_scoping        # no API key needed, ~5 seconds

WHY THIS EXISTS. Every individual user could see every course on the instance. The
rule in db.courses_for_user was "on a team -> that team's courses; on NO team ->
EVERYTHING", written so that a person not yet in a team would not be locked out of an
agent they are entitled to use. Nothing recorded who had CREATED a course, so there was
nothing narrower to fall back to. The effect: sign in for the first time and every
course anyone in the org had ever imported was in your private workspace, switchable,
editable, and generate-able against. Somebody on a team had the mirror-image problem —
the course they made themselves was not on their own shelf.

And filtering the list would not have been enough on its own. The course is a plain
parameter on every curriculum, settings, session, sync and generation endpoint, so a
name typed into a URL reached another person's course whatever the sidebar showed.
Both halves are checked here: what you are OFFERED, and what you are ALLOWED.

Structure: three ordinary users and one admin against one server.
  alice — creates "Alice Course"
  bob   — creates "Bob Course" and "Team Course", the latter shared with a team
  team  — {alice, bob}, owning "Team Course" only
  carol — brand new, on no team, owns nothing: the case that used to see everything
  admin — sees the instance, deliberately

The database is a throwaway under TR_DATA_DIR, so this never touches anything of the
user's.
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

TMP = tempfile.mkdtemp(prefix="tr_scoping_test_")
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

PORT = 8793
BASE = f"http://127.0.0.1:{PORT}/api"

ALICE = {"email": "alice@nxtwave.co.in", "name": "Alice", "is_admin": False}
BOB = {"email": "bob@nxtwave.co.in", "name": "Bob", "is_admin": False}
CAROL = {"email": "carol@nxtwave.co.in", "name": "Carol", "is_admin": False}
ADMIN = {"email": "admin@nxtwave.co.in", "name": "Admin", "is_admin": True}

A_COURSE = "Alice Course"
B_COURSE = "Bob Course"
T_COURSE = "Team Course"

# --- fakes: WHO is signed in (swapped per request), and the generation thread -------
CURRENT = dict(ALICE)
server.app.dependency_overrides[server.current_user] = lambda: dict(CURRENT)
server._guided_generate_all = lambda gid: None


def as_user(u):
    CURRENT.clear()
    CURRENT.update(u)


db.init()
for u in (ALICE, BOB, CAROL, ADMIN):
    db.upsert_user(u["email"], u["name"], u["is_admin"])


def http(method, path, body=None):
    """(status, parsed_json) as whoever as_user() last named. Never raises on 4xx/5xx —
    the status IS the thing under test here."""
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


def q(course):
    return urllib.parse.quote(course)


def names(payload):
    return sorted(c["name"] for c in payload.get("courses", []))


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


def make_course(user, course):
    """Create a course the way the app does: select the name, then save a row into it."""
    as_user(user)
    st, _ = http("POST", "/courses/select", {"course": course})
    assert st == 200, (course, st)
    st, _ = http("POST", "/curriculum", {"course": course, "rows": [
        {"session_no": 1, "topic": "T", "session_name": f"{course} session 1",
         "key_takeaways": ["a takeaway"]}]})
    assert st == 200, (course, st)


print("\n== creating a course records WHO created it ==")
make_course(ALICE, A_COURSE)
make_course(BOB, B_COURSE)
make_course(BOB, T_COURSE)
check("alice owns the course she created", db.course_owner(A_COURSE) == ALICE["email"],
      f"got {db.course_owner(A_COURSE)!r}")
check("bob owns his", db.course_owner(B_COURSE) == BOB["email"],
      f"got {db.course_owner(B_COURSE)!r}")

print("\n== re-saving somebody else's curriculum does NOT transfer it ==")
# First claim wins. Without that, ownership would follow whoever edited last, which is
# no ownership at all.
as_user(ADMIN)
http("POST", "/curriculum", {"course": B_COURSE, "rows": [
    {"session_no": 2, "topic": "T", "session_name": "admin added this",
     "key_takeaways": ["x"]}]})
check("the creator is still bob after an admin edit",
      db.course_owner(B_COURSE) == BOB["email"], f"got {db.course_owner(B_COURSE)!r}")

print("\n== the individual shelf is only what you made ==")
as_user(ALICE)
st, r = http("GET", "/courses")
check("GET /courses -> 200", st == 200, f"got {st}")
check("alice is offered her course and nothing else", names(r) == [A_COURSE], f"got {names(r)}")
st, w = http("GET", "/workspaces")
check("her individual workspace holds only her course",
      w.get("individual", {}).get("courses") == [A_COURSE],
      f"got {w.get('individual')}")

as_user(BOB)
st, r = http("GET", "/courses")
check("bob is offered his two", names(r) == sorted([B_COURSE, T_COURSE]), f"got {names(r)}")
check("…and not alice's", A_COURSE not in names(r), f"got {names(r)}")

print("\n== a brand-new user sees nobody else's courses ==")
# THE REGRESSION THIS GUARDS. Carol is on no team and has created nothing: the exact
# case that used to be handed every course on the instance.
as_user(CAROL)
st, r = http("GET", "/courses")
check("carol's shelf is empty", names(r) == [], f"got {names(r)}")
st, w = http("GET", "/workspaces")
check("…individually too", w.get("individual", {}).get("courses") == [],
      f"got {w.get('individual')}")
check("…and she is on no team", w.get("teams") == [], f"got {w.get('teams')}")

print("\n== …and cannot reach one by naming it ==")
# Filtering the list is cosmetic on its own: the course is a plain parameter on every
# one of these.
for method, path, body in (
    ("GET", f"/curriculum?course={q(B_COURSE)}", None),
    ("GET", f"/course-settings?course={q(B_COURSE)}", None),
    ("GET", f"/sessions?course={q(B_COURSE)}", None),
    ("GET", f"/bootstrap?course={q(B_COURSE)}", None),
    ("POST", "/curriculum", {"course": B_COURSE, "rows": [
        {"session_no": 1, "topic": "hijack", "session_name": "hijack",
         "key_takeaways": []}]}),
    ("POST", "/curriculum/insert", {"course": B_COURSE, "at_session_no": 1}),
    ("DELETE", f"/curriculum/1?course={q(B_COURSE)}", None),
    ("POST", "/course-settings", {"course": B_COURSE, "max_pages": 4}),
    ("POST", "/session-settings", {"course": B_COURSE, "session_no": 1, "max_pages": 4}),
    ("POST", "/curriculum/ingest", {"course": B_COURSE, "force": False, "sessions": None}),
    ("POST", "/sync", {"course_link": "https://docs.google.com/spreadsheets/d/X/edit",
                       "course_name": B_COURSE, "course_type": "semester"}),
    ("POST", "/courses/select", {"course": B_COURSE}),
    ("POST", "/guided/start", {"session_no": 1, "course": B_COURSE}),
):
    st, r = http(method, path, body)
    check(f"{method} {path.split('?')[0]} on someone else's course -> 403",
          st == 403, f"got {st}")

check("…and the edit she attempted never landed",
      [row["session_name"] for row in db.curriculum(B_COURSE) if row["session_no"] == 1]
      == [f"{B_COURSE} session 1"],
      str([row["session_name"] for row in db.curriculum(B_COURSE)]))

print("\n== a team shelf is that team's courses, and stops there ==")
as_user(ADMIN)
st, r = http("POST", "/admin/teams", {"name": "Shared Team", "course": None})
tid = (r.get("teams") or [{}])[0].get("id") if r.get("teams") else None
tid = next((t["id"] for t in db.teams() if t["name"] == "Shared Team"), None)
check("the team was created", tid is not None, f"got {r}")
for u in (ALICE, BOB):
    http("POST", f"/admin/teams/{tid}/members", {"email": u["email"]})
# Bob shares ONE of his two courses with the team. The other is the control: it proves
# the team shelf is the team's courses and not "everything its members own".
as_user(BOB)
st, r = http("POST", f"/teams/{tid}/courses", {"course": T_COURSE})
check("bob can attach his own course to his team", st == 200, f"got {st}: {r}")

as_user(ALICE)
st, w = http("GET", "/workspaces")
team = next((t for t in w.get("teams", []) if t["id"] == tid), None)
check("alice now sees the team", team is not None, f"got {w.get('teams')}")
check("the team shelf is exactly the shared course",
      (team or {}).get("courses") == [T_COURSE], f"got {(team or {}).get('courses')}")
check("bob's OTHER course is not on it", B_COURSE not in ((team or {}).get("courses") or []))
check("her individual shelf did not grow",
      w.get("individual", {}).get("courses") == [A_COURSE], f"got {w.get('individual')}")

st, r = http("GET", "/courses")
check("the shared course is now offered to her",
      names(r) == sorted([A_COURSE, T_COURSE]), f"got {names(r)}")
st, r = http("GET", f"/curriculum?course={q(T_COURSE)}")
check("…and she can open it", st == 200, f"got {st}")
st, r = http("GET", f"/curriculum?course={q(B_COURSE)}")
check("…but still not the one that was never shared", st == 403, f"got {st}")

print("\n== a member cannot pull somebody else's course onto the team ==")
# Otherwise this endpoint is the way round everything above: name a course, and it is
# on your whole team's shelf.
as_user(ALICE)
st, r = http("POST", f"/teams/{tid}/courses", {"course": B_COURSE})
check("attaching a course she cannot open -> 403", st == 403, f"got {st}")
check("…and the team shelf is unchanged", db.team_course_list(tid) == [T_COURSE],
      f"got {db.team_course_list(tid)}")

print("\n== the instance-wide 'active course' does not lock anyone out ==")
# app_settings.course_name() is ONE setting for the whole instance, so whoever selected
# a course last set the default for everybody. Returning it unchecked would 403 the
# app's own bootstrap for everyone who cannot open it — a locked-out user, not a scoped
# one — so an unnamed request falls back to a course the CALLER owns.
as_user(BOB)
http("POST", "/courses/select", {"course": B_COURSE})
as_user(ALICE)
st, b = http("GET", "/bootstrap")
check("alice's bootstrap still answers", st == 200, f"got {st}")
check("…on a course she owns", b.get("course") == A_COURSE, f"got {b.get('course')!r}")
as_user(CAROL)
st, b = http("GET", "/bootstrap")
check("carol's bootstrap answers too", st == 200, f"got {st}")
check("…with no course and an empty shelf",
      b.get("course") != B_COURSE and (b.get("courses") or []) == [],
      f"got {b.get('course')!r} / {b.get('courses')}")
# The label mattering less than the payload: naming a course she cannot open would put
# its curriculum and its session list straight into her reply.
check("…and none of bob's curriculum in the reply",
      (b.get("curriculum", {}).get("rows") or []) == [],
      str(b.get("curriculum", {}).get("rows")))
check("…nor his sessions in her generate dropdown",
      (b.get("sessions") or []) == [], str(b.get("sessions")))

print("\n== a finished document is not downloadable by naming its file ==")
# download / preview / gdoc identify an output by run id or exact filename, so the
# course scoping above does not reach them on its own.
db.create_run("bobrun", user_email=BOB["email"], course=B_COURSE, team_id=None,
              session_no=1, title="Bob doc", enforce_time=True)
db.finish_run("bobrun", status="done", accepted=True,
              docx_path=str(Path(TMP) / "Session 1 _ Bob doc.docx"))
as_user(ALICE)
for path in (f"/preview/1?run_id=bobrun",
             f"/preview/1?name={q('Session 1 _ Bob doc.docx')}"):
    st, r = http("GET", path)
    check(f"GET {path.split('?')[0]} on bob's run -> 403", st == 403, f"got {st}")
as_user(BOB)
st, r = http("GET", "/preview/1?run_id=bobrun")
check("bob himself is not blocked (404 for a missing file, not 403)", st != 403,
      f"got {st}")

print("\n== an in-flight generation is not readable by its id ==")
as_user(BOB)
st, r = http("POST", "/guided/start", {"session_no": 1, "course": B_COURSE})
gid = r.get("guided_id")
check("bob starts a run", st == 200 and bool(gid), f"got {st}: {r}")
as_user(ALICE)
st, r = http("GET", f"/guided/{gid}")
check("alice cannot read it", st == 403, f"got {st}")
st, r = http("POST", f"/guided/{gid}/regenerate", {"index": 0, "reason": "because"})
check("…nor spend his tokens regenerating a chunk of it", st == 403, f"got {st}")
st, r = http("POST", f"/guided/{gid}/discard")
check("…nor discard it", st == 403, f"got {st}")
as_user(BOB)
st, r = http("GET", f"/guided/{gid}")
check("bob can read his own", st == 200, f"got {st}")

print("\n== an admin still sees the instance ==")
as_user(ADMIN)
st, r = http("GET", "/courses")
check("every course is offered to an admin",
      names(r) == sorted([A_COURSE, B_COURSE, T_COURSE]), f"got {names(r)}")
st, r = http("GET", f"/curriculum?course={q(B_COURSE)}")
check("…and openable", st == 200, f"got {st}")

print("\n== a course nobody created is not stranded ==")
# What a curriculum imported before ownership was recorded looks like. Refusing it would
# lock its own author out with no way back in, so it stays visible until the first write
# claims it.
LEGACY = "Legacy Course"
db.curriculum_upsert(LEGACY, 1, topic="T", session_name="legacy 1", key_takeaways=["x"])
as_user(CAROL)
st, r = http("GET", "/courses")
check("an unclaimed course is offered", names(r) == [LEGACY], f"got {names(r)}")
check("…and flagged as unclaimed rather than hers",
      [(c["mine"], c["unclaimed"]) for c in r["courses"]] == [(False, True)],
      str(r["courses"]))
st, _ = http("POST", "/curriculum", {"course": LEGACY, "rows": [
    {"session_no": 2, "topic": "T", "session_name": "carol added this",
     "key_takeaways": ["y"]}]})
check("writing to it claims it", db.course_owner(LEGACY) == CAROL["email"],
      f"got {db.course_owner(LEGACY)!r}")
as_user(ALICE)
st, r = http("GET", "/courses")
check("…so it leaves everyone else's shelf", LEGACY not in names(r), f"got {names(r)}")

print("\n== the backfill attributes courses that predate all of this ==")
# On a real instance every course has runs; the earliest one says who was working on it.
ORPHAN = "Orphan Course"
db.curriculum_upsert(ORPHAN, 1, topic="T", session_name="orphan 1", key_takeaways=["x"])
db.create_run("oldrun", user_email=BOB["email"], course=ORPHAN, team_id=None,
              session_no=1, title="old", enforce_time=True)
claimed = db.backfill_course_owners()
check("the orphan is attributed to whoever generated first",
      claimed.get(ORPHAN) == BOB["email"], f"got {claimed}")
check("and re-running it changes nothing", db.backfill_course_owners() == {})
as_user(ALICE)
st, r = http("GET", "/courses")
check("so it is on bob's shelf, not alice's", ORPHAN not in names(r), f"got {names(r)}")

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
