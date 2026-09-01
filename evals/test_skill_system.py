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
import inspect
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
    for s in db.skills(course, include_retired=True):
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
print("\n== there is NO 'every course' scope, and a rule for every course says so ==")
# There was one: a skill under a reserved course name governed every course on the
# instance. It is gone, because the repo already holds the place for a house rule —
# harness/system_prompt.md and harness/style_guide.md are read on every generation for
# every course, and are reviewed and versioned like the code beside them. Two places to
# write one rule is worse than either alone: the one you did not check is the one in
# force.
check("the store offers two scopes, not three", db.SCOPES == ("course", "session"),
      str(db.SCOPES))
check("…and the reserved global course name is gone with it",
      not hasattr(db, "GLOBAL_COURSE"))
check("a skill asking for it falls back to the course rather than reaching further",
      (lambda i: db.skills(DBMS) and [x for x in db.skills(DBMS) if x["id"] == i][0]
       ["scope"] == "course")(db.add_skill(DBMS, "Tried to be global.", scope="global",
                                           created_by=ALICE)))
check("…and the brief never grows a global section",
      "GLOBAL" not in skills.block(DBMS), skills.block(DBMS))
check("the writer is told the order WITHOUT one",
      "COURSE SKILLS → GLOBAL" not in (ROOT / "harness" / "system_prompt.md").read_text())
for _s in db.skills(DBMS, include_retired=True):
    if _s["text"] == "Tried to be global.":
        db.retire_skill(_s["id"], ALICE)

# --------------------------------------------------------------------------- #
print("\n== PRECEDENCE: reviewer, then session, then course ==")
rev = db.add_skill(REACT, "Corrections review keeps making on this course.",
                   category="reviewer",
                   instructions=["Never call a function component a class component."],
                   source="user", created_by=ALICE)
db.approve_skill(rev, ALICE)
b = skills.block(REACT, 12)
order = [b.index(h) for h in ("## COURSE REVIEWER SKILLS", "## SESSION SKILLS",
                              "## COURSE SKILLS")]
check("every tier is present and headed", all(i >= 0 for i in order), str(order))
check("…strongest first", order == sorted(order), str(order))
check("the order is also STATED, so it survives a model reading it out of order",
      "HARD RULES → COURSE REVIEWER SKILLS → SESSION SKILLS → COURSE SKILLS" in b,
      b[:1600])
check("applicable() returns them in that order too",
      [s["id"] for s in skills.applicable(REACT, 12)] == [rev, sid, gid],
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
print("\n== the brief reaches the WRITER where the writer is looking ==")
# WHY THIS IS NOT ENOUGH TO SEND IT ONCE. The brief goes to the model as a system block,
# which is where its authority comes from. On a real run that is 2,591 characters of it
# inside a 71,114-character system prompt, behind ten thousand tokens of prior-deck
# context, in front of a per-chunk instruction that mentioned it nowhere — `brief` and
# `skill` appeared ZERO times in the whole of context_builder. Nothing was being lost in
# transit; every line arrived. It was simply the one input with no presence in the task
# being answered, and the course owner's instructions came back half-applied.
import inspect as _i                                              # noqa: E402
from src import generator                                         # noqa: E402
_rem = skills.reminder(REACT, 12)
check("the reminder carries every instruction of every skill",
      all(line in _rem
          for sk in skills.applicable(REACT, 12)
          for ins in skills.instructions_of(sk)
          for line in [ins.split("\n")[0]]), _rem)
check("…framed as something to CHECK the output against, not as background",
      "APPLY EVERY LINE" in _rem and "BEFORE YOU RETURN" in _rem, _rem[:400])
check("…and it repeats the no-leak rule, since it is now the last thing read",
      "never what you write about" in _rem, _rem[:600])
check("a course with no skills adds nothing at all",
      skills.reminder("A Course With Nothing", 1) == "")

# It is appended in _complete_json rather than at each call site, so a generator added
# later cannot forget it. Checked by CALLING one, not by reading the source.
_sent = {}
_real_complete = generator.llm.complete
generator.llm.complete = lambda **kw: (_sent.update(kw), '{"ok": 1}')[1]
try:
    generator.generate("WRITE THE OPENING.", course=REACT, session=12)
finally:
    generator.llm.complete = _real_complete
check("every content-writing call gets the brief",
      "APPLY EVERY LINE" in _sent.get("user", ""), str(_sent.get("user", ""))[:200])
check("…at the END of the user message, after the instruction",
      _sent["user"].index("WRITE THE OPENING.") < _sent["user"].index("APPLY EVERY LINE"))
check("…and it still has system-level authority as well",
      "Explain the mechanism only once the concept is named."
      in _sent.get("system_extra", "") or "HOW '" in _sent.get("system_extra", ""),
      _sent.get("system_extra", "")[:200])
check("the self-check asks the writer to find where it obeyed each line",
      "Brief-adherence audit" in (ROOT / "harness" / "system_prompt.md").read_text())

# --------------------------------------------------------------------------- #
print("\n== the leak check is WIRED IN, not just available ==")
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
print("\n== a skill is a piece of WRITING, and keeps the shape it was written in ==")
# A skill is a fragment of the prompt the writer works from, so an author lays it out
# the way they would lay out any instruction: a paragraph, the points it breaks into,
# sometimes both. All of it used to go through `" ".join(text.split())`, which collapses
# newlines along with spaces — so a laid-out note was stored, approved and handed to the
# model as one run-on paragraph. It had to be fixed in four places, and this checks all
# four, because fixing three of them looks exactly like fixing none.
LAID_OUT = """Explain every snippet line by line — the learner should write it themselves after.

- name each variable before it is used
- say what the line does, not what it says

Keep snippets under 12 lines. Never show a whole file."""
check("1. the STORE keeps the layout byte for byte",
      db.skill_body(LAID_OUT) == LAID_OUT, repr(db.skill_body(LAID_OUT))[:160])
check("…tidying only the spaces WITHIN a line",
      db.skill_body("a   b\n\n  c  ") == "a b\n\nc")
check("…collapsing a run of blank lines to one, and dropping trailing ones",
      db.skill_body("a\n\n\n\nb\n\n\n") == "a\n\nb")
lid = db.add_skill(REACT, LAID_OUT, category="teaching_guidelines", created_by=ALICE)
db.approve_skill(lid, ALICE)
stored = [x for x in db.skills(REACT) if x["id"] == lid][0]
check("…so it comes back out exactly as it went in", stored["text"] == LAID_OUT,
      repr(stored["text"])[:160])

check("2. instructions_of does not flatten a point that spans lines",
      skills.instructions_of({"instructions": ["one\ntwo"]}) == ["one\ntwo"],
      str(skills.instructions_of({"instructions": ["one\ntwo"]})))

blk = skills.block(REACT)
check("3. the BRIEF keeps the bullets as bullets",
      "\n  - name each variable before it is used" in blk, blk)
check("…and indents every continuation under its marker, so a three-line skill does "
      "not read as three skills",
      all(ln.startswith("  ") for ln in blk.split("- Explain every snippet")[1]
          .split("\n")[1:4] if ln.strip()), blk)
check("…and a grouped point that spans lines keeps its break, indented under its number",
      "\n       " in "\n".join(skills._indented("a\nb", "    1. ", "       ")),
      str(skills._indented("a\nb", "    1. ", "       ")))

check("4. the prompt tells the model to give the layout back",
      "KEEP THEIR SHAPE" in inspect.getsource(skills.articulate))
check("…asking for it as an ARRAY OF LINES, not newlines inside one string",
      '\\"lines\\": [' in inspect.getsource(skills.articulate),
      "a model will not put real newlines in a JSON string; it returns prose every time")

print("\n== 5. and the ARTICULATION is checked for shape, not only for content ==")
# Everything the author said, in the right order, with nothing dropped — and the list
# run together into prose. Content-complete on purpose, so this tests the SHAPE check
# and not the content one that runs before it.
FLAT = ("Explain every snippet line by line — the learner should write it themselves "
        "after. Name each variable before it is used. Say what the line does, not what "
        "it says. Keep snippets under 12 lines. Never show a whole file.")
check("a list run together into prose is caught",
      "LIST" in skills.lossy(LAID_OUT, FLAT), skills.lossy(LAID_OUT, FLAT))
check("…and two blocks merged into one is caught",
      "blank line" in skills.lossy("first block\n\nsecond block", "first block second block"))
check("…while a faithful layout passes",
      skills.lossy("do X\n\n- a\n- b", "Do X.\n\n- A\n- B") == "")
check("…and a plain sentence is never asked for structure it never had",
      skills.lossy("show the snippet first", "Show the snippet first.") == "")

print("\n== …and it may not write the brief FOR the author ==")
# The other side of "you may sharpen it". Once the model was allowed to clarify a vague
# note and to give content structure, "make the analogies good" came back as four
# confident rules about mapping, domains and how to test an analogy — none of which the
# author had written, all of which would have been approved as theirs.
VAGUE = "make the analogies good"
INVENTED = ("Use analogies that map precisely onto the concept being taught.\n"
            "- The analogy must clarify the target concept, not obscure it.\n"
            "- Every part of the analogy must correspond to a part of what is being "
            "explained.\n"
            "- Choose analogies from domains the reader already understands.\n"
            "- Test each analogy by asking what someone would wrongly learn from it.")
check("a four-word note grown into a paragraph of rules is caught",
      "expansion" in skills.lossy(VAGUE, INVENTED), skills.lossy(VAGUE, INVENTED))
check("…while saying the same short thing properly is not",
      skills.lossy(VAGUE, "Make every analogy map onto the concept it explains.") == "")
check("…and a long note may still be rearranged freely",
      skills.lossy(" ".join(["word"] * 40), " ".join(["word"] * 100)) == "",
      "3x of a 40-word note is 120; 100 is inside it")
check("…with a flat allowance so a SHORT note has room to be said properly",
      skills.lossy("show snippet first", "Show the code snippet before explaining it, "
                   "so the learner reads the code before the prose about it.") == "")

check("the prompt asks for the structure the content deserves",
      "ADD THE STRUCTURE THE CONTENT DESERVES" in inspect.getsource(skills.articulate))
check("…worked through for the case that is missed constantly, a run-together sequence",
      "is FOUR STEPS" in inspect.getsource(skills.articulate))
check("…and forbids padding a list out with lines the author never said",
      "MUST BE SOMETHING THEY ACTUALLY SAID" in inspect.getsource(skills.articulate))
check("…and keeps a vague note to ONE instruction",
      "A VAGUE NOTE STAYS ONE INSTRUCTION" in inspect.getsource(skills.articulate))

_shape_tries = []
def _flattener(prompt):
    _shape_tries.append(prompt)
    return json.dumps({"lines": [FLAT], "category": "teaching_guidelines"})
check("a model that flattens the author's list is rejected",
      skills.articulate(LAID_OUT, model=_flattener) is None)
check("…and told, on the retry, that the list has to come back as a list",
      len(_shape_tries) == 2 and "LIST" in _shape_tries[1], str(len(_shape_tries)))

print("\n== a RUN OF NAMED RULES becomes a list, not a wall with headings in it ==")
# THE CASE THIS EXISTS FOR. A course brief is usually written as a short title, a
# sentence saying what it means, then the next one — ten of them. Left as loose blocks,
# the skill was one long document whose file name was its FIRST RULE, so the other nine
# were named after something that was one of them, and the tenth was as easy to miss as
# if it had not been written.
NAMED_RULES = """Explain in Simple Language First
Explain the concept in clear, beginner-friendly language.

Explain Before Showing Code
Do not introduce code without first explaining what it is intended to achieve.

Build Progressively
Prefer extending the existing example rather than starting from a new one."""
_drafted = skills.articulate(NAMED_RULES, model=lambda p: json.dumps({
    "name": "How this course explains concepts and code",
    "category": "teaching_guidelines",
    "instructions": [
        {"title": "Explain in Simple Language First",
         "text": "Explain the concept in clear, beginner-friendly language."},
        {"title": "Explain Before Showing Code",
         "text": "Do not introduce code without first explaining what it is intended to achieve."},
        {"title": "Build Progressively",
         "text": "Prefer extending the existing example rather than starting from a new one."},
    ]}))
check("three named rules become three instructions",
      len((_drafted or {}).get("instructions") or []) == 3, str(_drafted))
check("…each keeping its own title on its own line, above what it requires",
      (_drafted["instructions"][0].split("\n")
       == ["Explain in Simple Language First",
           "Explain the concept in clear, beginner-friendly language."]),
      repr(_drafted["instructions"][0]))
check("…and the skill is named for the WHOLE set, never for its first rule",
      _drafted["text"] == "How this course explains concepts and code",
      _drafted["text"])
# The check runs against the skill PUT BACK TOGETHER. Without that, a perfectly
# structured answer — a name and three instructions — looks like one unbroken paragraph
# and is rejected for flattening the very list it just built.
check("…and a well-structured answer is not rejected as flattened",
      skills.lossy(NAMED_RULES,
                   skills._assembled(_drafted["text"], _drafted["instructions"])) == "",
      skills.lossy(NAMED_RULES,
                   skills._assembled(_drafted["text"], _drafted["instructions"])))
check("a single instruction still comes back as one, with no list of one",
      (skills.articulate("show the snippet first", model=lambda p: json.dumps(
          {"lines": ["Show the snippet first."], "category": "teaching_flow"}))
       or {}).get("instructions") == [])
check("the prompt names the run-of-named-rules case outright",
      "A RUN OF NAMED RULES" in inspect.getsource(skills.articulate))
check("…and forbids naming the set after its first rule",
      "NEVER the first rule's title" in inspect.getsource(skills.articulate))
_SERVER = (ROOT / "server.py").read_text()
check("…and the endpoint stores the instructions the agent found",
      'body.instructions or (drafted or {}).get("instructions")' in _SERVER,
      "dropping them here would throw away the whole point of the articulation")

check("the `lines` array is joined back into the layout",
      (skills.articulate("do x\n\n- a\n- b", model=lambda p: json.dumps(
          {"lines": ["Do X.", "", "- A", "- B"], "category": "teaching_flow"}))
       or {}).get("text") == "Do X.\n\n- A\n- B")
# THE INPUT ITSELF used to be flattened on the first line of articulate(), so the model
# never saw the layout, the shape check had nothing to miss, and `source_quote` recorded
# a run-on paragraph as "your own words". One line, three bugs.
_seen = {}
skills.articulate(LAID_OUT, model=lambda p: _seen.setdefault("p", p) and None or {"text": "x"})
check("the author's note reaches the model laid out, not flattened",
      "\n- name each variable" in _seen["p"], _seen["p"][-400:])

# …AND THE SCREEN. The store, the prompt and the renderer each have to keep the layout,
# and the renderer is the one the author actually looks at before approving. Checked as
# source text rather than by rendering it, which is the most this suite can do for JSX —
# enough to catch the property being deleted, not enough to catch it being broken.
_APP = (ROOT / "frontend" / "src" / "App.jsx").read_text()
check("5. the renderer keeps a step's description WITH the step",
      "list.items[list.items.length - 1].body.push(line)" in _APP,
      "a numbered heading and the sentence under it are one thing; split apart, the "
      "heading rendered small and grey and its own description rendered big and bold")
check("…and a step that has a description is set as a label, not as a bullet",
      "it.body.length ? 'labelled' : ''" in _APP)
check("…and a skill is a FILE that opens and closes",
      'className="filebtn"' in _APP and "aria-expanded={shown}" in _APP)
check("…which is forced open while it is being edited",
      "const shown = open || editing" in _APP)

db.retire_skill(lid, ALICE)   # leave the brief as the later checks expect it

# --------------------------------------------------------------------------- #
print("\n== articulating a skill EDITS the author's English, it does not summarise ==")
# THE BUG THIS GUARDS. The prompt asked for "one or two full sentences" and told the
# model not to echo the author's phrasing. Between those two instructions, a note
# carrying three worked examples came back as one clean sentence carrying none — and the
# author was shown that sentence to APPROVE, with nothing to say anything had gone.
# Approving it is how the loss becomes permanent, which is the worst possible shape for
# this failure.
NOTE = ("explain the code line by line, keep snippets under 12 lines, and for useEffect "
        "show the empty dep array first, then add a dep and show what re-runs")
check("a faithful rewrite is clean", skills.lossy(NOTE,
      "Explain the code line by line. Keep snippets under 12 lines. For useEffect, show "
      "the empty dep array first, then add a dep and show what re-runs.") == "")
check("…dropping a NUMBER the author gave is caught",
      "12" in skills.lossy(NOTE, "Explain the code line by line and keep snippets short. "
                                 "For useEffect, show the empty dep array first, then add "
                                 "a dep and show what re-runs."),
      skills.lossy(NOTE, "…"))
check("…dropping a NAME the author gave is caught",
      "useeffect" in skills.lossy(NOTE, "Explain the code line by line, keep snippets "
                                        "under 12 lines, and show dependency arrays "
                                        "before adding to them and re-running.").lower())
check("…and a summary of a long note is caught, even with nothing specific in it",
      "summary" in skills.lossy(
          "explain each concept slowly and carefully, giving the learner the intuition "
          "first, then the definition, then a walked-through case, and always finishing "
          "with what goes wrong when it is misapplied",
          "Explain concepts thoroughly."))
check("…while TIGHTENING a long note is allowed", skills.lossy(
      "explain each concept slowly and carefully, giving the learner the intuition "
      "first, then the definition, then a walked-through case, and always finishing "
      "with what goes wrong when it is misapplied",
      "Explain each concept carefully: give the intuition first, then the definition, "
      "then a walked-through case, and finish with what goes wrong when it is "
      "misapplied.") == "")
check("a short note is never judged on length alone",
      skills.lossy("show the snippet first", "Show the snippet first.") == "")

_tries = []
def _summariser(prompt):
    _tries.append(prompt)
    return json.dumps({"text": "Explain the code well.", "category": "teaching_guidelines"})
check("a model that summarises is REJECTED, not shown to the author",
      skills.articulate(NOTE, model=_summariser) is None)
check("…after being told exactly what it dropped, once",
      len(_tries) == 2 and "REJECTED" in _tries[1] and "12" in _tries[1], str(len(_tries)))
# The caller stores the author's own words when this returns None (server.add_skill),
# so the worst case is an unpolished instruction rather than a truncated one.

_n = {"i": 0}
def _second_time_lucky(prompt):
    _n["i"] += 1
    if _n["i"] == 1:
        return json.dumps({"text": "Explain the code well.", "category": "teaching_guidelines"})
    return json.dumps({"text": "Explain the code line by line. Keep snippets under 12 "
                               "lines. For useEffect, show the empty dep array first, "
                               "then add a dep and show what re-runs.",
                       "category": "teaching_guidelines"})
got = skills.articulate(NOTE, model=_second_time_lucky)
check("…and a faithful retry IS kept", got and "useEffect" in got["text"], str(got))

check("the prompt no longer caps the length",
      "one or two full sentences" not in inspect.getsource(skills.articulate),
      "that cap is what turned a paragraph of examples into a sentence")
check("…and says outright that examples must survive",
      "LOSE NOTHING" in inspect.getsource(skills.articulate))

print("\n== drafting from notes keeps every example too ==")
RAW2 = ("use tables to compare, for example TCP vs UDP side by side. "
        "keep every worked example under 8 steps.")
drafts2 = skills.from_requirements(RAW2, model=lambda p: json.dumps({"skills": [
    {"category": "examples_visuals", "text": "The examples and visuals this course uses.",
     "instructions": ["Use tables for side-by-side comparisons, for example TCP vs UDP.",
                      "Keep every worked example to 8 steps or fewer."],
     "source_quotes": ["use tables to compare", "keep every worked example under 8 steps"]},
]}))
check("a faithful draft is accepted first time", len(drafts2) == 1, str(drafts2))
_d = {"i": 0}
def _lossy_drafter(prompt):
    _d["i"] += 1
    if _d["i"] == 1:
        return json.dumps({"skills": [
            {"category": "examples_visuals", "text": "Use comparisons.",
             "instructions": ["Use tables to compare things."],
             "source_quotes": ["use tables to compare"]}]})
    return json.dumps({"skills": [
        {"category": "examples_visuals", "text": "The examples and visuals this course uses.",
         "instructions": ["Use tables for side-by-side comparisons, for example TCP vs UDP.",
                          "Keep every worked example to 8 steps or fewer."],
         "source_quotes": ["use tables to compare"]}]})
got2 = skills.from_requirements(RAW2, model=_lossy_drafter)
check("a drafter that drops an example is asked again", _d["i"] == 2, str(_d["i"]))
check("…and the version that keeps it is the one returned",
      any("TCP vs UDP" in i for d in got2 for i in d["instructions"]), str(got2))

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
      "HARD RULES → COURSE REVIEWER SKILLS → SESSION SKILLS → COURSE SKILLS" in prompt,
      prompt[:0])
check("…and says where a rule for EVERY course goes instead",
      "belongs in this file and in the style guide" in prompt, prompt[:0])
check("…forbids the brief appearing as content",
      "THE BRIEF IS NEVER CONTENT" in prompt, prompt[:0])
check("…names the flow-as-bullets failure specifically",
      'bullets are "Problem / Concept / Mechanism / Example"' in prompt, prompt[:0])
check("…and the self-check ends with a leak audit",
      "Brief-leak audit" in prompt, prompt[:0])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
