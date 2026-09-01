"""THE SKILL SYSTEM: what a skill is, where it applies, and that it never becomes content.

    python -m evals.test_skill_system        # no API key needed, ~5 seconds

WHY THIS EXISTS. Skills started as a flat list of course-wide style notes and grew into
the answer to a different question: a TR generator that serves many courses needs one
place to say HOW each of them teaches, without ever touching WHAT they teach. Four things
had to be true for that, and none of them was:

  · A SKILL IS THE UNIT, NOT THE INSTRUCTION. An author who writes four related lines
    under "Teaching Guidelines" has written one skill with four instructions. Splitting
    them into four skills — which the drafter used to do — loses the grouping, loses the
    ORDER (which for a teaching flow IS the instruction), and turns one approval into
    four.
  · A SKILL HAS A SCOPE. The whole course, one session of it, or every course on the
    instance. A session's brief that reaches every session is not a session brief.
  · PRECEDENCE IS DECIDED, NOT IMPLIED. Hard rules, then this course's reviewer
    corrections, then the session, then the course, then the house.
  · A SKILL IS NEVER CONTENT. Told "problem → concept → mechanism → example", a model
    writes a slide whose bullets are those four words. That is the instruction printed as
    curriculum, it is the commonest way a brief leaks, and a reviewer cannot tell it from
    curriculum they have forgotten writing.

The database is a throwaway under TR_DATA_DIR.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skill_system_test_")
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


from src import db, skills                                        # noqa: E402

REACT = "React Fundamentals"
DBMS = "Database Systems"
ALICE = "alice@nxtwave.co.in"

db.init()


def approve_all(course):
    for s in db.skills(course, include_retired=True, include_global=True):
        if s["status"] == "draft":
            db.approve_skill(s["id"], ALICE)


# --------------------------------------------------------------------------- #
print("\n== ONE skill, several instructions — the author's grouping survives ==")
# The exact case from the spec: four related lines under one heading.
GUIDELINES = [
    "Explain intuition before formal definitions.",
    "Use simple language before introducing technical terminology.",
    "Connect new concepts to the previous session.",
    "Use a worked example for difficult concepts.",
]
gid = db.add_skill(REACT, "How this course explains its content.",
                   category="teaching_guidelines", instructions=GUIDELINES,
                   source="user", created_by=ALICE)
check("it is stored as ONE skill", isinstance(gid, int) and gid > 0, str(gid))
check("…not as four", len(db.skills(REACT)) == 1, str(len(db.skills(REACT))))
row = db.skills(REACT)[0]
check("…keeping every instruction", row["instructions"] == GUIDELINES,
      str(row["instructions"]))
check("…in the order they were written", row["instructions"][0].startswith("Explain"),
      str(row["instructions"][:1]))
check("…and its category", row["category"] == "teaching_guidelines",
      str(row["category"]))
db.approve_skill(gid, ALICE)
brief = skills.block(REACT)
check("the brief carries all four", all(g in brief for g in GUIDELINES), brief)
check("…under ONE heading, numbered so the order is visible",
      "1. Explain intuition" in brief and "4. Use a worked example" in brief, brief)
check("…named for what it governs, not a bucket",
      "TEACHING GUIDELINES" in brief, brief[:400])

# --------------------------------------------------------------------------- #
print("\n== a skill says HOW to teach, and the brief says so in as many words ==")
low = brief.lower()
check("the brief states it is not curriculum",
      "how to teach" in low and "what to teach" in low, brief[:900])
check("…and that it must never be printed",
      "never print it" in low, brief[:1200])
check("…naming the exact leak it is trying to stop",
      "mechanism" in low and "agenda" in low, brief[:1400])

# --------------------------------------------------------------------------- #
print("\n== SESSION SKILLS apply to their session and nowhere else ==")
sid = db.add_skill(REACT, "The sequence session 12 is taught in.",
                   category="teaching_flow", scope="session", session_ref=12,
                   instructions=["Open on the render loop before naming any hook."],
                   source="user", created_by=ALICE)
db.approve_skill(sid, ALICE)
check("it is stored against its session",
      db.skills(REACT)[1]["scope"] == "session"
      and db.skills(REACT)[1]["session_ref"] == "12", str(db.skills(REACT)[1]))
in_12 = [s["id"] for s in skills.applicable(REACT, 12)]
in_13 = [s["id"] for s in skills.applicable(REACT, 13)]
none_ = [s["id"] for s in skills.applicable(REACT)]
check("it governs session 12", sid in in_12, str(in_12))
check("…and NOT session 13", sid not in in_13, str(in_13))
check("…and not a run that named no session", sid not in none_, str(none_))
check("the course's own skills reach every session",
      gid in in_12 and gid in in_13 and gid in none_, str((in_12, in_13, none_)))
check("session 12's brief names the session it is for",
      "SESSION 12 ONLY" in skills.block(REACT, 12), skills.block(REACT, 12)[:600])
check("…and session 13's brief does not carry it at all",
      "render loop" not in skills.block(REACT, 13), skills.block(REACT, 13))
check("a session skill with no session is refused, not silently widened",
      db.add_skill(REACT, "Nowhere in particular.", scope="session") is None)

# --------------------------------------------------------------------------- #
print("\n== GLOBAL SKILLS reach every course, and are the weakest tier ==")
glo = db.add_skill(db.GLOBAL_COURSE, "House rule: define a term the first time it is used.",
                   category="teaching_guidelines", scope="global",
                   source="user", created_by=ALICE)
db.approve_skill(glo, ALICE)
check("a course that wrote nothing still gets it",
      glo in [s["id"] for s in skills.applicable(DBMS)],
      str(skills.applicable(DBMS)))
check("…and so does one that wrote plenty",
      glo in [s["id"] for s in skills.applicable(REACT, 12)])
check("it is labelled as the house rule it is",
      "GLOBAL SKILLS" in skills.block(DBMS), skills.block(DBMS))

# --------------------------------------------------------------------------- #
print("\n== PRECEDENCE: reviewer, then session, then course, then global ==")
rev = db.add_skill(REACT, "Corrections review keeps making on this course.",
                   category="reviewer",
                   instructions=["Never call a function component a class component."],
                   source="user", created_by=ALICE)
db.approve_skill(rev, ALICE)
b = skills.block(REACT, 12)
order = [b.index(h) for h in ("## COURSE REVIEWER SKILLS", "## SESSION SKILLS",
                              "## COURSE SKILLS", "## GLOBAL SKILLS")]
check("every tier is present and headed", all(i >= 0 for i in order), str(order))
check("…strongest first", order == sorted(order), str(order))
check("the order is also STATED, so it survives a model reading it out of order",
      "HARD RULES → COURSE REVIEWER SKILLS → SESSION SKILLS → COURSE SKILLS → "
      "GLOBAL SKILLS" in b, b[:1600])
check("applicable() returns them in that order too",
      [s["id"] for s in skills.applicable(REACT, 12)] == [rev, sid, gid, glo],
      str([s["id"] for s in skills.applicable(REACT, 12)]))
check("a reviewer correction stays in ITS course",
      rev not in [s["id"] for s in skills.applicable(DBMS)],
      str(skills.applicable(DBMS)))

# --------------------------------------------------------------------------- #
print("\n== a skill NEVER becomes content — the leak check ==")
FLOW = db.add_skill(
    REACT, "The sequence this course teaches in.", category="teaching_flow",
    instructions=["Start with the problem → build intuition → explain the concept → "
                  "explain the mechanism → give an example → compare approaches."],
    source="user", created_by=ALICE)
db.approve_skill(FLOW, ALICE)
live = skills.applicable(REACT, 12)


def doc(*bullets, takeaways=None, notes=""):
    return {"key_takeaways": takeaways or ["1. Rendering: what triggers a re-render"],
            "agenda": takeaways or ["1. Rendering: what triggers a re-render"],
            "sections": [{"index": 1, "name": "Rendering", "slides": [
                {"n": 1, "title": "Why a component re-renders",
                 "speaker_notes": notes,
                 "content": [{"type": "bullets", "items": list(bullets)}]}]}]}


clean = doc("State changes queue a re-render",
            "Props arriving from a parent do the same",
            "The virtual DOM is diffed before anything is painted")
check("a document that merely FOLLOWS the brief is clean",
      skills.leaks(clean, live) == [], str(skills.leaks(clean, live)))

# The exact failure named in the spec: the teaching flow printed as a bullet list.
leaked_flow = doc("Problem", "Concept", "Mechanism", "Example")
found = skills.leaks(leaked_flow, live)
check("printing the flow's steps as a list is caught", bool(found), str(found))
check("…blamed on the skill it came from",
      any(f["skill_id"] == FLOW for f in found), str(found))
check("…and said to be a flow being printed, not just a similar line",
      any("teaching flow" in f["why"] for f in found), str(found))

# The other shape: an instruction restated as a bullet.
leaked_line = doc("Explain intuition before formal definitions",
                  "State changes queue a re-render",
                  "The virtual DOM is diffed first")
found2 = skills.leaks(leaked_line, live)
check("restating an instruction as a bullet is caught", bool(found2), str(found2))
check("…and pointed at the right place in the document",
      any("slide 1" in f["where"] for f in found2), str(found2))
check("a leak in the SPEAKER NOTES counts too — they are read aloud",
      bool(skills.leaks(doc("State changes queue a re-render",
                            notes="Use simple language before introducing technical "
                                  "terminology."), live)))
check("a leak into the key takeaways counts — that is curriculum",
      bool(skills.leaks(doc("State changes queue a re-render",
                            takeaways=["1. Explain intuition before formal definitions"]),
                        live)))
check("the failure message says what to do about it",
      any("what the LEARNER needs to know" in m
          for m in skills.leak_failures(leaked_line, live)),
      str(skills.leak_failures(leaked_line, live)))
check("a course with no skills cannot leak them", skills.leaks(leaked_flow, []) == [])

# FALSE POSITIVES ARE THE REAL RISK. A gate that fires on ordinary content is worse than
# no gate: it discards correct documents and teaches everyone to ignore it.
ordinary = doc("A problem arises when two components hold the same state",
               "The concept of lifting state up solves it",
               "The mechanism is a shared parent")
check("bullets that merely USE the flow's words are not a leak",
      skills.leaks(ordinary, live) == [], str(skills.leaks(ordinary, live)))
check("…nor is a real curriculum bullet about the same subject",
      skills.leaks(doc("Worked example: a counter with two buttons",
                       "Each button dispatches a different action",
                       "The reducer decides what the next state is"), live) == [],
      str(skills.leaks(doc("Worked example: a counter with two buttons",
                           "Each button dispatches a different action",
                           "The reducer decides what the next state is"), live)))

# --------------------------------------------------------------------------- #
print("\n== the leak check is WIRED IN, not just available ==")
import inspect                                                    # noqa: E402
from guardrails import guardrails                                 # noqa: E402
src = inspect.getsource(guardrails.check)
check("the guardrails run it on the assembled document",
      "leak_failures" in src, src[:0])
from src.course_loader import Session                              # noqa: E402
gr = guardrails.check(
    leaked_flow, Session(number=12, name="Rendering", module="Core", topic="Rendering",
                         key_takeaways=["1. Rendering: what triggers a re-render"]),
    False, False, course=REACT, skills=live)
check("…so a leaked brief FAILS the run",
      any("teaching flow" in f for f in gr.failures), str(gr.failures)[:400])

from src import pipeline                                          # noqa: E402
psrc = inspect.getsource(pipeline.evaluate)
check("the run resolves skills for its SESSION, not the course alone",
      "_skills.applicable(course, _session_no)" in psrc, psrc[:0])

# --------------------------------------------------------------------------- #
print("\n== drafting groups instructions instead of scattering them ==")
RAW = ("start with the problem then build intuition then the concept, "
       "explain intuition before formal definitions, use simple language before "
       "technical terms, and connect each concept to the previous session")
drafts = skills.from_requirements(RAW, model=lambda p: json.dumps({"skills": [
    {"category": "teaching_flow", "text": "The teaching order.",
     "instructions": ["Open every concept on the problem it solves, build intuition, "
                      "then name the concept."],
     "source_quotes": ["start with the problem then build intuition then the concept"]},
    {"category": "teaching_guidelines", "text": "How to explain.",
     "instructions": ["Give the intuition before the formal definition."],
     "source_quotes": ["explain intuition before formal definitions"]},
    # A model that keeps splitting — the old behaviour — must not be able to scatter a
    # category across several skills just by returning it twice.
    {"category": "teaching_guidelines", "text": "How to explain, again.",
     "instructions": ["Use plain words before any technical term.",
                      "Tie every new concept back to the previous session."],
     "source_quotes": ["use simple language before technical terms"]},
]}))
check("one skill per category, not one per sentence", len(drafts) == 2, str(len(drafts)))
guide = [d for d in drafts if d["category"] == "teaching_guidelines"][0]
check("…the split category is merged back into one skill",
      len(guide["instructions"]) == 3, str(guide["instructions"]))
check("…in the order the model returned them",
      guide["instructions"][0].startswith("Give the intuition"),
      str(guide["instructions"]))
check("…keeping every quote it was drawn from", len(guide["source_quotes"]) == 2,
      str(guide["source_quotes"]))
check("…under a sentence that names the whole group, not just its first line",
      "explains" in guide["text"].lower(), guide["text"])
check("an untraceable draft is still dropped",
      skills.from_requirements("x", model=lambda p: json.dumps({"skills": [
          {"category": "teaching_flow", "text": "Nobody asked for this.",
           "instructions": ["Invent something."]}]})) == [],
      "a draft with no quote must never be offered for approval")

# The drafter must be TOLD the categories and the WHAT/HOW line, or it will not group.
dsrc = inspect.getsource(skills.from_requirements)
check("the drafting prompt names the four categories",
      all(c in skills._CATEGORY_BRIEF for c in skills.CATEGORIES),
      skills._CATEGORY_BRIEF)
check("…and forbids curriculum being filed as a skill",
      "that is CURRICULUM" in dsrc, dsrc[:0])

stored = skills.store_drafts(REACT, drafts, created_by=ALICE, scope="session",
                             session_ref=14)
check("drafts can be stored against a session", stored == 2, str(stored))
check("…and they arrive as drafts, applying to nothing yet",
      all(s["status"] == "draft" for s in db.skills(REACT)
          if s.get("session_ref") == "14"))

# --------------------------------------------------------------------------- #
print("\n== what an import may and may not carry ==")
n = db.import_skills(REACT, DBMS, ALICE)
imported = {s["text"]: s for s in db.skills(DBMS)}
check("a course-scoped skill comes over",
      any(s.get("category") == "teaching_guidelines" for s in imported.values()),
      str(list(imported)))
check("…with its instructions intact",
      any(len(s.get("instructions") or []) == 4 for s in imported.values()),
      str([(t[:24], len(s.get('instructions') or [])) for t, s in imported.items()]))
check("a SESSION skill does not — its numbering means nothing here",
      not any(s.get("scope") == "session" for s in imported.values()),
      str(list(imported)))
check("nor does a GLOBAL one — it already applies",
      "House rule: define a term the first time it is used." not in imported,
      str(list(imported)))
check("a reviewer correction is a proposal in the new course, not a decision",
      all(s["status"] == "draft" for s in imported.values()),
      str([(t[:20], s["status"]) for t, s in imported.items()]))

# --------------------------------------------------------------------------- #
print("\n== the run records which set of skills it was written under ==")
v12 = db.skills_version(REACT, 12)
v13 = db.skills_version(REACT, 13)
check("two sessions of one course do not claim the same set", v12 != v13, f"{v12} {v13}")
check("…and both are recorded", bool(v12) and bool(v13), f"{v12} {v13}")

# --------------------------------------------------------------------------- #
print("\n== the writer is told the WHAT/HOW line at system level too ==")
prompt = (ROOT / "harness" / "system_prompt.md").read_text()
check("the system prompt draws the line",
      "# WHAT vs HOW" in prompt, prompt[:0])
check("…states the full precedence order",
      "HARD RULES → COURSE REVIEWER SKILLS → SESSION SKILLS → COURSE SKILLS → GLOBAL"
      in prompt, prompt[:0])
check("…forbids the brief appearing as content",
      "THE BRIEF IS NEVER CONTENT" in prompt, prompt[:0])
check("…names the flow-as-bullets failure specifically",
      'bullets are "Problem / Concept / Mechanism / Example"' in prompt, prompt[:0])
check("…and the self-check ends with a leak audit",
      "Brief-leak audit" in prompt, prompt[:0])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
