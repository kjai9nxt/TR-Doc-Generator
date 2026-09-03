"""COURSE MEMORY — the two gaps it closes, and the two it must not open.

    python -m evals.test_course_memory       # no API key needed, ~2 seconds

WHY THIS EXISTS. Two things were not remembered anywhere:

  1. WHAT AN APPROVED TR TAUGHT, BEFORE ITS DECK EXISTS. `pptx_ingest.taught_index` is
     built from extracted decks and nothing else, and `sync.prune_orphan_decks` states
     the rule: a curriculum row with no link has no deck. But a TR is written, reviewed
     and approved WEEKS before its session is recorded and linked. In that window the
     session is absent from the digest the writer reads, absent from the digest the
     judge reads, and absent from `taught_titles` — which is what the deterministic
     repetition guardrail compares a new slide's title against. So a batch of documents
     written ahead of recording can re-teach itself with every gate green.

  2. THE EXAMPLES ALREADY SPENT. guardrails.check catches one example reused across
     slides of ONE document; nothing looked across documents, so the same worked
     example could be built in session 4 and built again in session 11.

And the two things that must NOT happen as a result:

  · A DECK MUST ALWAYS WIN. The placeholder describes a promise; the deck describes a
    recording. Once a deck exists the placeholder must vanish, or the taught index holds
    two opinions of one session.
  · MEMORY MUST NOT CROSS COURSES. Same failure the deck store had, and worse on the
    write side: `learning.record_feedback` documents why — a value read from the wrong
    course is one bad document, a value written to the wrong course is permanent.

The database and knowledge base are throwaways under TR_DATA_DIR.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_course_memory_")
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


from src import course_memory, db, pptx_ingest, sync   # noqa: E402

OS_C = "Operating Systems"
REACT = "React Fundamentals"

db.init()


def doc(session_no, title, slide_titles, examples=()):
    """A rendered TR doc in the shape assemble_doc produces.

    `examples` are (heading, prose) pairs emitted as `working_example` slides, which is
    the role the harness defines for a slide the learner must be able to EXECUTE.
    """
    slides, n = [], 0
    for t in slide_titles:
        n += 1
        slides.append({"n": n, "title": t, "role": "concept_intro", "heading": t,
                       "subheading": "", "content": [{"type": "text", "text": f"About {t}."}],
                       "speaker_notes": "note"})
    for heading, prose in examples:
        n += 1
        slides.append({"n": n, "title": heading, "role": "working_example",
                       "heading": heading, "subheading": "",
                       "content": [{"type": "text", "text": prose}],
                       "speaker_notes": "note"})
    return {"session_no": session_no, "session_title": title,
            "sections": [{"index": 1, "name": "S", "slides": slides}]}


def deck(session_no, title, slide_titles):
    slides = [{"n": i + 1, "title": t, "body": f"body of {t}", "notes": "", "tables": []}
              for i, t in enumerate(slide_titles)]
    return {"session_no": session_no, "source_file": f"{title}.pptx",
            "deck_title": title, "n_slides": len(slides),
            "summary": title, "slides": slides}


print("\n== a course with approved TRs and NO decks at all ==")
# The starting state of every new course: documents are written before anything is
# recorded. Before this feature the taught index was empty here, so session 3 was
# generated as if sessions 1 and 2 had never happened.
for n, name in ((1, "Processes & Threads"), (2, "CPU Scheduling"), (3, "Deadlocks")):
    db.curriculum_upsert(OS_C, n, topic="T", session_name=name, key_takeaways=["k"])

check("with nothing recorded, the taught index is empty",
      pptx_ingest.taught_index(OS_C, 3) == [])

course_memory.record(OS_C, 1, doc(1, "Processes & Threads",
                                  ["Process Control Block", "Context Switching",
                                   "Agenda", "Quiz Time!"]), run_id="r1")
course_memory.record(OS_C, 2, doc(2, "CPU Scheduling",
                                  ["Round Robin", "Shortest Job First"]), run_id="r2")

idx = pptx_ingest.taught_index(OS_C, 3)
check("an approved TR now stands in for its session",
      [e["session_no"] for e in idx] == [1, 2], str([e["session_no"] for e in idx]))
check("…and is marked as a promise, not a recording",
      all(e.get("provisional") for e in idx)
      and "not yet recorded" in idx[0]["deck_title"], idx[0]["deck_title"])
topics = [t for _n, t in pptx_ingest.taught_titles(OS_C, 3)]
check("…so the REPETITION GUARDRAIL can see those topics",
      "Context Switching" in topics and "Round Robin" in topics, str(topics))
check("…and deck furniture is stripped exactly as it is for a deck",
      "Agenda" not in topics and "Quiz Time!" not in topics, str(topics))
check("…and the digest the writer and judge read names the session",
      "Round Robin" in pptx_ingest.taught_digest(OS_C, 3),
      pptx_ingest.taught_digest(OS_C, 3)[:120])

print("\n== a session never sees its OWN memory ==")
# The trap a naive implementation falls into: regenerating session 2 must not be told
# that session 2 already taught Round Robin.
own = [t for _n, t in pptx_ingest.taught_titles(OS_C, 2)]
check("regenerating a session is not warned about itself",
      "Round Robin" not in own and "Context Switching" in own, str(own))

print("\n== the deck always wins ==")
# The link goes on the row as well as the deck in the store, because that is what a real
# recording looks like — and because prune_orphan_decks treats a deck whose row has NO
# link as rubbish and deletes it, which is correct and would make this a test of nothing.
db.curriculum_upsert(OS_C, 1, ppt_link="https://docs.google.com/presentation/d/S1/edit")
pptx_ingest.put_deck(OS_C, 1, deck(1, "Processes & Threads",
                                   ["Process Control Block", "Thread Models"]))
idx = pptx_ingest.taught_index(OS_C, 3)
s1 = [e for e in idx if e["session_no"] == 1]
check("one session yields ONE entry, not two",
      len(s1) == 1, f"{len(s1)} entries for session 1")
check("…and it is the deck's, not the document's",
      not s1[0].get("provisional") and "Thread Models" in s1[0]["topics"],
      str(s1[0]))
check("…even before anything prunes the stale row",
      1 in {e["session_no"] for e in db.provisional_taught(OS_C)},
      "expected the row to still exist, proving the read-time filter is what won")

sync.prune_orphan_decks(OS_C)
check("…and a sync then clears it",
      1 not in {e["session_no"] for e in db.provisional_taught(OS_C)},
      str([e["session_no"] for e in db.provisional_taught(OS_C)]))

print("\n== memory follows the curriculum ==")
db.curriculum_delete(OS_C, 2)
sync.prune_orphan_decks(OS_C)
check("a session removed from the curriculum takes its memory with it",
      2 not in {e["session_no"] for e in db.provisional_taught(OS_C)},
      str([e["session_no"] for e in db.provisional_taught(OS_C)]))
check("…and its examples too",
      2 not in {e["session_no"] for e in db.examples_used(OS_C)},
      str(db.examples_used(OS_C)))

print("\n== one course cannot read another's memory ==")
db.curriculum_upsert(REACT, 1, topic="T", session_name="JSX", key_takeaways=["k"])
db.curriculum_upsert(REACT, 2, topic="T", session_name="Hooks", key_takeaways=["k"])
course_memory.record(REACT, 1, doc(1, "JSX", ["Virtual DOM", "Reconciliation"]), run_id="r3")
react_topics = [t for _n, t in pptx_ingest.taught_titles(REACT, 9)]
os_topics = [t for _n, t in pptx_ingest.taught_titles(OS_C, 9)]
check("React sees only React", "Virtual DOM" in react_topics
      and "Context Switching" not in react_topics, str(react_topics))
check("…and Operating Systems only its own",
      "Virtual DOM" not in os_topics, str(os_topics))

print("\n== the examples ledger ==")
db.curriculum_upsert(OS_C, 4, topic="T", session_name="Paging", key_takeaways=["k"])
db.curriculum_upsert(OS_C, 5, topic="T", session_name="Segmentation", key_takeaways=["k"])
course_memory.record(OS_C, 4, doc(
    4, "Paging", ["Page Tables"],
    examples=[("Translating a logical address",
               "Translate 0x2F1A with a 4096-byte page and frame 0x07.")]), run_id="r4")

ex = db.examples_used(OS_C, 5)
check("a worked example is remembered", len(ex) == 1, str(ex))
check("…with the concept it taught",
      ex and ex[0]["concept"] == "Translating a logical address", str(ex))
check("…and the FIGURES that make it that example, not another one",
      ex and "0x2F1A" in ex[0]["figures"], str(ex[0]["figures"] if ex else None))
check("only working_example slides are counted",
      all(e["concept"] != "Page Tables" for e in ex), str(ex))

blk = course_memory.examples_block(OS_C, 5)
check("the writer is told which examples are spent",
      "0x2F1A" in blk and "Session 4" in blk, blk[:160])
check("…and told that the CONCEPT may still be revisited",
      "same CONCEPT again at greater depth" in blk, blk[:200])
check("a session is not shown its own examples",
      "0x2F1A" not in course_memory.examples_block(OS_C, 4),
      course_memory.examples_block(OS_C, 4)[:120])
check("…and a course is not shown another course's",
      "0x2F1A" not in course_memory.examples_block(REACT, 9),
      course_memory.examples_block(REACT, 9)[:120])

print("\n== re-approving a session REPLACES what it claims, never appends ==")
course_memory.record(OS_C, 4, doc(
    4, "Paging", ["Page Tables", "TLB"],
    examples=[("Translating a logical address",
               "Translate 0x5B20 with a 8192-byte page and frame 0x11.")]), run_id="r5")
ex2 = db.examples_used(OS_C, 5)
check("the discarded draft's example is forgotten",
      len(ex2) == 1 and "0x5B20" in ex2[0]["figures"], str(ex2))
s4 = [e for e in pptx_ingest.taught_index(OS_C, 5) if e["session_no"] == 4]
check("…and the topics are the new document's",
      len(s4) == 1 and "TLB" in s4[0]["topics"], str(s4))

print("\n== the repetition gate names WHERE it knows a session from ==")
# The gate is identical for a deck and for an approved TR, and must be: sessions are
# delivered in order, so session 12 is recorded before session 13 is ever delivered —
# by the time a learner reaches 13 they have been taught 12 either way. What differs is
# only the MESSAGE. Told just "Session 12 already introduced this", a reviewer goes
# hunting for session 12's deck, finds none, and cannot tell whether the gate is right
# or broken.
check("a session known from a deck is not labelled provisional",
      1 not in pptx_ingest.provisional_sessions(OS_C, 9),
      str(pptx_ingest.provisional_sessions(OS_C, 9)))
check("…and one known only from an approved TR is",
      4 in pptx_ingest.provisional_sessions(OS_C, 9),
      str(pptx_ingest.provisional_sessions(OS_C, 9)))
check("a session with no memory at all is neither",
      pptx_ingest.provisional_sessions(REACT, 2) == {1},
      str(pptx_ingest.provisional_sessions(REACT, 2)))

print("\n== nothing here can break a generation ==")
# Every read runs inside a live generation, so an unreachable store must degrade to
# "no memory" rather than raise. Asserted against a course that does not exist.
check("an unknown course reads as empty, not as an error",
      pptx_ingest.taught_index("No Such Course", 5) == []
      and course_memory.examples_block("No Such Course", 5) == "")
check("a doc with no sections records nothing and does not raise",
      course_memory.record(OS_C, 9, {"session_title": "x"}) == {"topics": 0, "examples": 0})

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
