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

print("\n== the assumed-knowledge block covers EVERY prerequisite session ==")
# It used to flatten a prerequisite into one list and keep the first 60 entries. On the
# real 32-session Operating Systems course that sent sessions 1-4 and dropped 702 of 762
# topics in deck order — so the model was told the learner knows number systems, and
# nothing about scheduling, deadlock, paging or virtual memory.
BIG = "Big Course"
for n in range(1, 13):
    deck(BIG, n, f"Unit {n}", [f"Topic {n}.{i}" for i in range(1, 9)])
    db.curriculum_upsert(BIG, n, topic="B", session_name=f"Unit {n}", key_takeaways=["k"])
NEW = "Downstream Course"
db.curriculum_upsert(NEW, 1, topic="D", session_name="One", key_takeaways=["k"])
db.add_prereq(NEW, BIG, added_by=ALICE)
blk = prereqs.block(NEW)
check("every prerequisite session appears",
      all(f"Session {n}" in blk for n in range(1, 13)),
      str([n for n in range(1, 13) if f"Session {n}" not in blk]))
check("…including the LAST one, which flat truncation cut first",
      "Topic 12.8" in blk, blk[-300:])
check("…each named by its deck", "Unit 12" in blk, blk[-300:])
check("nothing is silently dropped at this size", "(+" not in blk, blk)

# The ceiling is per session, so truncation lands evenly rather than amputating the
# back half of the course — and it says so where it happens.
deck(BIG, 13, "Fat Unit", [f"Fat Topic {i}" for i in range(1, 60)])
db.curriculum_upsert(BIG, 13, topic="B", session_name="Fat Unit", key_takeaways=["k"])
tight = prereqs.block(NEW, max_per_session=5)
check("a per-session ceiling still shows every session",
      all(f"Session {n}" in tight for n in range(1, 14)),
      str([n for n in range(1, 14) if f"Session {n}" not in tight]))
check("…and marks what it cut, per session", tight.count("(+") >= 13, str(tight.count("(+")))
check("…saying how many", "(+54 more)" in tight, tight[-400:])
from src import config as _cfg
check("the default ceiling comes from the harness, not a literal",
      prereqs.block(NEW) == prereqs.block(
          NEW, max_per_session=int(_cfg.harness()["context"]
                                   ["prereq_topics_per_session"])))
db.delete_course(NEW); db.delete_course(BIG)

print("\n== an overlap must be EVIDENCE, not a word that happens to appear ==")
# Shipped over-claiming: the test was `topic.lower() in takeaway.lower()`, so a bare
# slide title matched anywhere inside any word. Against a real 32-deck OS course every
# plausible Python takeaway flagged — "Counting" (a counting semaphore) hit "counting
# loops", "File" hit "profile", "Ready" hit "already". A report that is always wrong is
# one nobody reads, so the bar is now: word boundaries, no deck furniture, and a
# single-word topic only when the word is not ordinary English.
NOISE = "Noise Course"
deck(NOISE, 1, "Concurrency", ["Counting", "Semaphores", "Ready", "Overview",
                               "Examples", "Analogy", "Aging", "Virtual Memory"])
db.curriculum_upsert(NOISE, 1, topic="C", session_name="Concurrency", key_takeaways=["k"])
VICTIM = "Victim Course"
db.curriculum_upsert(VICTIM, 1, topic="V", session_name="Basics", key_takeaways=[
    "Learn how counting loops work and how they are already familiar",
    "See an overview of the paging model and its analogy",
    "Understand how virtual memory is managed"])
db.add_prereq(VICTIM, NOISE, added_by=ALICE)
rep = prereqs.coverage_report(VICTIM)
flagged = {o["takeaway"][:20] for o in rep["overlaps"]}

check("bare deck furniture is not indexed as a topic at all",
      not ({"overview", "examples", "analogy"} & {t.lower()
            for t in prereqs.assumed_topics(VICTIM)}),
      str(prereqs.assumed_topics(VICTIM)))
check("…so a takeaway using those words is not flagged",
      "See an overview of" not in flagged, str(sorted(flagged)))
check("an everyday word that is a real topic is indexed",
      "Counting" in prereqs.assumed_topics(VICTIM), str(prereqs.assumed_topics(VICTIM)))
check("…but is not treated as evidence on its own",
      "Learn how counting" not in flagged, str(sorted(flagged)))
check("…and neither is a word inside another word (\"Aging\" in \"paging\")",
      not any("Aging" in (o.get("topics") or []) for o in rep["overlaps"]),
      str(rep["overlaps"]))
check("a distinctive multi-word topic IS still reported",
      any("Virtual Memory" in (o.get("topics") or []) for o in rep["overlaps"]),
      str(rep["overlaps"]))
check("the report says how many topics it actually compared",
      rep["topics_compared"] <= rep["topics_indexed"] and rep["topics_compared"] > 0,
      f"{rep['topics_compared']} of {rep['topics_indexed']}")

# One takeaway naming three prerequisite topics is ONE takeaway. It used to be appended
# once per topic, and the UI reported that number as a count of takeaways.
db.curriculum_upsert(VICTIM, 2, topic="V", session_name="More", key_takeaways=[
    "Compare virtual memory, semaphores and the dispatcher"])
deck(NOISE, 2, "Scheduling", ["Dispatcher"])
rep2 = prereqs.coverage_report(VICTIM)
multi = [o for o in rep2["overlaps"] if o["session_no"] == 2]
check("a takeaway naming several prerequisite topics is reported once",
      len(multi) == 1, str(multi))
check("…carrying all of them", len(multi[0]["topics"]) >= 2 if multi else False,
      str(multi[0]["topics"] if multi else None))
check("…so the count is a count of takeaways",
      len(rep2["overlaps"]) == len({(o["session_no"], o["takeaway"])
                                    for o in rep2["overlaps"]}), str(rep2["overlaps"]))
db.delete_course(VICTIM); db.delete_course(NOISE)

print("\n== the prerequisite decks' BODIES are actually read ==")
# They were extracted, written to disk, and never opened. retrieve() built its corpus
# from decks_before(course, n) and had no way to be pointed at a prerequisite, so the
# only thing a prerequisite ever contributed was a list of slide TITLES. A title says
# WHICH topics the learner met; it cannot say how far they were taken, and that is the
# difference between "has heard of FCFS" and "can trace FCFS to a total head movement".
DEEP = "Deep Course"
pptx_ingest.put_deck(DEEP, 1, {"session_no": 1, "deck_title": "Disk Scheduling",
    "n_slides": 3, "slides": [
        # A section divider: its body is just its own title echoed. Ranks fine on a
        # title-word query and arrives carrying nothing.
        {"n": 1, "title": "Disk Scheduling Algorithms",
         "body": "Disk Scheduling Algorithms\nDisk Scheduling Algorithms"},
        # Real depth — a worked trace with concrete values.
        {"n": 2, "title": "FCFS Worked Trace",
         "body": ("A queue of cylinder requests 98, 183, 37, 122, 14, 124, 65, 67 "
                  "arrives with the head starting at cylinder 53 on a 200-cylinder "
                  "disk. Total head movement is 640 cylinders.")},
        # Boilerplate, however well it matches.
        {"n": 3, "title": "Agenda",
         "body": "Disk scheduling: FCFS, SSTF, SCAN and total head movement compared."},
    ]})
db.curriculum_upsert(DEEP, 1, topic="D", session_name="Disk Scheduling",
                     key_takeaways=["k"])
LATER = "Later Course"
db.curriculum_upsert(LATER, 1, topic="L", session_name="Simulators", key_takeaways=["k"])
db.add_prereq(LATER, DEEP, added_by=ALICE)

hits = prereqs.retrieve(LATER, "disk scheduling total head movement", top_k=5)
check("a prerequisite's slide BODIES are retrieved at all", len(hits) > 0, str(hits))
check("…carrying the actual content, not just the title",
      any("640 cylinders" in h["excerpt"] for h in hits),
      str([h["excerpt"][:60] for h in hits]))
check("…labelled with which prerequisite it came from",
      all(h.get("source") == DEEP for h in hits), str([h.get("source") for h in hits]))
check("a divider slide whose body echoes its title is not offered as depth",
      not any(h["slide"] == 1 for h in hits), str(hits))
check("…nor is boilerplate, however well it matches",
      not any(h["slide"] == 3 for h in hits), str(hits))

blk = prereqs.detail_block(LATER, "disk scheduling total head movement")
check("the block says it is showing the LEVEL, not a prohibition",
      "LEVEL the learner was left at" in blk, blk[:200])
check("…and carries the real numbers", "640 cylinders" in blk, blk)
check("…and tells the writer to start above it", "Write ABOVE this line" in blk, blk)
check("no prerequisites means no block", prereqs.detail_block(DEEP, "anything") == "")

print("\n== an EXTERNAL prerequisite's bodies are read the same way ==")
EXT = "Course Taught Elsewhere"
HOST = "Host Course"
pptx_ingest.put_deck(HOST, 1, {"session_no": 1, "deck_title": "Elsewhere", "n_slides": 1,
    "slides": [{"n": 1, "title": "Closures",
                "body": ("A closure captures the variables of the scope it was defined "
                         "in and keeps them alive after that scope returns.")}]},
    prereq=EXT)
db.add_prereq(HOST, EXT, kind="external", added_by=ALICE)
db.curriculum_upsert(HOST, 1, topic="H", session_name="One", key_takeaways=["k"])
ext_hits = prereqs.retrieve(HOST, "closures capturing scope", top_k=3)
check("an external prerequisite's bodies are retrieved from its substore",
      any("captures the variables" in h["excerpt"] for h in ext_hits), str(ext_hits))
check("…and are attributed to it", any(h.get("source") == EXT for h in ext_hits),
      str([h.get("source") for h in ext_hits]))

print("\n== and it reaches the PROMPT, not just the API ==")
# The whole point. A retrieval nothing injects is the same as no retrieval.
from src import context_builder
from src.course_loader import Session as _S
_cur = _S(number=1, name="Simulators", topic="L", key_takeaways=["build a disk scheduler"],
          module=None) if "module" in _S.__dataclass_fields__ else _S(
          number=1, name="Simulators", topic="L", key_takeaways=["build a disk scheduler"])
ctx = context_builder.past_ppts_context(LATER, _cur)
check("the session-level context carries the prerequisite's real content",
      "640 cylinders" in ctx, ctx[-800:])
chunk = context_builder.prereq_level_block(LATER, _cur, "total head movement")
check("…and so does the per-takeaway instruction", "640 cylinders" in chunk, chunk)
db.delete_course(LATER); db.delete_course(DEEP); db.delete_course(HOST)

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
