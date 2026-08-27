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
# Kept out of every other assertion so the sheet-import path can be exercised without
# perturbing the shelves the rest of this file describes.
DAVE = {"email": "dave@nxtwave.co.in", "name": "Dave", "is_admin": False}
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
for u in (ALICE, BOB, CAROL, DAVE, ADMIN):
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


def detail(payload):
    """The message a refusal carries. A 403 that says the wrong thing is its own bug —
    the person reading it has to know what to do next."""
    d = payload.get("detail", payload)
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


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

print("\n== the create-course form's own path claims it too ==")
# THE PATH THE UI ACTUALLY USES. "Create course" posts to /sync with a name and a sheet
# link; nothing else in the app creates a course from scratch. The claim has to happen
# on THAT request, not only on the select/save path — otherwise a course made the normal
# way is owner-less, and owner-less means visible to everyone.
# The import itself runs in a background thread and fails on this junk link, which is
# fine: the claim is synchronous, and that is what is under test.
D_COURSE = "Dave Sheet Course"
as_user(DAVE)
st, r = http("POST", "/sync", {"course_link": "https://docs.google.com/spreadsheets/d/nope/edit",
                               "course_name": D_COURSE, "course_type": "semester"})
check("POST /sync -> 200", st == 200, f"got {st}: {detail(r)}")
check("the importer is recorded as the creator", db.course_owner(D_COURSE) == DAVE["email"],
      f"got {db.course_owner(D_COURSE)!r}")
as_user(ALICE)
st, r = http("GET", "/courses")
check("…so it is not on anybody else's shelf", D_COURSE not in names(r), f"got {names(r)}")
st, r = http("GET", f"/curriculum?course={q(D_COURSE)}")
check("…nor openable by them", st == 403, f"got {st}")

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

print("\n== a team must be created with a course owner ==")
# The whole point of naming one: membership is delegated to them, so a team without an
# owner is a team only an admin can ever change — the bottleneck this removes.
as_user(ADMIN)
st, r = http("POST", "/admin/teams", {"name": "Ownerless", "course": None})
check("creating a team with no owner -> 400", st == 400, f"got {st}: {detail(r)}")
check("…and no such team exists",
      not any(t["name"] == "Ownerless" for t in db.teams()))
for bad in ("alice", "alice@gmail.com"):
    st, r = http("POST", "/admin/teams",
                 {"name": "Ownerless", "course": None, "owner": bad})
    check(f"…nor with an owner of {bad!r} -> 400", st == 400, f"got {st}: {detail(r)}")

print("\n== a team shelf is that team's courses, and stops there ==")
st, r = http("POST", "/admin/teams",
             {"name": "Shared Team", "course": None, "owner": ALICE["email"]})
tid = next((t["id"] for t in db.teams() if t["name"] == "Shared Team"), None)
check("the team was created", st == 200 and tid is not None, f"got {st}: {r}")
check("alice is its course owner", db.team_owner(tid) == ALICE["email"],
      f"got {db.team_owner(tid)!r}")
check("…and a member of it, so she can open what she is responsible for",
      ALICE["email"] in db.teams()[0].get("members", []) if db.teams() else False,
      str([t.get("members") for t in db.teams() if t["id"] == tid]))

print("\n== the course owner adds members, without an admin ==")
as_user(ALICE)
st, r = http("POST", f"/teams/{tid}/members", {"email": BOB["email"]})
check("the owner can add a member", st == 200, f"got {st}: {detail(r)}")
check("…and bob is on the team", BOB["email"] in (r.get("members") or []),
      f"got {r.get('members')}")
st, w = http("GET", "/workspaces")
check("the owner is told she may manage it",
      next((t["can_manage"] for t in w.get("teams", []) if t["id"] == tid), None) is True,
      str(w.get("teams")))

print("\n== …but an ordinary member does not ==")
# Seeing a team's work and deciding who else sees it are different powers, and only one
# of them is delegated.
as_user(BOB)
st, r = http("POST", f"/teams/{tid}/members", {"email": CAROL["email"]})
check("a plain member adding someone -> 403", st == 403, f"got {st}: {detail(r)}")
st, w = http("GET", "/workspaces")
check("…and he is told so", next((t["can_manage"] for t in w.get("teams", [])
                                 if t["id"] == tid), None) is False, str(w.get("teams")))
as_user(CAROL)
st, r = http("POST", f"/teams/{tid}/members", {"email": CAROL["email"]})
check("someone not on the team at all -> 403", st == 403, f"got {st}")
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

print("\n== adding someone hands them the team's courses, and only those ==")
as_user(ALICE)
st, r = http("POST", f"/teams/{tid}/members", {"email": CAROL["email"]})
check("the owner adds carol", st == 200, f"got {st}: {detail(r)}")
as_user(CAROL)
st, r = http("GET", "/courses")
check("carol is now offered the team's course", T_COURSE in names(r), f"got {names(r)}")
check("…and still not bob's other one", B_COURSE not in names(r), f"got {names(r)}")
st, r = http("GET", f"/curriculum?course={q(T_COURSE)}")
check("…and can open it", st == 200, f"got {st}")

print("\n== …and removing them takes it away again ==")
as_user(ALICE)
st, r = http("DELETE", f"/teams/{tid}/members/{q(CAROL['email'])}")
check("the owner removes carol", st == 200, f"got {st}: {detail(r)}")
as_user(CAROL)
st, r = http("GET", "/courses")
check("carol no longer sees it", T_COURSE not in names(r), f"got {names(r)}")
st, r = http("GET", f"/curriculum?course={q(T_COURSE)}")
check("…nor can open it", st == 403, f"got {st}")

print("\n== the owner cannot be removed from their own team ==")
# That would leave a team whose owner can still manage its members but cannot open the
# workspace they are responsible for. Re-assigning is the way, and that is the admin's.
as_user(ALICE)
st, r = http("DELETE", f"/teams/{tid}/members/{q(ALICE['email'])}")
check("removing the owner -> 409", st == 409, f"got {st}: {detail(r)}")
as_user(ADMIN)
st, r = http("DELETE", f"/admin/teams/{tid}/members/{q(ALICE['email'])}")
check("…even for an admin, who is told to reassign instead", st == 409, f"got {st}")
check("…so she is still on it", ALICE["email"] in db.teams_for_user(ALICE["email"])[0]["members"])

print("\n== bad member addresses are refused, not silently stored ==")
# add_member writes whatever it is given and membership is matched by exact string, so a
# typo makes a row that can never match a signed-in user: the team looks populated and
# the person it was for sees nothing.
as_user(ALICE)
for bad in ("carol", "carol@gmail.com", ""):
    st, r = http("POST", f"/teams/{tid}/members", {"email": bad})
    check(f"adding {bad!r} -> 400", st == 400, f"got {st}: {detail(r)}")
check("…and the team still has exactly its two members",
      sorted(db.teams_for_user(ALICE["email"])[0]["members"])
      == sorted([ALICE["email"], BOB["email"]]),
      str(db.teams_for_user(ALICE["email"])[0]["members"]))

print("\n== only an admin hands ownership to somebody else ==")
as_user(ALICE)
st, r = http("POST", f"/admin/teams/{tid}/owner", {"email": BOB["email"]})
check("the current owner cannot reassign -> 403", st == 403, f"got {st}")
as_user(ADMIN)
st, r = http("POST", f"/admin/teams/{tid}/owner", {"email": BOB["email"]})
check("the admin can", st == 200, f"got {st}: {detail(r)}")
check("…and the team says so", db.team_owner(tid) == BOB["email"],
      f"got {db.team_owner(tid)!r}")
check("…and the new owner owns the team's courses too",
      db.course_owner(T_COURSE) == BOB["email"], f"got {db.course_owner(T_COURSE)!r}")
as_user(ALICE)
st, r = http("POST", f"/teams/{tid}/members", {"email": CAROL["email"]})
check("the former owner can no longer add members -> 403", st == 403, f"got {st}")
as_user(BOB)
st, r = http("POST", f"/teams/{tid}/members", {"email": CAROL["email"]})
check("the new owner can", st == 200, f"got {st}: {detail(r)}")
# Put it back, so the sections after this read against the arrangement they describe.
as_user(BOB)
http("DELETE", f"/teams/{tid}/members/{q(CAROL['email'])}")
as_user(ADMIN)
http("POST", f"/admin/teams/{tid}/owner", {"email": ALICE["email"]})

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

print("\n== deleting a course you own ==")
# A course imported and no longer needed had to stay on the shelf for ever. It is the
# owner's to remove — and only theirs.
DOOMED = "Doomed Course"
make_course(ALICE, DOOMED)
as_user(BOB)
st, r = http("DELETE", f"/courses?course={q(DOOMED)}")
check("somebody else deleting it -> 403", st == 403, f"got {st}: {detail(r)}")
check("…and it is still there", len(db.curriculum(DOOMED)) == 1,
      str(db.curriculum(DOOMED)))
as_user(ALICE)
st, r = http("DELETE", f"/courses?course={q(DOOMED)}")
check("the owner can delete it", st == 200, f"got {st}: {detail(r)}")
check("…its curriculum is gone", db.curriculum(DOOMED) == [], str(db.curriculum(DOOMED)))
check("…its ownership record too", db.course_owner(DOOMED) is None,
      f"got {db.course_owner(DOOMED)!r}")
st, r = http("GET", "/courses")
check("…and it is off her shelf", DOOMED not in names(r), f"got {names(r)}")

print("\n== …but the documents it produced are kept ==")
# Deleting the record would not un-generate the documents. It would only make the
# instance lie about having produced them — and the reviewer still needs the files.
KEEP = "History Course"
make_course(BOB, KEEP)
db.create_run("keeprun", user_email=BOB["email"], course=KEEP, team_id=None,
              session_no=1, title="A finished doc", enforce_time=True)
db.finish_run("keeprun", status="done", accepted=True,
              docx_path=str(Path(TMP) / "Session 1 _ A finished doc.docx"))
as_user(BOB)
st, r = http("DELETE", f"/courses?course={q(KEEP)}")
check("the delete succeeds", st == 200, f"got {st}: {detail(r)}")
check("…and says so", r.get("history_kept") is True, str(r))
kept = [x for x in db.runs(user_email=BOB["email"]) if x["id"] == "keeprun"]
check("the run row survives", len(kept) == 1, str(len(kept)))
check("…still naming the course it was generated for",
      kept and kept[0].get("course") == KEEP, str(kept[0].get("course") if kept else None))
st, h = http("GET", "/my/history")
check("…and it is still in his history",
      any(c["course"] == KEEP for c in h.get("courses", [])),
      str([c["course"] for c in h.get("courses", [])]))
st, r = http("GET", "/preview/1?run_id=keeprun")
check("…and still resolvable, not forbidden", st != 403, f"got {st}")

print("\n== a shared course takes two steps, not one click ==")
# It is the curriculum a whole team works from. One request answers 409 and names them;
# only an explicit second one goes ahead.
SHARED = "Shared And Doomed"
make_course(ALICE, SHARED)
as_user(ALICE)
st, r = http("POST", f"/teams/{tid}/courses", {"course": SHARED})
check("alice shares it with her team", st == 200, f"got {st}: {detail(r)}")
st, r = http("DELETE", f"/courses?course={q(SHARED)}")
check("deleting it unconfirmed -> 409", st == 409, f"got {st}")
check("…and the refusal names the team",
      "Shared Team" in detail(r), f"got {detail(r)}")
check("…and hands the caller the list to confirm against",
      [t["name"] for t in (r.get("detail", {}).get("teams") or [])] == ["Shared Team"],
      str(r.get("detail", {}).get("teams")))
check("…and nothing was deleted", len(db.curriculum(SHARED)) == 1)
st, r = http("DELETE", f"/courses?course={q(SHARED)}&detach_teams=true")
check("confirmed, it goes", st == 200, f"got {st}: {detail(r)}")
check("…and is off the team's shelf too", SHARED not in db.team_course_list(tid),
      str(db.team_course_list(tid)))
check("…which the reply reports",
      [t["name"] for t in (r.get("teams_detached") or [])] == ["Shared Team"],
      str(r.get("teams_detached")))

print("\n== a team's course can be un-shared WITHOUT deleting it ==")
# The other half of the choice the 409 offers: keep the course, end the sharing.
as_user(BOB)
st, r = http("POST", f"/teams/{tid}/courses", {"course": T_COURSE})
as_user(BOB)          # bob is a member, not the owner
st, r = http("DELETE", f"/teams/{tid}/courses?course={q(T_COURSE)}")
check("a plain member cannot un-share it -> 403", st == 403, f"got {st}")
owner_before = db.course_owner(T_COURSE)
as_user(ALICE)        # …the owner can
st, r = http("DELETE", f"/teams/{tid}/courses?course={q(T_COURSE)}")
check("the team's course owner can", st == 200, f"got {st}: {detail(r)}")
# Checked through team_course_list, not the join table alone: that list also reads the
# team's legacy primary-course column, and un-sharing has to clear both or the course is
# removed and reported straight back as still attached.
check("…the team no longer holds it", T_COURSE not in db.team_course_list(tid),
      str(db.team_course_list(tid)))
check("…nor does the reply claim it does", T_COURSE not in (r.get("courses") or []),
      str(r.get("courses")))
check("…but the course itself is untouched", len(db.curriculum(T_COURSE)) >= 1,
      str(db.curriculum(T_COURSE)))
check("…and its owner is unchanged", db.course_owner(T_COURSE) == owner_before,
      f"{owner_before!r} -> {db.course_owner(T_COURSE)!r}")

print("\n== a deleted course only takes the decks nothing else claims ==")
# THE SUBTLE ONE. Extracted decks are filed by session NUMBER alone — decks/session_02
# .json — with no course anywhere in the path, so they are shared ground. Deleting "this
# course's decks" would take session 1's deck away from every other course that has a
# session 1, and every course has a session 1.
from src import pptx_ingest                                  # noqa: E402
DECKA, DECKB = "Deck Course A", "Deck Course B"
make_course(ALICE, DECKA)
for n in (91, 92):
    db.curriculum_upsert(DECKA, n, topic="T", session_name=f"a{n}", key_takeaways=["k"])
make_course(BOB, DECKB)
db.curriculum_upsert(DECKB, 91, topic="T", session_name="b91", key_takeaways=["k"])
pptx_ingest.DECKS_DIR.mkdir(parents=True, exist_ok=True)
for n in (91, 92):
    (pptx_ingest.DECKS_DIR / f"session_{n:02d}.json").write_text(
        json.dumps({"session_no": n, "slides": []}), encoding="utf-8")
as_user(ALICE)
st, r = http("DELETE", f"/courses?course={q(DECKA)}")
check("the delete succeeds", st == 200, f"got {st}: {detail(r)}")
check("session 92's deck goes — nothing else has a session 92",
      r.get("decks_cleared") == [92], f"got {r.get('decks_cleared')}")
check("…and the file is really gone",
      not (pptx_ingest.DECKS_DIR / "session_92.json").exists())
check("session 91's deck STAYS — the other course has a session 91",
      (pptx_ingest.DECKS_DIR / "session_91.json").exists())

print("\n== an unclaimed course is the admin's to delete, not anyone's ==")
ORPHANED = "Nobody's Course"
db.curriculum_upsert(ORPHANED, 1, topic="T", session_name="x", key_takeaways=["y"])
as_user(CAROL)
st, r = http("DELETE", f"/courses?course={q(ORPHANED)}")
check("a user who can merely OPEN it cannot delete it -> 403", st == 403, f"got {st}")
check("…and is told why", "only an admin" in detail(r), f"got {detail(r)}")
as_user(ADMIN)
st, r = http("DELETE", f"/courses?course={q(ORPHANED)}")
check("the admin can", st == 200, f"got {st}: {detail(r)}")
check("…and it is gone", db.curriculum(ORPHANED) == [])

print("\n== deleting the ACTIVE course does not leave the app pointing at nothing ==")
# app_settings.course_name() is one instance-wide setting and is what an unnamed request
# falls back to; left naming a deleted course it would drop to a hard-coded legacy
# default nobody chose.
LAST = "Alice Second Course"
make_course(ALICE, LAST)          # select() makes it the active one
as_user(ALICE)
st, r = http("DELETE", f"/courses?course={q(LAST)}")
check("the active course can be deleted", st == 200, f"got {st}: {detail(r)}")
check("…and the reply moves the caller to one that exists",
      r.get("course") in {c["name"] for c in r.get("courses", [])} or r.get("course") is None,
      f"got {r.get('course')!r} against {[c['name'] for c in r.get('courses', [])]}")
st, b = http("GET", "/bootstrap")
check("…so bootstrap still answers", st == 200, f"got {st}")
check("…and not on the deleted course", b.get("course") != LAST, f"got {b.get('course')!r}")
# app_settings.course_name() falls back to a HARD-CODED legacy default when cleared, so
# without a check for "does this course exist" the reply would name a course nobody on
# the instance has ever had.
known = set(db.curriculum_session_counts())
check("…nor on a course that does not exist at all",
      b.get("course") in known or b.get("course") == "default"
      or db.course_owner(b.get("course") or "") == ALICE["email"],
      f"got {b.get('course')!r} against {sorted(known)}")

print("\n== deleting the LAST course leaves no phantom sessions behind ==")
# knowledge_base/course_structure.json is the curriculum's on-disk projection and is keyed
# by session NUMBER alone — it names no course — and the session loader falls back to it
# whenever the database holds no curriculum at all. Left behind, a deleted course's
# sessions went on filling the generate dropdown of an instance with no courses left.
as_user(ADMIN)
for c in sorted(db.curriculum_session_counts()):
    http("DELETE", f"/courses?course={q(c)}&detach_teams=true")
check("every course is gone", db.curriculum_session_counts() == {},
      str(db.curriculum_session_counts()))
st, b = http("GET", "/bootstrap")
check("bootstrap answers", st == 200, f"got {st}")
check("…with no curriculum", (b.get("curriculum", {}).get("rows") or []) == [],
      str(len(b.get("curriculum", {}).get("rows") or [])))
check("…and NO sessions offered for generation",
      (b.get("sessions") or []) == [], f"got {len(b.get('sessions') or [])}")

print("\n== a course you just created survives a reload before it has any rows ==")
# The other side of that check: a course claimed at select-time has no curriculum yet, and
# taking it off the caller because of that would lose their place every reload.
as_user(ALICE)
st, _ = http("POST", "/courses/select", {"course": "Empty But Mine"})
check("selecting a brand-new name works", st == 200, f"got {st}")
st, b = http("GET", "/bootstrap")
check("…and an unnamed bootstrap stays on it",
      b.get("course") == "Empty But Mine", f"got {b.get('course')!r}")

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
