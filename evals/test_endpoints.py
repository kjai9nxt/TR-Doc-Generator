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

print("\n== inserting a session renumbers the ones after it ==")
# Reported: inserting a row at the TOP of a 34-session course numbered it 35. A
# curriculum is an ordered list — the row you put first is session 1.
from src import pptx_ingest                                    # noqa: E402

INS = "Insert Order Course"
for n in (1, 2, 3):
    db.curriculum_upsert(INS, n, session_name=f"original {n}",
                         key_takeaways=[f"takeaway {n}"],
                         ppt_link=f"https://docs.google.com/presentation/d/D{n}/edit")
# Give session 2 an extracted deck, so we can check the deck follows its row.
pptx_ingest.DECKS_DIR.mkdir(parents=True, exist_ok=True)
(pptx_ingest.DECKS_DIR / "session_02.json").write_text(json.dumps(
    {"session_no": 2, "deck_title": "Deck of the second session", "n_slides": 3,
     "slides": [{"n": 1, "title": "T", "text": "x"}]}), encoding="utf-8")

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
moved = pptx_ingest.DECKS_DIR / "session_03.json"
check("the extracted deck moved with its session", moved.exists(),
      str(sorted(p.name for p in pptx_ingest.DECKS_DIR.glob("session_*.json"))))
if moved.exists():
    check("…and says so inside the file too",
          json.loads(moved.read_text()).get("session_no") == 3)
    check("…and did not leave a copy behind",
          not (pptx_ingest.DECKS_DIR / "session_02.json").exists())

# The deck move must also reach the CLOUD MIRROR. On the deployed instance the decks
# sit on an ephemeral disk and are restored from kb_files on boot, so a rename that
# never reached that table would be undone by the next restart — and the renumbered
# curriculum would then be pointing at the old decks.
_renames = []
_real_rename = db.kb_rename_decks
db.kb_rename_decks = lambda m: _renames.append(dict(m)) or 0
st, _ = http("POST", "/curriculum/insert", {"at_session_no": 2, "course": INS})
db.kb_rename_decks = _real_rename
check("renumbering moves the decks in the DB mirror too", len(_renames) == 1,
      f"kb_rename_decks called {len(_renames)} time(s)")

# And the mirror move itself, against a real table — including the chain that makes
# the naive version collide (3->4 while 4 still exists).
for rel, body in (("decks/session_03.json", "three"), ("decks/session_04.json", "four")):
    db._exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
             (rel, body, "now"))
n = db.kb_rename_decks({3: 4, 4: 5})
stored = {r["path"]: r["content"]
          for r in db._query("SELECT path, content FROM kb_files WHERE path LIKE 'decks/%'")}
check("a chained rename moves both rows", n == 2, f"moved {n}")
check("…without one clobbering the other",
      stored.get("decks/session_04.json") == "three"
      and stored.get("decks/session_05.json") == "four", str(stored))
check("…and leaves no temporary path behind",
      not [k for k in stored if "__moving__" in k], str(list(stored)))
check("…and frees the number that moved", "decks/session_03.json" not in stored, str(list(stored)))

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
    (pptx_ingest.DECKS_DIR / f"session_{n:02d}.json").write_text(
        json.dumps({"session_no": n, "deck_title": f"deck {n}", "n_slides": 1,
                    "slides": [{"n": 1, "title": "T", "text": "x"}]}), encoding="utf-8")

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
    p2 = pptx_ingest.DECKS_DIR / f"session_{n:02d}.json"
    return json.loads(p2.read_text())["deck_title"] if p2.exists() else None
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
