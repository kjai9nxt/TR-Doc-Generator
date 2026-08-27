"""PREREQUISITE COURSES: what the learner already knows, before session 1.

    python -m evals.test_prerequisites        # no API key needed, ~3 seconds

WHY THIS EXISTS. "Already taught" meant EARLIER SESSIONS OF THIS COURSE. A React course
whose learners have done a JavaScript course had no way to say so, so the writer had no
basis for deciding whether to define `const` — and it guessed, differently each session.
The page budget is fixed, so every re-taught concept costs a page from something new.

PREREQUISITE IS NOT PRIOR SESSION, and the rule differs in a way that matters:

  · a prior session's topic must NOT be re-taught — the learner met it in this course,
    under this course's numbering, and repeating it is the defect the repetition gate
    exists for;
  · a prerequisite's topic may be REFERENCED freely — it is assumed ground, and the doc
    is expected to build on it by name.

Two blocks, two rules. Conflating them would either forbid a React doc from saying
"useState" because a JS deck mentioned state, or permit re-teaching a session from three
weeks ago.

A prerequisite is FIRST-CLASS a course already in the agent — its decks are here, so
nothing is uploaded twice. Decks from outside are the fallback.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_prereq_test_")
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


from src import db, pptx_ingest, prereqs, context_builder, course_loader   # noqa: E402
from guardrails import guardrails                                          # noqa: E402

REACT, JS, CSS = "React Fundamentals", "JavaScript Essentials", "CSS Layout"
ALICE = "alice@nxtwave.co.in"
db.init()


def deck(course, n, title, topics):
    pptx_ingest.put_deck(course, n, {
        "session_no": n, "deck_title": title, "n_slides": len(topics),
        "slides": [{"n": i + 1, "title": t, "body": f"about {t}"}
                   for i, t in enumerate(topics)]})


# The JS course lives in this agent already — that is the whole point of C below.
for n, t, topics in ((1, "Values and Types", ["Let And Const", "Template Literals"]),
                     (2, "Functions", ["Arrow Functions", "Closures"]),
                     (3, "Async", ["Promises", "Async Await"])):
    db.curriculum_upsert(JS, n, topic="JS", session_name=t, key_takeaways=["k"])
    deck(JS, n, t, topics)
deck(CSS, 1, "Flexbox", ["Flex Container", "Flex Items"])
db.curriculum_upsert(CSS, 1, topic="CSS", session_name="Flexbox", key_takeaways=["k"])
for n, t in ((1, "Components"), (2, "Props"), (3, "State and Effects")):
    db.curriculum_upsert(REACT, n, topic="Hooks", session_name=t,
                         key_takeaways=[f"{t}: what it is"])
deck(REACT, 1, "Components", ["What Is A Component", "JSX Syntax"])

print("\n== a prerequisite is a course the agent already holds ==")
check("adding one", db.add_prereq(REACT, JS, added_by=ALICE))
check("…it is listed", [p["prereq"] for p in db.prereqs(REACT)] == [JS],
      str(db.prereqs(REACT)))
check("…and nothing was uploaded twice — its decks are already here",
      pptx_ingest.deck_session_numbers(JS) == {1, 2, 3},
      str(sorted(pptx_ingest.deck_session_numbers(JS))))
check("adding the same one twice does not duplicate it",
      db.add_prereq(REACT, JS, added_by=ALICE) is False
      and len(db.prereqs(REACT)) == 1, str(db.prereqs(REACT)))
check("a course cannot be its own prerequisite",
      db.add_prereq(REACT, REACT, added_by=ALICE) is False)
db.add_prereq(REACT, CSS, added_by=ALICE)
check("a course can have several", len(db.prereqs(REACT)) == 2, str(db.prereqs(REACT)))

print("\n== assumed knowledge is every prerequisite's topics ==")
known = prereqs.assumed_topics(REACT)
check("the JS topics are assumed", "Closures" in known and "Promises" in known, str(known))
check("…and the CSS ones", "Flex Container" in known, str(known))
check("…and not this course's own", "JSX Syntax" not in known, str(known))
check("a course with no prerequisites assumes nothing",
      prereqs.assumed_topics(JS) == [], str(prereqs.assumed_topics(JS)))

print("\n== the block says REFERENCE freely, do not re-teach ==")
blk = prereqs.block(REACT)
check("it names the prerequisite courses", JS in blk and CSS in blk, blk[:200])
check("…lists the topics", "Closures" in blk, blk[:400])
check("…says the learner already knows them",
      "already" in blk.lower() and "assum" in blk.lower(), blk[:300])
# The distinction that matters: prior-session topics are FORBIDDEN, these are not.
check("…and says they may be referenced, not that they are banned",
      "refer" in blk.lower(), blk[:400])
# The two blocks sit side by side in one prompt and must say opposite things about
# their own topics — that is the whole reason they are separate.
_ctx = context_builder.past_ppts_context(
    REACT, course_loader.get_session(3, course_loader.load_sessions(None, course=REACT)))
check("…which is the opposite of the prior-session rule, still stated",
      "Do NOT re-teach any of it" in _ctx,
      "the prior-session block must still forbid repetition")
check("…and both blocks are in the same prompt",
      "ALREADY TAUGHT IN THIS COURSE" in _ctx and "ASSUMED KNOWLEDGE" in _ctx,
      _ctx[:160])
check("a course with no prerequisites gets no block", prereqs.block(JS) == "",
      prereqs.block(JS)[:80])

print("\n== a prerequisite topic is NOT a repetition failure ==")
# The trap. taught_titles feeds the deterministic repetition gate; if prerequisite topics
# went in there, a React slide could be failed for naming a concept it is supposed to
# build on.
cur = course_loader.get_session(3, course_loader.load_sessions(None, course=REACT))
prior = [t for _n, t in pptx_ingest.taught_titles(REACT, 3)]
check("the repetition lookup holds this course's own topics",
      "JSX Syntax" in prior, str(prior))
check("…and NOT the prerequisites'",
      "Closures" not in prior and "Flex Container" not in prior, str(prior))

print("\n== the generation prompt carries it ==")
base = context_builder.build_guided_base(REACT, None, cur, None)
check("the assumed-knowledge block is in the prompt", "Closures" in base, base[:200])
check("…and the prerequisite course is named", JS in base)
check("a course without prerequisites has no such block",
      "ASSUMED KNOWLEDGE" not in context_builder.build_guided_base(
          JS, None, course_loader.get_session(
              1, course_loader.load_sessions(None, course=JS)), None))

print("\n== gaps and overlaps, as a review signal ==")
# The one visible product of attaching prerequisites: what this course assumes that no
# prerequisite covers, and what it is about to re-teach.
db.curriculum_upsert(REACT, 4, topic="Hooks", session_name="Custom Hooks",
                     key_takeaways=["Closures: why a hook captures its scope",
                                    "Suspense: a thing nothing covers"])
rep = prereqs.coverage_report(REACT)
check("an overlap is reported",
      any("Closures" in o["topic"] for o in rep["overlaps"]), str(rep["overlaps"]))
check("…naming which prerequisite covers it",
      any(o.get("prereq") == JS for o in rep["overlaps"]), str(rep["overlaps"]))
check("the report counts what was indexed",
      rep["topics_indexed"] >= 6, str(rep["topics_indexed"]))
check("…and which courses it came from", sorted(rep["prereqs"]) == sorted([CSS, JS]),
      str(rep["prereqs"]))

print("\n== removing a prerequisite, and deleting a course ==")
check("a prerequisite can be removed", db.remove_prereq(REACT, CSS))
check("…and its topics stop being assumed",
      "Flex Container" not in prereqs.assumed_topics(REACT),
      str(prereqs.assumed_topics(REACT)))
check("…while the other stays", "Closures" in prereqs.assumed_topics(REACT))
db.delete_course(REACT)
check("deleting a course takes its prerequisite links",
      db.prereqs(REACT) == [], str(db.prereqs(REACT)))
check("…and does NOT delete the prerequisite course itself",
      len(db.curriculum(JS)) == 3, str(len(db.curriculum(JS))))
db.add_prereq("Another Course", JS, added_by=ALICE)
db.delete_course(JS)
check("deleting a course that is somebody's prerequisite unlinks it there too",
      db.prereqs("Another Course") == [], str(db.prereqs("Another Course")))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
