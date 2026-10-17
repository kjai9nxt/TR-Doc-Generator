"""An uploaded TR doc (.docx) as a session's course memory.

    python -m evals.test_trdoc_upload        # no API key needed, a few seconds

A session becomes "taught" when the agent holds what was taught in it. Besides its
deck (a Google Slides link or a Web Slides upload), a session can now hold its finished
TR doc. This suite asserts the contract:

  · the .docx is read slide by slide — recap and agenda are NOT this session's material;
  · the session leaves the generate list and appears in the taught index of the NEXT
    session's run;
  · a held TR doc is not overwritten by "Fetch new decks" (even forced) or by a later
    Web Slides upload, and survives the orphan-deck prune;
  · detaching it forgets it: the deck goes back to pending, or with no deck the session
    returns to the generate list.

The database and knowledge base are throwaways under TR_DATA_DIR.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_trdoc_")
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


from docx import Document                         # noqa: E402

from src import db, pptx_ingest, sync, trdoc_ingest   # noqa: E402

COURSE = "Networks"
db.init()


def make_docx(title: str, slides: list[tuple[str, str]], recap: str = "") -> bytes:
    import io
    d = Document()
    d.add_heading(title, 1)
    if recap:
        d.add_heading(f"RECAP: {recap}", 2)
        d.add_paragraph("RECAP-ONLY LINE about the previous session")
    d.add_heading("Agenda for Today's Session", 2)
    d.add_paragraph("AGENDA-ONLY LINE")
    d.add_heading("--------- SECTION 1: Basics", 2)
    for i, (t, body) in enumerate(slides, start=1):
        d.add_heading(f"Slide {i}: {t}", 3)
        d.add_paragraph(f"Heading: {t}")
        d.add_paragraph("Content:")
        d.add_paragraph(body)
        d.add_paragraph('Speaker Notes: "Explain it slowly."')
    d.add_heading("Key Takeaways", 2)
    d.add_paragraph("1. Basics: the thing")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def generate_list() -> set[int]:
    rows = db.curriculum(COURSE)
    return {r["session_no"] for r in rows if not db.deck_source(r)}


for n in (1, 2, 3):
    db.curriculum_upsert(COURSE, n, topic="T", session_name=f"Session {n} name",
                         key_takeaways=["1. Basics: the thing"])
db.curriculum_upsert(COURSE, 3, topic="T", session_name="Session 3 name",
                     key_takeaways=["1. Basics: the thing"],
                     ppt_link="https://docs.google.com/presentation/d/x")

print("extraction")
data = make_docx("Session 1 : Sockets", [("What a Socket Is", "A socket is an endpoint."),
                                         ("Ports", "A port names a process.")],
                 recap="Session 0 : Intro")
_, deck = trdoc_ingest.extract_trdoc(data, 1, "Session 1 name", "s1.docx")
titles = [s["title"] for s in deck["slides"]]
check("slides read from 'Slide N:' headings", titles[:2] == ["What a Socket Is", "Ports"], titles)
check("speaker notes kept apart from body", deck["slides"][0]["notes"] == "Explain it slowly.",
      deck["slides"][0]["notes"])
blob = str(deck)
check("recap is not this session's material", "RECAP-ONLY" not in blob)
check("agenda is not this session's material", "AGENDA-ONLY" not in blob)
for bad, why in ((b"", "empty"), (b"not a zip", "signature")):
    try:
        trdoc_ingest.extract_trdoc(bad, 1, "", "x.docx")
        check(f"rejects {why}", False)
    except ValueError:
        check(f"rejects {why}", True)
try:
    trdoc_ingest.extract_trdoc(data, 1, "", "x.pdf")
    check("rejects a non-.docx name", False)
except ValueError:
    check("rejects a non-.docx name", True)

print("upload → memory")
check("before: sessions 1 and 2 need a doc", generate_list() == {1, 2}, generate_list())
res = sync.ingest_trdoc(COURSE, 1, "s1.docx", data)
check("upload reports its slides", res["n_slides"] == 3, res)
check("session 1 leaves the generate list", generate_list() == {2}, generate_list())
check("held in the deck store", 1 in pptx_ingest.deck_session_numbers(COURSE))
idx = pptx_ingest.taught_digest(COURSE, 2)
check("session 2's run sees session 1 as taught", "What a Socket Is" in idx, idx[:300])
check("row records the file", db.curriculum(COURSE)[0]["tr_doc_file"] == "s1.docx")
check("prune leaves a TR-doc-only session alone",
      1 not in sync.prune_orphan_decks(COURSE) and pptx_ingest.has_deck(COURSE, 1))

print("TR doc wins over the deck")
data3 = make_docx("Session 3 : QoS", [("QoS Parameters", "Delay, jitter, loss.")])
sync.ingest_trdoc(COURSE, 3, "s3.docx", data3)
got = sync.ingest_decks(COURSE, force=True, verbose=False)
d3 = pptx_ingest.get_deck(COURSE, 3)
check("forced fetch does not overwrite a held TR doc",
      d3 and d3.get("source_kind") == "trdoc", d3 and d3.get("source_kind"))

print("detach")
out = sync.detach_trdoc(COURSE, 3)
row3 = next(r for r in db.curriculum(COURSE) if r["session_no"] == 3)
check("deck link goes back to pending", out["deck"] == "link"
      and row3["deck_hash"] == "" and row3["tr_doc_file"] == "", row3)
check("session with a link stays off the generate list", 3 not in generate_list())
sync.detach_trdoc(COURSE, 1)
check("session with nothing returns to the generate list", 1 in generate_list(), generate_list())
check("and is forgotten", not pptx_ingest.has_deck(COURSE, 1)
      and "What a Socket Is" not in pptx_ingest.taught_digest(COURSE, 2))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
