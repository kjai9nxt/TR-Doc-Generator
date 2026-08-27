"""The three edits a reviewer makes DURING guided review, over real HTTP.

    python -m evals.test_review_edits        # no API key needed, ~5 seconds

WHAT IS UNDER TEST, and why each exists:

  1. SPLITTING A SLIDE. A slide carrying too much for one slide is the commonest
     structural note a reviewer has, and the only way to act on it was to regenerate the
     whole chunk with "split this" and hope — a model call to move content that already
     existed, and a re-draft free to rewrite the slides either side of it. The split is
     deterministic now: the content is divided, not rewritten. The hard part is the
     NUMBERING — every slide after it moves, in that chunk and in all the later ones, and
     the reviewer is reading those numbers on screen while they work.

  2. A REVIEWER NOTE THAT STICKS. Most notes are about the document, not the one chunk in
     front of you ("stop restating the takeaway", "plainer language"). Applying one meant
     retyping it into every remaining chunk in turn, waiting for each. Ticking "apply to
     every chunk after this one" fans it out AND keeps it as a standing instruction, so a
     chunk redrafted later for some other reason still obeys it.

  3. The finalize button's loading state — UI only, covered by frontend/test/ui_smoke.mjs.

The LLM is stubbed (patch/generate), AFTER the handler has fully run, so everything this
is meant to catch — the numbering, the coverage remap, which chunks receive which
instruction, the guardrail-shaped choices the split makes — is real. The database is a
throwaway under TR_DATA_DIR.
"""
import copy
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

TMP = tempfile.mkdtemp(prefix="tr_review_edits_")
os.environ["TR_DATA_DIR"] = TMP
os.environ.pop("TURSO_DATABASE_URL", None)
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
from src import db, docx_writer                  # noqa: E402

PORT = 8805
BASE = f"http://127.0.0.1:{PORT}/api"
USER = {"email": "reviewer@nxtwave.co.in", "name": "Reviewer", "is_admin": False}
COURSE = "Review Edits Course"

server.app.dependency_overrides[server.current_user] = lambda: USER
server._guided_generate_all = lambda gid: None          # chunks are installed by hand

# The two model calls a regeneration can make. Stubbed to record WHAT they were asked
# for — which is the question this file is about — and to return a chunk that is
# recognisably the answer to it.
PATCHED: list = []


ADD_A_SLIDE = []          # set to [True] to make the next patch grow its chunk by one


def _fake_patch(gid, index, reason):
    PATCHED.append({"index": index, "reason": reason})
    with server._lock:
        chunk = copy.deepcopy(server.GUIDED[gid]["chunks"][index])
    sec = chunk["fragment"].get("section", chunk["fragment"])
    for s in sec.get("slides") or []:
        s["speaker_notes"] = f"NOTE APPLIED. {reason[:60]}"
    if ADD_A_SLIDE:
        ADD_A_SLIDE.pop()
        # `n: None` is exactly what patcher.apply_section_patch leaves on an added
        # slide — numbering is assigned, never patched.
        extra = copy.deepcopy((sec.get("slides") or [{}])[-1])
        extra.update({"n": None, "title": "An Added Slide"})
        sec.setdefault("slides", []).append(extra)
    chunk["markdown"] = docx_writer.chunk_to_markdown(chunk["kind"], chunk["fragment"])
    return chunk, {"mode": "patch", "slides_changed": [s.get("n") for s in sec.get("slides") or []],
                   "changed_share": 0.2}


server._patch_one = _fake_patch
server._gen_one = lambda gid, index, prior, reason=None: (_ for _ in ()).throw(
    AssertionError("the full re-draft path should not be reached in this test"))
# Feedback distillation is a model call and is irrelevant here; it is best-effort in the
# handler, so stubbing it keeps the test offline and fast rather than changing behaviour.
import src.learning                              # noqa: E402
src.learning.record_feedback = lambda *a, **k: None
import src.regen_log                             # noqa: E402
src.regen_log.record = lambda *a, **k: None

db.init()
db.upsert_user(USER["email"], USER["name"])
db.claim_course(COURSE, USER["email"])
db.curriculum_upsert(COURSE, 5, topic="T", session_name="Splitting And Sticking",
                     key_takeaways=["Takeaway one", "Takeaway two", "Takeaway three"])


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


def detail(payload):
    d = payload.get("detail", payload)
    return d.get("message", str(d)) if isinstance(d, dict) else str(d)


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


def slide(n, title, role, *, content, analogy=None):
    s = {"n": n, "title": title, "heading": "A heading", "subheading": "A subheading",
         "role": role, "content": content, "visual_guidance": "A diagram",
         "speaker_notes": "Cue it. Exam hook."}
    if analogy:
        s["analogy"] = analogy
    return s


def section(name, slides, takeaway, refs):
    return {"section": {"name": name, "slides": slides},
            "coverage": {"takeaway": takeaway,
                         "sub_concepts": [{"name": f"sub {n}", "slide": n} for n in refs]}}


def install_chunks(gid):
    """A run in review: the derived opening, then three section chunks."""
    chunks = [
        {"kind": "opening",
         "fragment": {"recap": {"prev_session_no": 4, "prev_session_name": "Before",
                                "bullets": ["one", "two"]},
                      "agenda": ["Takeaway one", "Takeaway two", "Takeaway three"]},
         "markdown": ""},
        {"kind": "section", "fragment": section("One", [
            slide(1, "First Slide Of Section One", "concept_intro",
                  content=[{"type": "text", "text": "Alpha here. Beta there. Gamma too."},
                           {"type": "bullets", "items": ["b one", "b two", "b three"]}],
                  analogy="A post office — just as a router forwards a packet."),
            slide(2, "Second Slide", "mechanism",
                  content=[{"type": "bullets", "items": ["only one item"]}]),
        ], "Takeaway one", [1, 2])},
        {"kind": "section", "fragment": section("Two", [
            slide(3, "Third Slide", "overview",
                  content=[{"type": "text", "text": "One sentence only."}]),
            slide(4, "Fourth Slide", "mechanism",
                  content=[{"type": "bullets", "items": ["x", "y"]}]),
        ], "Takeaway two", [3, 4])},
        {"kind": "section", "fragment": section("Three", [
            slide(5, "Fifth Slide", "comparison",
                  content=[{"type": "table", "columns": ["a", "b"],
                            "rows": [["1", "2"], ["3", "4"], ["5", "6"]]}]),
        ], "Takeaway three", [5])},
    ]
    for c in chunks:
        c["markdown"] = docx_writer.chunk_to_markdown(c["kind"], c["fragment"])
    with server._lock:
        st = server.GUIDED[gid]
        st["chunks"] = chunks
        st["total"] = len(chunks)
        st["labels"] = ["Opening", "Takeaway one", "Takeaway two", "Takeaway three"]
        st["index"] = len(chunks)
        st["status"] = "reviewing"
        st["standing_notes"] = []
    server._guided_save(gid)


def start_run():
    st, r = http("POST", "/guided/start",
                 {"session_no": 5, "use_judge": True, "enforce_time": True,
                  "course": COURSE})
    assert st == 200, (st, r)
    gid = r["guided_id"]
    install_chunks(gid)
    return gid


def wait_reviewing(gid, timeout=25):
    """Regeneration runs in a background thread; wait for it to hand control back."""
    for _ in range(int(timeout * 10)):
        s, v = http("GET", f"/guided/{gid}")
        if s == 200 and v.get("status") == "reviewing":
            return v
        time.sleep(0.1)
    return http("GET", f"/guided/{gid}")[1]


def numbers(view):
    """Every slide number in the run, in document order."""
    return [sl["n"] for c in view["chunks"] for sl in (c.get("slides") or [])]


gid = start_run()

print("\n== the run starts numbered 1..N ==")
st, view = http("GET", f"/guided/{gid}")
check("GET the run -> 200", st == 200, f"got {st}")
check("slides run 1..5 across the chunks", numbers(view) == [1, 2, 3, 4, 5],
      str(numbers(view)))
check("the opening reports no slides to split",
      (view["chunks"][0].get("slides") or []) == [], str(view["chunks"][0].get("slides")))

print("\n== splitting one slide in two ==")
st, r = http("POST", f"/guided/{gid}/split", {"index": 1, "slide_n": 1})
check("POST /split -> 200", st == 200, f"got {st}: {detail(r)}")
check("the chunk now has three slides", len(r["chunks"][1]["slides"]) == 3,
      str(r["chunks"][1]["slides"]))
with server._lock:
    sec1 = server.GUIDED[gid]["chunks"][1]["fragment"]["section"]["slides"]
check("the first half keeps the original title",
      sec1[0]["title"] == "First Slide Of Section One", str(sec1[0]["title"]))
check("…and the content is DIVIDED, not duplicated",
      [b["type"] for b in sec1[0]["content"]] == ["text", "bullets"]
      and [b["type"] for b in sec1[1]["content"]] == ["text"],
      str([[b["type"] for b in s["content"]] for s in sec1[:2]]))
# The document is graded on the SHARE of slides carrying a prose block
# (constraints.content.min_slides_with_text_share), so handing the only paragraph to one
# half would make a structural edit fail a content gate. The prose is divided too.
check("…with prose on BOTH halves, not just the first",
      all(any(b["type"] == "text" for b in s["content"]) for s in sec1[:2]),
      str([[b["type"] for b in s["content"]] for s in sec1[:2]]))
check("…and no sentence is lost between them",
      " ".join(b["text"] for s in sec1[:2] for b in s["content"] if b["type"] == "text")
      == "Alpha here. Beta there. Gamma too.",
      str([b["text"] for s in sec1[:2] for b in s["content"] if b["type"] == "text"]))
check("the second half is marked a continuation",
      sec1[1]["title"].endswith("(continued)"), str(sec1[1]["title"]))
check("…and stays inside the title word cap", len(sec1[1]["title"].split()) <= 8,
      str(len(sec1[1]["title"].split())))
# Two gates decide this: an analogy is REQUIRED on a first introduction and BANNED on
# every other role, and the same analogy may not appear on two slides.
check("the analogy stays on the first half only",
      bool(sec1[0].get("analogy")) and not sec1[1].get("analogy"),
      f"{bool(sec1[0].get('analogy'))} / {bool(sec1[1].get('analogy'))}")
check("…so the continuation is given a non-intro role",
      sec1[1].get("role") == "mechanism", str(sec1[1].get("role")))
check("it inherits every field a slide is required to carry",
      all(str(sec1[1].get(f) or "").strip()
          for f in ("heading", "subheading", "content", "visual_guidance", "speaker_notes")),
      str({f: sec1[1].get(f) for f in ("heading", "subheading", "visual_guidance", "speaker_notes")}))

print("\n== …and every slide after it is renumbered, in every chunk ==")
check("the run is contiguous 1..6 again", numbers(r) == [1, 2, 3, 4, 5, 6],
      str(numbers(r)))
check("the LATER chunks moved up one, not just this one",
      [sl["n"] for sl in r["chunks"][2]["slides"]] == [4, 5]
      and [sl["n"] for sl in r["chunks"][3]["slides"]] == [6],
      str([[sl["n"] for sl in c["slides"]] for c in r["chunks"][1:]]))
check("the endpoint reports which chunks it renumbered",
      r.get("renumbered_chunks") == [1, 2, 3], str(r.get("renumbered_chunks")))
check("a later chunk's MARKDOWN carries its new numbers",
      "Slide 4:" in r["chunks"][2]["markdown"] and "Slide 3:" not in r["chunks"][2]["markdown"],
      r["chunks"][2]["markdown"][:120])
# A coverage entry pointing at a number that now means a different slide is a HARD
# guardrail failure at finalize, not a cosmetic nit.
with server._lock:
    covs = [(c["fragment"].get("coverage") or {}).get("sub_concepts")
            for c in server.GUIDED[gid]["chunks"][1:]]
# THREE refs in the split chunk, not two: a slide nothing in the coverage map points at
# fails the "teaches nothing the coverage map points at, so nothing on the agenda promised
# it" gate — so the continuation inherits its half's reference. It is the same sub-concept,
# now taught across two slides, so it keeps its name.
check("coverage references followed the renumbering, and cover the new slide",
      [[s["slide"] for s in (cv or [])] for cv in covs] == [[1, 2, 3], [4, 5], [6]],
      str([[s["slide"] for s in (cv or [])] for cv in covs]))
check("…and the continuation's reference names the same sub-concept",
      [s["name"] for s in covs[0]][:2] == ["sub 1", "sub 1"],
      str([s["name"] for s in covs[0]]))

print("\n== a split that cannot be done is refused, not half-done ==")
before = numbers(http("GET", f"/guided/{gid}")[1])
st, r = http("POST", f"/guided/{gid}/split", {"index": 2, "slide_n": 4})
check("a one-sentence paragraph -> 400", st == 400, f"got {st}")
check("…saying what to do instead", "regenerate" in detail(r).lower(), detail(r))
st, r = http("POST", f"/guided/{gid}/split", {"index": 1, "slide_n": 3})
check("a one-item bullet list -> 400", st == 400, f"got {st}: {detail(r)}")
st, r = http("POST", f"/guided/{gid}/split", {"index": 0, "slide_n": 1})
check("the opening chunk -> 400", st == 400, f"got {st}")
check("…because its recap and agenda come from the curriculum",
      "curriculum" in detail(r), detail(r))
st, r = http("POST", f"/guided/{gid}/split", {"index": 1, "slide_n": 99})
check("a slide that is not there -> 400", st == 400, f"got {st}")
st, r = http("POST", f"/guided/{gid}/split", {"index": 9, "slide_n": 1})
check("a chunk that is not there -> 400", st == 400, f"got {st}")
check("…and nothing moved through any of that",
      numbers(http("GET", f"/guided/{gid}")[1]) == before, str(before))

print("\n== a table and a paragraph split too, not only bullet lists ==")
st, r = http("POST", f"/guided/{gid}/split", {"index": 3, "slide_n": 6})
check("splitting a 3-row table -> 200", st == 200, f"got {st}: {detail(r)}")
with server._lock:
    rows = [b["rows"] for s in server.GUIDED[gid]["chunks"][3]["fragment"]["section"]["slides"]
            for b in s["content"]]
check("the rows are divided between the two slides",
      rows == [[["1", "2"], ["3", "4"]], [["5", "6"]]], str(rows))
check("…and both keep the header columns",
      all(b.get("columns") == ["a", "b"]
          for s in server.GUIDED[gid]["chunks"][3]["fragment"]["section"]["slides"]
          for b in s["content"]))
st, r = http("POST", f"/guided/{gid}/split", {"index": 1, "slide_n": 1})
check("splitting a 3-sentence paragraph -> 200", st == 200, f"got {st}: {detail(r)}")
with server._lock:
    texts = [b["text"] for s in server.GUIDED[gid]["chunks"][1]["fragment"]["section"]["slides"][:2]
             for b in s["content"] if b["type"] == "text"]
# Slide 1 was already split once above, so its prose is down to two sentences by now —
# splitting again gives one each, which is the smallest a text block can usefully be.
check("the sentences are divided, and none is lost",
      texts == ["Alpha here.", "Beta there."], str(texts))

print("\n== a split survives a restart mid-review ==")
# Guided state is checkpointed after every mutation, so a redeploy or a spun-down
# instance must not lose a structural edit the reviewer made by hand.
after = numbers(http("GET", f"/guided/{gid}")[1])
with server._lock:
    server.GUIDED.pop(gid, None)
st, view = http("GET", f"/guided/{gid}")
check("the run rehydrates", st == 200, f"got {st}: {detail(view)}")
check("…with the split and the numbering intact", numbers(view) == after, str(numbers(view)))

print("\n== a regeneration that changes the slide count renumbers too ==")
# The consequence of showing the reviewer real slide numbers during review. A patch may
# ADD or REMOVE a slide, and a full re-draft comes back at whatever length it likes.
# That used to be left to assembly, so the review pane showed "Slide None" for an added
# slide and stale numbers in every later chunk until the document was finished — which is
# no longer tolerable now that the reviewer picks a slide BY NUMBER to split it.
_g = start_run()
check("it starts contiguous", numbers(http("GET", f"/guided/{_g}")[1]) == [1, 2, 3, 4, 5],
      str(numbers(http("GET", f"/guided/{_g}")[1])))
ADD_A_SLIDE.append(True)
st, _ = http("POST", f"/guided/{_g}/regenerate", {"index": 1, "reason": "Add one."})
view = wait_reviewing(_g)
check("the chunk grew by one", len(view["chunks"][1]["slides"]) == 3,
      str([sl["n"] for sl in view["chunks"][1]["slides"]]))
check("no slide is left unnumbered",
      all(sl["n"] is not None for c in view["chunks"] for sl in (c.get("slides") or [])),
      str(numbers(view)))
check("the whole run is contiguous again", numbers(view) == [1, 2, 3, 4, 5, 6],
      str(numbers(view)))
check("…and the LATER chunks moved, not just this one",
      [sl["n"] for sl in view["chunks"][2]["slides"]] == [4, 5],
      str([sl["n"] for sl in view["chunks"][2]["slides"]]))
check("…which the markdown on screen reflects",
      "Slide 4:" in view["chunks"][2]["markdown"], view["chunks"][2]["markdown"][:100])
# …and the numbers on screen are the ones the split endpoint resolves against, which is
# the whole reason the renumbering cannot wait for assembly: the reviewer picks a slide
# BY NUMBER, from what they are reading.
st, r = http("POST", f"/guided/{_g}/split", {"index": 2, "slide_n": 3})
check("the number that chunk USED to show no longer resolves",
      st == 400 and "not in this chunk" in detail(r), f"{st}: {detail(r)}")
st, r = http("POST", f"/guided/{_g}/split", {"index": 2, "slide_n": 4})
check("…and the one it shows now does",
      "not in this chunk" not in detail(r), detail(r))

print("\n== a split introduces no guardrail failure of its own ==")
# THE CHECK THAT MATTERS MOST. The split makes several choices for gate reasons — which
# half keeps the analogy, what role the continuation gets, trimming the title to fit the
# word cap, giving the second slide every required field. Asserting those one by one says
# the code does what it meant to; running the real guardrails over the assembled document
# says it was ENOUGH. A hand-built fixture fails plenty of unrelated gates (minimum slide
# count, worked examples, coverage), so the comparison is before-vs-after: the split must
# add nothing.
from guardrails import guardrails                             # noqa: E402
from src import pipeline, course_loader                       # noqa: E402

_gid = start_run()


def assembled_failures(g):
    with server._lock:
        chunks = copy.deepcopy(server.GUIDED[g]["chunks"])
        cur = server.GUIDED[g]["cur"]
        nxt = server.GUIDED[g]["nxt"]
    opening = chunks[0]["fragment"]
    sections = [(c["fragment"].get("section", c["fragment"])) for c in chunks[1:]]
    coverage = [c["fragment"].get("coverage") or {} for c in chunks[1:]]
    doc = pipeline.assemble_doc(cur, nxt, opening, sections, coverage)
    return set(guardrails.check(doc, cur, False, False).failures)


import re                                                    # noqa: E402


def normalise(fails):
    """Failure text with slide NUMBERS removed.

    A split renumbers the document, so a pre-existing failure reads "Slide 5: …" before
    and "Slide 6: …" after. Compared verbatim, every one of those looks like a failure the
    split introduced. The numbers are what changed legitimately; the defects are not.
    """
    out = set()
    for f in fails:
        f = re.sub(r"\bslide\(?s?\)?\s*\[?\d+(?:\s*,\s*\d+)*\]?", "slide N", f, flags=re.I)
        out.add(re.sub(r"\b\d+\b", "N", f))
    return out


before_f = normalise(assembled_failures(_gid))
st, _ = http("POST", f"/guided/{_gid}/split", {"index": 1, "slide_n": 1})
check("the split lands", st == 200, f"got {st}")
after_f = normalise(assembled_failures(_gid))
introduced = sorted(after_f - before_f)
check("the assembled document gains no guardrail failure", not introduced,
      "introduced: " + " | ".join(x[:120] for x in introduced))
# Named individually as well, because these are the four the split could plausibly break
# and a bare set-difference would not say which.
for what, needle in (("a missing required field", "missing '"),
                     ("an analogy on the wrong role", "analogy"),
                     ("an over-long title", "title"),
                     ("broken slide numbering", "numbered")):
    check(f"…in particular, no {what}",
          not any(needle in x.lower() for x in introduced),
          " | ".join(x[:100] for x in introduced if needle in x.lower()))

print("\n== a note can be made to apply to every following chunk ==")
gid = start_run()
PATCHED.clear()
st, r = http("POST", f"/guided/{gid}/regenerate",
             {"index": 1, "reason": "Drop every analogy.", "apply_to_following": True})
check("POST /regenerate -> 200", st == 200, f"got {st}: {detail(r)}")
check("…and it echoes that the note is being fanned out",
      r.get("apply_to_following") is True, str(r))
view = wait_reviewing(gid)
check("the run comes back to review", view.get("status") == "reviewing",
      str(view.get("status")))
check("every chunk after it received the note",
      sorted(x["index"] for x in PATCHED) == [1, 2, 3],
      str([x["index"] for x in PATCHED]))
check("…and it is the reviewer's own words",
      all("Drop every analogy." in x["reason"] for x in PATCHED),
      str([x["reason"][:40] for x in PATCHED]))
check("the note is shown back as a standing instruction",
      [(n["from_index"], n["reason"]) for n in view.get("standing_notes") or []]
      == [(1, "Drop every analogy.")], str(view.get("standing_notes")))

print("\n== …and it keeps applying to a chunk regenerated LATER ==")
# A standing instruction that stopped applying the moment a chunk was redrafted for some
# other reason would be worse than not having it: the reviewer has no way to see that it
# lapsed.
PATCHED.clear()
st, r = http("POST", f"/guided/{gid}/regenerate",
             {"index": 3, "reason": "Shorten the headings."})
check("regenerating a covered chunk on its own -> 200", st == 200, f"got {st}: {detail(r)}")
wait_reviewing(gid)
check("only that chunk was touched", [x["index"] for x in PATCHED] == [3],
      str([x["index"] for x in PATCHED]))
sent = PATCHED[0]["reason"] if PATCHED else ""
check("…and it carried BOTH the new note and the standing one",
      "Shorten the headings." in sent and "Drop every analogy." in sent, sent[:200])

print("\n== two standing notes are stated once each, not compounded ==")
# The instruction actually sent is composed from the reviewer's words plus the standing
# notes. Passing that COMPOSED text on to the fan-out made it compose again, so every
# earlier note was repeated — a prompt that says the same rule three times, growing with
# each note the reviewer adds.
PATCHED.clear()
st, r = http("POST", f"/guided/{gid}/regenerate",
             {"index": 1, "reason": "Shorter headings.", "apply_to_following": True})
check("a second standing note -> 200", st == 200, f"got {st}: {detail(r)}")
view = wait_reviewing(gid)
check("both are now standing",
      [n["reason"] for n in view.get("standing_notes") or []]
      == ["Drop every analogy.", "Shorter headings."], str(view.get("standing_notes")))
later = [x for x in PATCHED if x["index"] > 1]
check("the later chunks got both", later and all(
      "Drop every analogy." in x["reason"] and "Shorter headings." in x["reason"]
      for x in later), str([x["reason"][:70] for x in later]))
check("…each stated exactly once", later and all(
      x["reason"].count("Drop every analogy.") == 1
      and x["reason"].count("Shorter headings.") == 1 for x in later),
      str([(x["reason"].count("Drop every analogy."),
            x["reason"].count("Shorter headings.")) for x in later]))
check("…and the standing block is not repeated either",
      later and all(x["reason"].count("STANDING") <= 1 for x in later),
      str([x["reason"].count("STANDING") for x in later]))

print("\n== a chunk BEFORE the note is left alone ==")
PATCHED.clear()
st, r = http("POST", f"/guided/{gid}/regenerate",
             {"index": 1, "reason": "Just this one."})
wait_reviewing(gid)
check("regenerating the chunk the note was given on -> 200", st == 200, f"got {st}")
check("…touches only it", [x["index"] for x in PATCHED] == [1],
      str([x["index"] for x in PATCHED]))
check("…and does not re-send the note to itself",
      PATCHED and PATCHED[0]["reason"].count("Drop every analogy.") == 0,
      PATCHED[0]["reason"][:200] if PATCHED else "")

print("\n== the standing notes reach the FINALIZE repair too ==")
# finalize's repair pass edits slides the reviewer already approved — for length, a hard
# guardrail failure or a wrong fact. Without the reviewer's rules it edits them knowing
# nothing about them, so a trim can put back exactly what they had removed everywhere and
# the only sign of it is in the finished document.
FINALIZE_ARGS = {}
_real_finalize = server.pipeline.finalize


def _capture_finalize(*a, **k):
    """Record what finalize was called with and go no further.

    Deliberately NOT delegating to the real one: it grades with the LLM judge, and this
    suite is offline. What is under test is that the reviewer's standing instructions
    reach it at all — the prompt block itself is checked separately, just below.
    """
    FINALIZE_ARGS.update(k)
    raise RuntimeError("finalize stubbed — the run stays in review")


server.pipeline.finalize = _capture_finalize
gid_f = start_run()
http("POST", f"/guided/{gid_f}/regenerate",
     {"index": 1, "reason": "No analogies anywhere.", "apply_to_following": True})
wait_reviewing(gid_f)
st, r = http("POST", f"/guided/{gid_f}/finalize")
check("POST /finalize -> 200", st == 200, f"got {st}: {detail(r)}")
for _ in range(200):
    if FINALIZE_ARGS:
        break
    time.sleep(0.05)
check("finalize was handed the standing instructions",
      FINALIZE_ARGS.get("standing_notes") == ["No analogies anywhere."],
      str(FINALIZE_ARGS.get("standing_notes")))
server.pipeline.finalize = _real_finalize
# …and that they reach the repair PROMPT, not just the signature.
from src import context_builder                               # noqa: E402
block = context_builder.standing_notes_block(["No analogies anywhere.",
                                              "No analogies anywhere."])
check("the prompt block states the rule", "No analogies anywhere." in block, block[:80])
check("…once, however many times it was given",
      block.count("No analogies anywhere.") == 1, block)
check("…and says it must not be undone", "must not undo" in block, block[:200])
check("no block at all when there are no standing notes",
      context_builder.standing_notes_block([]) == "", repr(context_builder.standing_notes_block([])))

print("\n== without the tick, nothing else is touched ==")
gid = start_run()
PATCHED.clear()
st, r = http("POST", f"/guided/{gid}/regenerate",
             {"index": 1, "reason": "Only this chunk please."})
check("-> 200", st == 200, f"got {st}: {detail(r)}")
view = wait_reviewing(gid)
check("one chunk regenerated", [x["index"] for x in PATCHED] == [1],
      str([x["index"] for x in PATCHED]))
check("…and no standing instruction was recorded",
      (view.get("standing_notes") or []) == [], str(view.get("standing_notes")))

print("\n== a standing note survives a restart too ==")
gid = start_run()
http("POST", f"/guided/{gid}/regenerate",
     {"index": 1, "reason": "Plainer language throughout.", "apply_to_following": True})
wait_reviewing(gid)
with server._lock:
    server.GUIDED.pop(gid, None)
st, view = http("GET", f"/guided/{gid}")
check("the rehydrated run still carries it",
      [n["reason"] for n in view.get("standing_notes") or []]
      == ["Plainer language throughout."], str(view.get("standing_notes")))

print(f"\n{OK} passed, {FAIL} failed")
srv.should_exit = True
sys.exit(1 if FAIL else 0)
