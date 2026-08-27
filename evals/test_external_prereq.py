"""A prerequisite taught SOMEWHERE ELSE — decks, but no course in this agent.

    python -m evals.test_external_prereq        # no API key needed, ~3 seconds

WHY THIS EXISTS. A prerequisite was a course the agent already holds, which covers the
case that compounds — add Operating Systems and Computer Networks as prerequisites of
Distributed Systems and their decks are already here. It does not cover the ordinary one:
the learners did a JavaScript course somewhere else and all anybody has is its slides.

So a prerequisite comes in two kinds, and the difference is only WHERE THE DECKS LIVE:

  · internal — a course in this agent. Its decks are its own, under its own folder, and
    attaching it costs nothing.
  · external — a name and a set of deck links. The decks belong to the COURSE THAT
    DECLARED IT, because there is no course of their own to hang them on, so they live
    at decks/<course>/prereq/<name>/ and go when that course goes.

Everything downstream is identical: both kinds feed the same assumed-knowledge block,
the same judge input, and neither goes into the deterministic repetition lookup.

The decks here are written straight into the store rather than fetched, because
downloading real Google Slides is not what this is testing.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_extprereq_test_")
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


from src import db, pptx_ingest, prereqs                            # noqa: E402

REACT, OS = "React Fundamentals", "Operating Systems"
JS = "JavaScript (taught elsewhere)"
ALICE = "alice@nxtwave.co.in"
db.init()


def deck(n, title, topics):
    return {"session_no": n, "deck_title": title, "n_slides": len(topics),
            "slides": [{"n": i + 1, "title": t, "body": f"about {t}"}
                       for i, t in enumerate(topics)]}


for n, t in ((1, "Components"), (2, "State")):
    db.curriculum_upsert(REACT, n, topic="UI", session_name=t, key_takeaways=["k"])
pptx_ingest.put_deck(REACT, 1, deck(1, "Components", ["What Is A Component"]))
db.curriculum_upsert(OS, 1, topic="OS", session_name="Processes", key_takeaways=["k"])
pptx_ingest.put_deck(OS, 1, deck(1, "Processes", ["Process States"]))

print("\n== an external prerequisite is a name plus decks ==")
check("it can be declared", db.add_prereq(REACT, JS, added_by=ALICE, kind="external"))
row = db.prereqs(REACT)[0]
check("…and is marked as external", row["kind"] == "external", str(row))
check("an internal one still works and is marked so",
      db.add_prereq(REACT, OS, added_by=ALICE)
      and next(p["kind"] for p in db.prereqs(REACT) if p["prereq"] == OS) == "course",
      str(db.prereqs(REACT)))
check("an external prerequisite need not be a course in the agent",
      JS not in db.curriculum_session_counts(), str(sorted(db.curriculum_session_counts())))

print("\n== its decks belong to the course that declared it ==")
pptx_ingest.put_deck(REACT, 1, deck(1, "JS Basics", ["Let And Const", "Closures"]),
                     prereq=JS)
pptx_ingest.put_deck(REACT, 2, deck(2, "JS Async", ["Promises"]), prereq=JS)
check("they are stored under that course's prereq folder",
      pptx_ingest.prereq_decks_dir(REACT, JS).is_dir()
      and pptx_ingest.prereq_decks_dir(REACT, JS).parent.parent
      == pptx_ingest.course_decks_dir(REACT),
      str(pptx_ingest.prereq_decks_dir(REACT, JS)))
check("…and read back", pptx_ingest.deck_session_numbers(REACT, prereq=JS) == {1, 2},
      str(sorted(pptx_ingest.deck_session_numbers(REACT, prereq=JS))))
check("the course's OWN decks are untouched by them",
      pptx_ingest.deck_session_numbers(REACT) == {1},
      str(sorted(pptx_ingest.deck_session_numbers(REACT))))
check("…which is the trap: both have a session 1",
      pptx_ingest.get_deck(REACT, 1)["deck_title"] == "Components"
      and pptx_ingest.get_deck(REACT, 1, prereq=JS)["deck_title"] == "JS Basics",
      f"{pptx_ingest.get_deck(REACT, 1)} / {pptx_ingest.get_deck(REACT, 1, prereq=JS)}")

print("\n== both kinds feed the same assumed knowledge ==")
known = prereqs.assumed_topics(REACT)
check("the external one's topics are assumed",
      "Closures" in known and "Promises" in known, str(known))
check("…and the internal one's", "Process States" in known, str(known))
check("…and not this course's own", "What Is A Component" not in known, str(known))
blk = prereqs.block(REACT)
check("the block names both", JS in blk and OS in blk, blk[:240])
check("…and says which is which",
      "taught elsewhere" in blk.lower() or "external" in blk.lower(), blk[:300])

print("\n== an external prerequisite stays OUT of the repetition lookup ==")
# The same trap as the internal kind, and worse here: its decks live inside this course's
# own folder, so a careless reader would treat them as this course's prior sessions and
# fail a slide for building on exactly what it was told to build on.
prior = [t for _n, t in pptx_ingest.taught_titles(REACT, 99)]
check("this course's own topic is in it", "What Is A Component" in prior, str(prior))
check("…and the external prerequisite's is NOT",
      "Closures" not in prior and "Promises" not in prior, str(prior))
check("…nor does it appear in this course's own deck list",
      pptx_ingest.deck_session_numbers(REACT) == {1},
      str(sorted(pptx_ingest.deck_session_numbers(REACT))))

print("\n== removing it takes its decks; removing an internal one does not ==")
check("removing the internal prerequisite", db.remove_prereq(REACT, OS))
check("…leaves that course's decks alone — they are its own",
      pptx_ingest.deck_session_numbers(OS) == {1},
      str(sorted(pptx_ingest.deck_session_numbers(OS))))
check("removing the external one", db.remove_prereq(REACT, JS))
check("…takes its decks with it, because nothing else owns them",
      not pptx_ingest.prereq_decks_dir(REACT, JS).is_dir(),
      str(pptx_ingest.prereq_decks_dir(REACT, JS)))
check("…and it is no longer assumed knowledge",
      "Closures" not in prereqs.assumed_topics(REACT),
      str(prereqs.assumed_topics(REACT)))

print("\n== deleting the course takes every external prerequisite with it ==")
db.add_prereq(REACT, JS, added_by=ALICE, kind="external")
pptx_ingest.put_deck(REACT, 1, deck(1, "JS Basics", ["Closures"]), prereq=JS)
check("re-declared and stored", pptx_ingest.prereq_decks_dir(REACT, JS).is_dir())
db.delete_course(REACT)
# db.delete_course deliberately does not touch disk — it does not know about the
# filesystem, and the endpoint calls drop_course_decks for that. This is the same pair of
# calls DELETE /api/courses makes.
gone = pptx_ingest.drop_course_decks(REACT)
check("the whole deck store for that course is gone",
      not pptx_ingest.course_decks_dir(REACT).exists(),
      f"cleared {gone}; {pptx_ingest.course_decks_dir(REACT)}")
check("…including the external prerequisite's decks inside it",
      not pptx_ingest.prereq_decks_dir(REACT, JS).is_dir())
check("…and the link", db.prereqs(REACT) == [], str(db.prereqs(REACT)))
check("…while the OTHER course is untouched",
      pptx_ingest.deck_session_numbers(OS) == {1},
      str(sorted(pptx_ingest.deck_session_numbers(OS))))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
