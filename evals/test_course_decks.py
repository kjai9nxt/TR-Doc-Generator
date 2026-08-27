"""The extracted-deck store, and whether it can hold more than one course.

    python -m evals.test_course_decks        # no API key needed, ~2 seconds

WHY THIS EXISTS. Decks were filed by SESSION NUMBER alone —
knowledge_base/decks/session_07.json — with no course anywhere in the path. One
directory, one manifest, globbed globally. On a single-course instance that is
invisible. With two courses it means:

  · two courses that both have a session 7 SHARE ONE FILE, so ingesting the second
    silently overwrites the first, and whichever was fetched last is what both courses
    read as "what I already taught";
  · `taught_digest` — the "already taught, do not teach again" block in every
    generation prompt — is built from whatever decks are on disk, so a React doc is
    told it has already taught Deadlock Detection;
  · `taught_titles` feeds the DETERMINISTIC repetition guardrail, so a legitimate
    React slide can be failed for repeating an Operating Systems title;
  · deleting or renumbering a session in one course moves another course's deck.

This suite asserts the course-scoped behaviour. Every check here fails against the
global store, which is the point: the fixtures below are two courses that both have a
session 7, and nothing about them is unusual.

The database and knowledge base are throwaways under TR_DATA_DIR.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_course_decks_")
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


from src import db, pptx_ingest, sync            # noqa: E402

REACT = "React Fundamentals"
OS = "Operating Systems"

db.init()


def deck(session_no: int, title: str, slide_titles: list[str]) -> dict:
    """A deck record in the shape extract_deck() produces."""
    slides = [{"n": i + 1, "title": t, "body": f"body of {t}", "notes": "", "tables": []}
              for i, t in enumerate(slide_titles)]
    return {"session_no": session_no, "source_file": f"{title}.pptx",
            "deck_title": title, "n_slides": len(slides),
            "summary": title + "\n" + "\n".join(
                f"    - Slide {s['n']}: {s['title']}" for s in slides),
            "slides": slides}


def put(course: str, session_no: int, title: str, slide_titles: list[str]) -> None:
    """Write a deck the way an ingest would — through the store's own API, so the test
    exercises the layout rather than hard-coding a path."""
    pptx_ingest.put_deck(course, session_no, deck(session_no, title, slide_titles))


print("\n== two courses, both with a session 7 ==")
# Nothing contrived: every course has low session numbers, so any two courses on one
# instance collide immediately.
for c, n, t in ((OS, 6, "Process Scheduling"), (OS, 7, "Deadlock Detection"),
                (REACT, 6, "JSX and Rendering"), (REACT, 7, "Hooks Basics")):
    db.curriculum_upsert(c, n, topic="T", session_name=t, key_takeaways=["k"])
put(OS, 6, "Process Scheduling", ["Scheduler Queues", "Round Robin"])
put(OS, 7, "Deadlock Detection", ["Wait-For Graph", "Recovery"])
put(REACT, 6, "JSX and Rendering", ["JSX Syntax", "Virtual DOM"])
put(REACT, 7, "Hooks Basics", ["useState", "useEffect"])

check("each course keeps its own session 7",
      pptx_ingest.get_deck(OS, 7)["deck_title"] == "Deadlock Detection"
      and pptx_ingest.get_deck(REACT, 7)["deck_title"] == "Hooks Basics",
      f"{(pptx_ingest.get_deck(OS, 7) or {}).get('deck_title')!r} / "
      f"{(pptx_ingest.get_deck(REACT, 7) or {}).get('deck_title')!r}")
check("…and ingesting one does not overwrite the other",
      pptx_ingest.get_deck(OS, 6)["deck_title"] == "Process Scheduling",
      str((pptx_ingest.get_deck(OS, 6) or {}).get("deck_title")))

print("\n== which sessions have a deck is answered per course ==")
check("the OS course reports its own",
      pptx_ingest.deck_session_numbers(OS) == {6, 7},
      str(pptx_ingest.deck_session_numbers(OS)))
check("…and React its own", pptx_ingest.deck_session_numbers(REACT) == {6, 7},
      str(pptx_ingest.deck_session_numbers(REACT)))
db.curriculum_upsert(REACT, 9, topic="T", session_name="Context", key_takeaways=["k"])
put(REACT, 9, "Context API", ["Provider", "Consumer"])
check("a session only one course has does not appear in the other",
      9 in pptx_ingest.deck_session_numbers(REACT)
      and 9 not in pptx_ingest.deck_session_numbers(OS),
      f"react={sorted(pptx_ingest.deck_session_numbers(REACT))} "
      f"os={sorted(pptx_ingest.deck_session_numbers(OS))}")

print("\n== 'what have I already taught' is this course's decks, nobody else's ==")
# The block that goes into EVERY generation prompt. Built globally, a React session 8
# was told it had already taught Deadlock Detection.
os_taught = [t for _, t in pptx_ingest.taught_titles(OS, 8)]
react_taught = [t for _, t in pptx_ingest.taught_titles(REACT, 8)]
check("the OS course has taught scheduling and deadlocks",
      "Round Robin" in os_taught and "Wait-For Graph" in os_taught, str(os_taught))
check("…and none of React's topics", not any(
      x in os_taught for x in ("useState", "useEffect", "Virtual DOM")), str(os_taught))
check("React has taught hooks and JSX",
      "useState" in react_taught and "JSX Syntax" in react_taught, str(react_taught))
check("…and none of the OS course's", not any(
      x in react_taught for x in ("Round Robin", "Wait-For Graph")), str(react_taught))
check("the digest is per course too",
      "Deadlock Detection" in pptx_ingest.taught_digest(OS, 8)
      and "Deadlock Detection" not in pptx_ingest.taught_digest(REACT, 8),
      pptx_ingest.taught_digest(REACT, 8)[:120])
check("…and it stops at the session being written",
      "Provider" not in [t for _, t in pptx_ingest.taught_titles(REACT, 8)],
      str([t for _, t in pptx_ingest.taught_titles(REACT, 8)]))

print("\n== deleting a session's deck touches only that course ==")
check("dropping React's session 7 reports it existed",
      pptx_ingest.drop_deck(REACT, 7) is True)
check("…React no longer has it", 7 not in pptx_ingest.deck_session_numbers(REACT),
      str(sorted(pptx_ingest.deck_session_numbers(REACT))))
check("…and the OS course still does",
      pptx_ingest.get_deck(OS, 7)["deck_title"] == "Deadlock Detection",
      str((pptx_ingest.get_deck(OS, 7) or {}).get("deck_title")))

print("\n== renumbering follows one course's sessions only ==")
put(REACT, 7, "Hooks Basics", ["useState", "useEffect"])
moved = pptx_ingest.renumber_decks(REACT, {7: 8})
check("React's session 7 becomes 8", moved and 8 in pptx_ingest.deck_session_numbers(REACT),
      f"moved={moved} react={sorted(pptx_ingest.deck_session_numbers(REACT))}")
check("…the file says so inside too",
      pptx_ingest.get_deck(REACT, 8)["session_no"] == 8,
      str(pptx_ingest.get_deck(REACT, 8).get("session_no")))
check("…and the OS course's session 7 has not moved",
      pptx_ingest.get_deck(OS, 7)["deck_title"] == "Deadlock Detection"
      and 7 in pptx_ingest.deck_session_numbers(OS),
      str(sorted(pptx_ingest.deck_session_numbers(OS))))

print("\n== pruning orphans is scoped to the course that was edited ==")
# prune_orphan_decks drops decks for sessions whose row no longer carries a link. Global,
# it pruned by another course's curriculum.
db.curriculum_upsert(OS, 6, topic="T", session_name="Process Scheduling",
                     key_takeaways=["k"], ppt_link="https://x/1")
db.curriculum_upsert(OS, 7, topic="T", session_name="Deadlock Detection",
                     key_takeaways=["k"], ppt_link="")
sync.prune_orphan_decks(OS)
check("the OS session whose link was cleared loses its deck",
      7 not in pptx_ingest.deck_session_numbers(OS),
      str(sorted(pptx_ingest.deck_session_numbers(OS))))
check("…the one that still has a link keeps it",
      6 in pptx_ingest.deck_session_numbers(OS),
      str(sorted(pptx_ingest.deck_session_numbers(OS))))
check("…and React's decks are untouched",
      pptx_ingest.deck_session_numbers(REACT) == {6, 8, 9},
      str(sorted(pptx_ingest.deck_session_numbers(REACT))))

print("\n== a course name that is not a safe path still gets its own folder ==")
ODD = "C++ / Advanced: Templates & Traits"
db.curriculum_upsert(ODD, 1, topic="T", session_name="Intro", key_takeaways=["k"])
put(ODD, 1, "Templates", ["Type Deduction"])
check("it stores and reads back",
      (pptx_ingest.get_deck(ODD, 1) or {}).get("deck_title") == "Templates",
      str(pptx_ingest.get_deck(ODD, 1)))
check("…without colliding with another course's session 1",
      1 not in pptx_ingest.deck_session_numbers(OS),
      str(sorted(pptx_ingest.deck_session_numbers(OS))))
check("…and its folder is inside the deck store",
      pptx_ingest.course_decks_dir(ODD).parent == pptx_ingest.DECKS_DIR,
      str(pptx_ingest.course_decks_dir(ODD)))

print("\n== the one-time migration out of the flat layout ==")
# Every instance that ran an earlier version has decks at decks/session_NN.json with no
# course recorded anywhere. Ownership has to be INFERRED, and the inference has to refuse
# to guess: attributing one course's material to another is worse than leaving it aside,
# because the writer would then be told it had already taught something it had not.
LEG_A, LEG_B = "Legacy Course A", "Legacy Course B"
for n in (1, 2):
    db.curriculum_upsert(LEG_A, n, topic="T", session_name=f"a{n}",
                         key_takeaways=["k"], ppt_link=f"https://x/a{n}")
db.curriculum_upsert(LEG_B, 3, topic="T", session_name="b3", key_takeaways=["k"],
                     ppt_link="https://x/b3")
# Session 5 is in BOTH curricula and linked in both — genuinely unattributable.
for c in (LEG_A, LEG_B):
    db.curriculum_upsert(c, 5, topic="T", session_name="shared", key_takeaways=["k"],
                         ppt_link="https://x/shared")
# Session 7 is in both, but only ONE of them has a link — the deck exists because a link
# was extracted, so that is the strong signal and it must be used.
db.curriculum_upsert(LEG_A, 7, topic="T", session_name="a7", key_takeaways=["k"],
                     ppt_link="https://x/a7")
db.curriculum_upsert(LEG_B, 7, topic="T", session_name="b7", key_takeaways=["k"])

pptx_ingest.DECKS_DIR.mkdir(parents=True, exist_ok=True)
for n in (1, 2, 3, 5, 7):
    (pptx_ingest.DECKS_DIR / f"session_{n:02d}.json").write_text(
        json.dumps(deck(n, f"legacy {n}", [f"topic {n}"])), encoding="utf-8")
# …and the old global manifest, whose hashes are what stop a deck being re-downloaded.
pptx_ingest.MANIFEST.write_text(json.dumps(
    {f"session_{n:02d}": {"hash": f"h{n}", "source_file": f"s{n}.pptx",
                          "session_no": n, "n_slides": 1} for n in (1, 2, 3, 5, 7)}),
    encoding="utf-8")

check("the flat decks are found", sorted(pptx_ingest.legacy_decks()) == [1, 2, 3, 5, 7],
      str(sorted(pptx_ingest.legacy_decks())))
res = pptx_ingest.migrate_legacy_decks()
check("a session only one course links goes to that course",
      res["moved"].get(1) == LEG_A and res["moved"].get(2) == LEG_A
      and res["moved"].get(3) == LEG_B, str(res["moved"]))
check("…the linked course wins over one that merely lists the session",
      res["moved"].get(7) == LEG_A, str(res["moved"].get(7)))
check("a session two courses both link is NOT guessed at",
      res["unassigned"] == [5], str(res["unassigned"]))
check("…and it is parked where nothing reads it",
      (pptx_ingest.DECKS_DIR / pptx_ingest.UNASSIGNED / "session_05.json").exists())
check("nothing is left in the flat layout", pptx_ingest.legacy_decks() == {},
      str(sorted(pptx_ingest.legacy_decks())))
check("the moved decks read back from their course",
      pptx_ingest.get_deck(LEG_A, 1)["deck_title"] == "legacy 1"
      and pptx_ingest.get_deck(LEG_B, 3)["deck_title"] == "legacy 3",
      str(pptx_ingest.get_deck(LEG_A, 1)))
check("…and only from it", pptx_ingest.get_deck(LEG_B, 1) is None,
      str(pptx_ingest.get_deck(LEG_B, 1)))
# Without the manifest a migrated deck looks unextracted and is downloaded again — the
# expensive half of a sync, for nothing.
check("the manifest hashes travel with the decks",
      pptx_ingest._load_manifest(LEG_A).get("session_01", {}).get("hash") == "h1",
      str(pptx_ingest._load_manifest(LEG_A)))
check("…split per course", "session_03" not in pptx_ingest._load_manifest(LEG_A)
      and pptx_ingest._load_manifest(LEG_B).get("session_03", {}).get("hash") == "h3",
      str(pptx_ingest._load_manifest(LEG_B)))
check("the mirror paths reported are the NEW ones",
      pptx_ingest.kb_rel(LEG_A, 1) in res["kb_paths"], str(res["kb_paths"]))
check("the legacy global manifest is retired, not deleted",
      not pptx_ingest.MANIFEST.exists()
      and (pptx_ingest.KB_DIR / "manifest.legacy.json").exists())
check("running it again does nothing", pptx_ingest.migrate_legacy_decks()
      == {"moved": {}, "unassigned": [], "kb_paths": []},
      str(pptx_ingest.migrate_legacy_decks()))
check("…and the decks are still there afterwards",
      pptx_ingest.deck_session_numbers(LEG_A) == {1, 2, 7},
      str(sorted(pptx_ingest.deck_session_numbers(LEG_A))))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
