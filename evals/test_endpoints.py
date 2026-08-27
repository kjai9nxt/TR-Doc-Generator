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
# Reviewing the chunk first, because that is what a reviewer does and what the server now
# requires: finalize refuses a document whose chunks were never ticked, rather than
# trusting a disabled button in the client to have stopped it.
st, r = http("POST", f"/guided/{gid}/finalize")
check("finalize refuses an unreviewed document", st == 409, f"got {st}: {detail(r)}")
st, _ = http("POST", f"/guided/{gid}/approve", {"index": 0})
check("approving the chunk -> 200", st == 200, f"got {st}")
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

print("\n== the roll-up counts docs, attempts and sign-offs as DIFFERENT numbers ==")
# The team panel showed "Docs built 0" against real work: it read `summary.runs`, a key
# _rollup has never emitted, which read undefined and fell through to 0. The UI harness
# could not catch it because its stub had invented the same key. So the CONTRACT is pinned
# here, on the server side, where the numbers are actually produced.
ROLL = "Rollup Course"
db.curriculum_upsert(ROLL, 1, session_name="rollup 1", key_takeaways=["k"])
for rid, status, approve in (("roll_done_ok", "done", True),
                             ("roll_done_flagged", "done", True),
                             ("roll_done_unapproved", "done", False),
                             ("roll_failed", "error", False),
                             ("roll_running", "running", False)):
    db.create_run(rid, user_email=USER["email"], course=ROLL, team_id=None,
                  session_no=1, title="t", enforce_time=True)
    if status != "running":
        db.finish_run(rid, status=status, accepted=(rid == "roll_done_ok"))
    if approve:
        db.mark_approved(rid, USER["email"])
_runs = [r for r in db.runs(course=ROLL)]
_roll = server._rollup(_runs)
check("every attempt is counted as an attempt", _roll["total_runs"] == 5,
      str(_roll["total_runs"]))
check("only the ones that produced a document count as docs built",
      _roll["docs_built"] == 3, str(_roll["docs_built"]))
check("…so a failed or still-running attempt is not a doc",
      _roll["docs_built"] < _roll["total_runs"], str(_roll))
check("human sign-offs are their own number", _roll["approved_docs"] == 2,
      str(_roll["approved_docs"]))
check("…and the graders' verdict another", _roll["gates_passed_docs"] == 1,
      str(_roll["gates_passed_docs"]))
# The keys the UI actually reads. A rename here silently zeroes a card on screen, which
# is exactly what happened, so the names are part of the contract.
for key in ("total_runs", "docs_built", "approved_docs", "gates_passed_docs",
            "total_cost", "total_tokens"):
    check(f"the roll-up carries '{key}'", key in _roll, str(sorted(_roll)))
check("…and does NOT carry a bare 'runs' the UI might reach for",
      "runs" not in _roll, str(sorted(_roll)))
st, _hist = http("GET", "/my/history")
check("/my/history reports it over the wire too",
      "docs_built" in (_hist.get("summary") or {}), str(sorted(_hist.get("summary") or {})))
st, _teams = http("GET", "/my/teams")
check("GET /my/teams -> 200", st == 200, f"got {st}")

print("\n== the ADMIN dashboard tells the two verdicts apart as well ==")
# The same conflation, in a third place. db.summary(), db.per_user() and db.timeseries()
# all counted `accepted` — the GRADERS' verdict — under the name `approved`, which the
# admin page prints as "Approved". So Completed and Approved differed on screen for a
# reason nobody could see, while the runs table right below them labelled the very same
# rows from `outcome`, which uses the human sign-off.
#
# Expectations are computed from the run rows rather than written as numbers: these are
# instance-wide aggregates, so a literal would only be asserting what the tests above
# happened to leave behind.
_all = db.runs(limit=100000)
_done = [r for r in _all if r["status"] == "done"]
_exp_approved = len([r for r in _done if r["approved"]])       # a person signed it off
_exp_gates = len([r for r in _done if r["accepted"]])          # the graders passed it
check("the two verdicts really are different here — otherwise this proves nothing",
      _exp_approved != _exp_gates, f"{_exp_approved} vs {_exp_gates}")
_sum = db.summary()
check("summary's 'approved' is the HUMAN sign-off", _sum["approved"] == _exp_approved,
      f"got {_sum['approved']}, expected {_exp_approved}")
check("…and NOT the graders' count, which is what it used to be",
      _sum["approved"] != _exp_gates, f"got {_sum['approved']}, graders={_exp_gates}")
check("the graders' verdict is reported too, under its own name",
      _sum["gates_passed"] == _exp_gates, f"got {_sum.get('gates_passed')}")
check("'completed' counts every finished run", _sum["done"] == len(_done), str(_sum["done"]))
check("…so completed >= approved, which is why they differ on screen",
      _sum["done"] >= _sum["approved"], str((_sum["done"], _sum["approved"])))
check("both rates are offered, named for what they measure",
      "approval_rate" in _sum and "acceptance_rate" in _sum, str(sorted(_sum)))
_me = {u["user"]: u for u in db.per_user()}.get(USER["email"], {})
_mine_done = [r for r in _done if r["user_email"] == USER["email"]]
check("per-user splits them too",
      _me.get("approved") == len([r for r in _mine_done if r["approved"]])
      and _me.get("gates_passed") == len([r for r in _mine_done if r["accepted"]]),
      f"approved={_me.get('approved')} gates={_me.get('gates_passed')}")
_ts = db.timeseries("day")
check("…and so does the time series",
      sum(b["approved"] for b in _ts) == len([r for r in _all if r["approved"]])
      and sum(b["gates_passed"] for b in _ts) == len([r for r in _all if r["accepted"]]),
      str([(b["approved"], b["gates_passed"]) for b in _ts]))
# And the per-run label the runs table prints must agree with the cards above it — that
# disagreement was the visible symptom.
check("the runs table's own labels agree with the roll-up",
      len([r for r in db.runs(course=ROLL) if r["outcome"] == "approved"])
      == server._rollup(db.runs(course=ROLL))["approved_docs"],
      str([r["outcome"] for r in db.runs(course=ROLL)]))

print("\n== inserting a session renumbers the ones after it ==")
# Reported: inserting a row at the TOP of a 34-session course numbered it 35. A
# curriculum is an ordered list — the row you put first is session 1.
from src import pptx_ingest                                    # noqa: E402

INS = "Insert Order Course"
for n in (1, 2, 3):
    db.curriculum_upsert(INS, n, session_name=f"original {n}",
                         key_takeaways=[f"takeaway {n}"],
                         ppt_link=f"https://docs.google.com/presentation/d/D{n}/edit")
# Give session 2 an extracted deck, so we can check the deck follows its row. Written
# through the store, which files it under THIS course — decks are course-scoped now, so
# a hand-built path would put it somewhere the endpoint does not look.
pptx_ingest.put_deck(INS, 2, {
    "session_no": 2, "deck_title": "Deck of the second session", "n_slides": 3,
    "slides": [{"n": 1, "title": "T", "text": "x"}]})

st, r = http("POST", "/curriculum/insert", {"at_session_no": 1, "course": INS})
check("POST /curriculum/insert -> 200", st == 200, f"got {st}: {detail(r)}")
rows = {int(x["session_no"]): x for x in (r.get("rows") or [])}
check("the new row IS session 1", r.get("inserted") == 1, str(r.get("inserted")))
check("…and it is the blank one", (rows.get(1) or {}).get("session_name") in ("", None),
      str(rows.get(1)))
check("every original moved down exactly one",
      [rows[n]["session_name"] for n in (2, 3, 4)]
      == ["original 1", "original 2", "original 3"],
      str([(n, rows[n]["session_name"]) for n in sorted(rows)]))
check("…keeping its own deck link",
      rows[2]["ppt_link"].endswith("D1/edit") and rows[4]["ppt_link"].endswith("D3/edit"),
      str([(n, rows[n]["ppt_link"][-12:]) for n in sorted(rows)]))
check("no two rows share a number", len(rows) == 4, str(sorted(rows)))
# The deck that was session 2's must now be session 3's — otherwise the new session 2
# would read it as what it had already taught.
check("the extracted deck moved with its session",
      pptx_ingest.has_deck(INS, 3),
      str(sorted(pptx_ingest.deck_session_numbers(INS))))
if pptx_ingest.has_deck(INS, 3):
    check("…and says so inside the file too",
          pptx_ingest.get_deck(INS, 3).get("session_no") == 3)
    check("…and did not leave a copy behind", not pptx_ingest.has_deck(INS, 2))

# The deck move must also reach the CLOUD MIRROR. On the deployed instance the decks
# sit on an ephemeral disk and are restored from kb_files on boot, so a rename that
# never reached that table would be undone by the next restart — and the renumbered
# curriculum would then be pointing at the old decks.
_renames = []
_real_rename = db.kb_rename_decks
db.kb_rename_decks = lambda c, m: _renames.append(dict(m)) or 0
st, _ = http("POST", "/curriculum/insert", {"at_session_no": 2, "course": INS})
db.kb_rename_decks = _real_rename
check("renumbering moves the decks in the DB mirror too", len(_renames) == 1,
      f"kb_rename_decks called {len(_renames)} time(s)")

# And the mirror move itself, against a real table — including the chain that makes
# the naive version collide (3->4 while 4 still exists). The mirror paths are
# course-scoped now, so they come from the store rather than being spelled out here.
_rel = lambda n: pptx_ingest.kb_rel(INS, n)
for n_, body in ((3, "three"), (4, "four")):
    db._exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
             (_rel(n_), body, "now"))
n = db.kb_rename_decks(INS, {3: 4, 4: 5})
stored = {r["path"]: r["content"]
          for r in db._query("SELECT path, content FROM kb_files WHERE path LIKE 'decks/%'")}
check("a chained rename moves both rows", n == 2, f"moved {n}")
check("…without one clobbering the other",
      stored.get(_rel(4)) == "three" and stored.get(_rel(5)) == "four", str(stored))
check("…and leaves no temporary path behind",
      not [k for k in stored if "__moving__" in k], str(list(stored)))
check("…and frees the number that moved", _rel(3) not in stored, str(list(stored)))
# The mirror path must carry the COURSE, or two courses' decks share one row.
check("…and the mirror path is scoped to the course",
      pptx_ingest.course_slug(INS) in _rel(4)
      and _rel(4) != pptx_ingest.kb_rel(OTHER, 4), _rel(4))

# Inserting in the MIDDLE leaves the rows above it alone. Compared against the state
# actually observed rather than hardcoded numbers, so adding a check above cannot
# quietly invalidate this one.
st, r = http("GET", f"/curriculum?course={INS.replace(' ', '%20')}")
was = {int(x["session_no"]): (x.get("session_name") or "") for x in (r.get("rows") or [])}
AT = 3
st, r = http("POST", "/curriculum/insert", {"at_session_no": AT, "course": INS})
now = {int(x["session_no"]): (x.get("session_name") or "") for x in (r.get("rows") or [])}
above_kept = all(now.get(n) == name for n, name in was.items() if n < AT)
below_moved = all(now.get(n + 1) == name for n, name in was.items() if n >= AT)
check("a middle insert leaves every row ABOVE it untouched", above_kept,
      f"{sorted(was.items())} -> {sorted(now.items())}")
check("…and moves every row from that point down by one", below_moved,
      f"{sorted(was.items())} -> {sorted(now.items())}")
check("…and the new row is the blank one at that position", now.get(AT) == "",
      f"got {now.get(AT)!r}")
st, r = http("POST", "/curriculum/insert", {"at_session_no": 0, "course": INS})
check("session 0 is rejected", st == 400, f"got {st}")

print("\n== deleting a session closes the gap behind it ==")
DEL = "Delete Order Course"
for n in (1, 2, 3, 4):
    db.curriculum_upsert(DEL, n, session_name=f"original {n}",
                         key_takeaways=[f"takeaway {n}"],
                         ppt_link=f"https://docs.google.com/presentation/d/E{n}/edit")
for n in (2, 3, 4):                       # every one of them has an extracted deck
    pptx_ingest.put_deck(DEL, n, {
        "session_no": n, "deck_title": f"deck {n}", "n_slides": 1,
        "slides": [{"n": 1, "title": "T", "text": "x"}]})

st, r = http("DELETE", f"/curriculum/2?course={DEL.replace(' ', '%20')}")
check("DELETE -> 200", st == 200, f"got {st}: {detail(r)}")
rows = {int(x["session_no"]): (x.get("session_name") or "") for x in (r.get("rows") or [])}
check("the numbers close up — no gap where 2 was",
      sorted(rows) == [1, 2, 3], str(sorted(rows)))
check("…and the rows below moved up, keeping their content",
      rows.get(2) == "original 3" and rows.get(3) == "original 4", str(sorted(rows.items())))
check("…the row above is untouched", rows.get(1) == "original 1", str(rows.get(1)))
# The decks must follow, and the deleted session's own deck must be gone rather than
# left sitting on the number the next session just took.
def deck_at(n):
    d = pptx_ingest.get_deck(DEL, n)
    return d["deck_title"] if d else None
check("session 3's deck is now session 2's", deck_at(2) == "deck 3", str(deck_at(2)))
check("session 4's deck is now session 3's", deck_at(3) == "deck 4", str(deck_at(3)))
check("nothing is left on the old highest number", deck_at(4) is None, str(deck_at(4)))
check("the deleted session's own deck is gone",
      "deck 2" not in [deck_at(n) for n in (1, 2, 3, 4)],
      str([deck_at(n) for n in (1, 2, 3, 4)]))
# Deleting the LAST session has nothing to shift, and must not disturb the rest.
st, r = http("DELETE", f"/curriculum/3?course={DEL.replace(' ', '%20')}")
rows = {int(x["session_no"]): (x.get("session_name") or "") for x in (r.get("rows") or [])}
check("deleting the last session shifts nothing", r.get("shifted") == 0, str(r.get("shifted")))
check("…and leaves the others alone",
      sorted(rows) == [1, 2] and rows[1] == "original 1", str(sorted(rows.items())))
# What FUTURE runs read is the point of all of this.
sess = course_loader.load_sessions(None, course=DEL)
check("generation reads the closed-up numbering",
      [(s.number, s.name) for s in sess] == [(1, "original 1"), (2, "original 3")],
      str([(s.number, s.name) for s in sess]))

print("\n== …but a document already generated keeps its own number ==")
# Explicitly asked for: renumber the curriculum for FUTURE runs, never the history. A
# finished document records what was generated, under the number it was generated for.
db.create_run("histrun", user_email=USER["email"], course=DEL, team_id=None,
              session_no=4, title="original 4", enforce_time=True)
db.finish_run("histrun", status="done", accepted=True)
before = next((x for x in db.runs() if x["id"] == "histrun"), {})
for at in (1, 2):
    http("POST", "/curriculum/insert", {"at_session_no": at, "course": DEL})
http("DELETE", f"/curriculum/1?course={DEL.replace(' ', '%20')}")
after = next((x for x in db.runs() if x["id"] == "histrun"), {})
check("an inserted and a deleted session do not move a finished run",
      after.get("session_no") == before.get("session_no") == 4,
      f"{before.get('session_no')} -> {after.get('session_no')}")
check("…and it keeps the title it was generated under",
      after.get("title") == "original 4", str(after.get("title")))

print("\n== a course profile round-trips over HTTP ==")
st, r = http("GET", f"/course-profile?course={COURSE.replace(' ', '%20')}")
check("GET /course-profile -> 200", st == 200, f"got {st}: {detail(r)}")
check("…it reports what applies, what was set, and what inherit means",
      all(k in r for k in ("profile", "overrides", "defaults")), str(sorted(r)))
check("…and an untouched course inherits everything",
      r["overrides"] == {} and r["profile"]["source"] == "harness default",
      str(r.get("overrides")))
st, r = http("POST", "/course-profile", {"course": COURSE, "profile": {
    "market_reference_platforms": ["react.dev", "MDN Web Docs"],
    "course_type": "interview"}})
check("POST /course-profile -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and it applies", r["profile"]["market_reference_platforms"] == ["react.dev", "MDN Web Docs"],
      str(r["profile"]["market_reference_platforms"]))
st, r = http("POST", "/course-profile", {"course": COURSE, "profile": {"nonsense": 1}})
check("an unknown key is refused -> 400", st == 400, f"got {st}")
check("…and says which key", "nonsense" in detail(r), detail(r))
st, r = http("POST", "/course-profile", {"course": COURSE,
                                         "profile": {"gates": {"rubric_min_total": 10}}})
check("lowering the pass bar is refused -> 400", st == 400, f"got {st}")
check("…and says why", "lower" in detail(r).lower(), detail(r))
st, r = http("GET", f"/course-profile?course={OTHER.replace(' ', '%20')}")
check("another course is untouched by all of that",
      r["overrides"] == {}, str(r.get("overrides")))

print("\n== course skills over HTTP: draft, approve, retire ==")
# What a course is written under is its OWNER's to decide — the same rule deletion uses,
# and for the same reason: working on a course and setting the rules every document it
# will ever produce is written under are different powers. This fixture's course was
# built by calling the database directly, so it has no owner; a course created the normal
# way is claimed by whoever creates it.
db.claim_course(COURSE, USER["email"])
st, r = http("GET", f"/skills?course={COURSE.replace(' ', '%20')}")
check("GET /skills -> 200", st == 200, f"got {st}: {detail(r)}")
check("…a fresh course has none", r.get("skills") == [], str(r.get("skills")))
st, r = http("POST", "/skills", {"course": COURSE,
                                 "text": "Explain each snippet line by line."})
check("POST /skills -> 200", st == 200, f"got {st}: {detail(r)}")
_sid = r.get("id")
check("…it starts as a draft",
      r["skills"][0]["status"] == "draft", str(r["skills"][0]["status"]))
st, b = http("GET", "/bootstrap")
st, r = http("POST", f"/skills/{_sid}/approve?course={COURSE.replace(' ', '%20')}")
check("approving it -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and it now applies", r["skills"][0]["status"] == "approved",
      str(r["skills"][0]["status"]))
st, r = http("POST", "/skills", {"course": COURSE, "text": "x",
                                 "check": {"assert": "run_python"}})
check("a check outside the vocabulary is refused -> 400", st == 400, f"got {st}")
check("…and says what is allowed", "block_present" in detail(r), detail(r))
st, r = http("POST", f"/skills/{_sid}/edit", {"course": COURSE,
                                              "text": "Explain each snippet, line by line."})
check("editing -> 200", st == 200, f"got {st}: {detail(r)}")
check("…sends it back to draft", r["skills"][0]["status"] == "draft",
      str(r["skills"][0]["status"]))
st, r = http("DELETE", f"/skills/{_sid}?course={COURSE.replace(' ', '%20')}")
check("retiring -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and it leaves the live list", r.get("skills") == [], str(r.get("skills")))
st, r = http("GET", f"/skills?course={COURSE.replace(' ', '%20')}&include_retired=true")
check("…but is kept for the record", len(r.get("skills") or []) == 1,
      str(r.get("skills")))
st, r = http("POST", "/skills/9999/approve?course=" + COURSE.replace(" ", "%20"))
check("a skill that is not there -> 404", st == 404, f"got {st}")

print("\n== prerequisites over HTTP ==")
st, r = http("GET", f"/prereqs?course={COURSE.replace(' ', '%20')}")
check("GET /prereqs -> 200", st == 200, f"got {st}: {detail(r)}")
check("…a fresh course has none", r.get("prereqs") == [], str(r.get("prereqs")))
check("…and it offers the courses that could be one",
      OTHER in (r.get("available") or []), str(r.get("available")))
st, r = http("POST", "/prereqs", {"course": COURSE, "prereq": OTHER})
check("attaching one -> 200", st == 200, f"got {st}: {detail(r)}")
check("…it is listed", [p["prereq"] for p in r["prereqs"]] == [OTHER], str(r["prereqs"]))
check("…and the coverage report comes with it",
      "topics_indexed" in (r.get("report") or {}), str(r.get("report")))
st, r = http("POST", "/prereqs", {"course": COURSE, "prereq": OTHER})
check("attaching it twice -> 409", st == 409, f"got {st}")
st, r = http("POST", "/prereqs", {"course": COURSE, "prereq": COURSE})
check("a course as its own prerequisite -> 400", st == 400, f"got {st}")
st, r = http("DELETE", f"/prereqs?course={COURSE.replace(' ', '%20')}"
                       f"&prereq={OTHER.replace(' ', '%20')}")
check("detaching -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and it is gone", r.get("prereqs") == [], str(r.get("prereqs")))

print("\n== a prerequisite taught SOMEWHERE ELSE ==")
# The common case: the learners did a course elsewhere and all anybody has is its slides.
st, r = http("POST", "/prereqs/external", {"course": COURSE, "name": "JS Elsewhere",
                                           "links": []})
check("no links -> 400", st == 400, f"got {st}")
check("…and says why a name alone is not enough", "slides" in detail(r), detail(r))
st, r = http("POST", "/prereqs/external", {"course": COURSE, "name": OTHER,
                                           "links": ["https://x/1"]})
check("a name that IS a course here -> 409", st == 409, f"got {st}")
check("…pointing at the simpler path", "attach it as a prerequisite" in detail(r),
      detail(r))
st, r = http("POST", "/prereqs/external", {
    "course": COURSE, "name": "JS Elsewhere",
    "links": ["https://docs.google.com/presentation/d/JS1/edit"]})
check("a real one -> 200", st == 200, f"got {st}: {detail(r)}")
check("…it returns a job, because fetching decks takes time",
      bool(r.get("job_id")), str(r))
check("…and is linked as external",
      any(p["prereq"] == "JS Elsewhere" and p["kind"] == "external"
          for p in r.get("prereqs") or []), str(r.get("prereqs")))
st, r = http("POST", "/prereqs/external", {
    "course": COURSE, "name": "JS Elsewhere", "links": ["https://x/1"]})
check("declaring the same one twice -> 409", st == 409, f"got {st}")
st, r = http("DELETE", f"/prereqs?course={COURSE.replace(' ', '%20')}&prereq=JS%20Elsewhere")
check("removing it -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and its deck store goes with it, since nothing else owns it",
      not pptx_ingest.prereq_decks_dir(COURSE, "JS Elsewhere").is_dir(),
      str(pptx_ingest.prereq_decks_dir(COURSE, "JS Elsewhere")))

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
