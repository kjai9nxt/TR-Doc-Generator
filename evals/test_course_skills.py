"""COURSE SKILLS: the instructions a course is written under, authored and approved.

    python -m evals.test_course_skills        # no API key needed, ~5 seconds

WHY THIS EXISTS. A React course needs things an Operating Systems course does not — show
the snippet, explain it line by line, one worked example pattern followed throughout —
and there was nowhere to put them. The harness is one set of instructions for every
course, and `learning.py`'s rules are INFERRED from corrections after the fact, not
authored up front.

A skill is an authored, approved instruction scoped to one course. Three ways in, two of
them authoring:

  A  the user writes it
  B  the user writes rough requirements and the agent splits them into atomic skills,
     each shown against the source text, each approved individually
  C  imported from another course that already has it

Two properties carry most of the weight here:

  · a DRAFT skill must not affect generation. Approval is the whole point of the
    workflow, and a draft that already applies makes it theatre.
  · a skill is scoped to its course and must never leak into another one — the same
    guarantee the deck store and the course profile now give.

The database is a throwaway under TR_DATA_DIR.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skills_test_")
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


from src import db, learning, skills                              # noqa: E402

REACT = "React Fundamentals"
OS = "Operating Systems"
ALICE = "alice@nxtwave.co.in"
BOB = "bob@nxtwave.co.in"

db.init()

print("\n== A: the user writes a skill, and approves it ==")
sid = db.add_skill(REACT, "Show the snippet before explaining it.",
                   kind="style", source="user", created_by=ALICE)
check("it is stored", isinstance(sid, int) and sid > 0, str(sid))
got = db.skills(REACT)
check("…and listed for its course", len(got) == 1 and got[0]["text"].startswith("Show"),
      str(got))
check("…as a DRAFT", got[0]["status"] == "draft", str(got[0]["status"]))
check("a draft does NOT reach generation", skills.applicable(REACT) == [],
      str(skills.applicable(REACT)))
check("approving it needs a person", db.approve_skill(sid, BOB) is True)
check("…and then it applies",
      [s["text"] for s in skills.applicable(REACT)]
      == ["Show the snippet before explaining it."],
      str(skills.applicable(REACT)))
got = db.skills(REACT)[0]
check("…recording who approved it and when",
      got["approved_by"] == BOB and got["approved_at"], str(got))

print("\n== a skill belongs to ONE course ==")
db.add_skill(OS, "Trace the Banker's algorithm with real numbers.",
             kind="content", source="user", created_by=ALICE)
for s in db.skills(OS):
    db.approve_skill(s["id"], ALICE)
check("React sees only its own",
      [s["text"] for s in skills.applicable(REACT)]
      == ["Show the snippet before explaining it."], str(skills.applicable(REACT)))
check("…and the OS course only its own",
      [s["text"] for s in skills.applicable(OS)]
      == ["Trace the Banker's algorithm with real numbers."], str(skills.applicable(OS)))
check("a course with none gets none", skills.applicable("Untouched Course") == [])

print("\n== it reaches the writer, labelled as AUTHORED ==")
# The channel already exists — learning.learned_rules_block() is injected at system level
# on every generation call and read by the judge. A skill goes down it, but must not be
# confused with a learned rule: one was written by a person up front, the other inferred
# from a correction afterwards, and they carry different authority.
blk = learning.learned_rules_block(REACT)
check("the block carries the skill", "Show the snippet before explaining it." in blk,
      blk[:200])
# It heads its own section, and that section is the COURSE's brief — distinct from the
# learned rules travelling beside it, which the agent inferred from corrections rather
# than being told up front.
check("…under its own heading, named for this course",
      f"HOW '{REACT}' IS WRITTEN" in blk, blk[:300])
check("…and it is still separable from the learned rules it travels with",
      "RULES LEARNED FROM EARLIER CORRECTIONS" not in blk.split("HOW '")[1][:400]
      if "HOW '" in blk else False, blk[:400])
check("…and says it was authored, not inferred",
      "authored" in blk.lower() or "written for this course" in blk.lower(), blk[:400])
check("another course's block does not carry it",
      "Show the snippet" not in learning.learned_rules_block(OS),
      learning.learned_rules_block(OS)[:200])

print("\n== editing and retiring ==")
check("a skill can be edited", db.edit_skill(sid, "Show the snippet first, then explain it."))
check("…and editing sends it back to draft",
      db.skills(REACT)[0]["status"] == "draft", str(db.skills(REACT)[0]["status"]))
check("…so an edited skill stops applying until re-approved",
      skills.applicable(REACT) == [], str(skills.applicable(REACT)))
db.approve_skill(sid, ALICE)
check("re-approved, it applies again with the NEW text",
      skills.applicable(REACT)[0]["text"] == "Show the snippet first, then explain it.",
      str(skills.applicable(REACT)))
check("retiring it stops it applying", db.retire_skill(sid, ALICE)
      and skills.applicable(REACT) == [], str(skills.applicable(REACT)))
check("…and it is not listed as a live skill any more",
      not any(x["id"] == sid for x in db.skills(REACT)),
      str([x["id"] for x in db.skills(REACT)]))
check("…but it is KEPT, so an old document can still be explained",
      any(x["id"] == sid and x["status"] == "retired"
          for x in db.skills(REACT, include_retired=True)),
      str(db.skills(REACT, include_retired=True)))

print("\n== C: importing from a course that already has it ==")
db.add_skill(OS, "Never introduce a term before the gap it fills.",
             kind="style", source="user", created_by=ALICE)
for s in db.skills(OS):
    db.approve_skill(s["id"], ALICE)
n = db.import_skills(OS, REACT, ALICE)
check("both of the source course's approved skills come over", n == 2, str(n))
imported = {s["text"]: s for s in db.skills(REACT)}
check("…recording where they came from",
      all(s["source"] == f"imported:{OS}" for t, s in imported.items()
          if t.startswith(("Trace", "Never"))), str(list(imported.values())))
check("…as DRAFTS, needing approval in the new course",
      all(s["status"] == "draft" for t, s in imported.items()
          if t.startswith(("Trace", "Never"))),
      str([(t, s["status"]) for t, s in imported.items()]))
check("…so nothing is imported into generation unreviewed",
      skills.applicable(REACT) == [], str(skills.applicable(REACT)))
check("importing the same skill twice does not duplicate it",
      db.import_skills(OS, REACT, ALICE) == 0
      and len([s for s in db.skills(REACT) if s["text"].startswith("Trace")]) == 1,
      str([s["text"] for s in db.skills(REACT)]))

print("\n== B: rough requirements become atomic drafts, each with its source ==")
# The agent FORMALISES what the user said — it does not invent. Every draft must be
# traceable to the words it came from, or the approval step is a rubber stamp.
RAW = ("code snippets should be shown, explain code each line by line, "
       "provide one example and follow it for all")
drafts = skills.from_requirements(RAW, model=lambda prompt: json.dumps({"skills": [
    {"text": "Show a code snippet for every concept that has one.",
     "kind": "style", "source_quote": "code snippets should be shown"},
    {"text": "Explain each code snippet line by line.",
     "kind": "style", "source_quote": "explain code each line by line",
     "check": {"assert": "field_present", "field": "walkthrough",
               "when_block": "code"}},
    {"text": "Use one worked-example pattern and keep it for the whole course.",
     "kind": "style", "source_quote": "provide one example and follow it for all"},
]}))
check("the free text becomes several atomic skills", len(drafts) == 3, str(len(drafts)))
check("…each quoting the words it came from",
      all(d.get("source_quote") and d["source_quote"] in RAW for d in drafts),
      str([d.get("source_quote") for d in drafts]))
check("…and a checkable one carries its check",
      any((d.get("check") or {}).get("assert") == "field_present" for d in drafts),
      str([d.get("check") for d in drafts]))
stored = skills.store_drafts(REACT, drafts, created_by=ALICE)
check("they are stored as drafts", stored == 3, str(stored))
check("…and none of them applies yet", skills.applicable(REACT) == [],
      str(skills.applicable(REACT)))
check("…each marked as derived from the requirements",
      all(s["source"] == "requirements" for s in db.skills(REACT)
          if s["text"].startswith(("Show a code", "Explain each", "Use one"))),
      str([(s["text"][:20], s["source"]) for s in db.skills(REACT)]))
check("a model that invents a skill with no source quote is dropped",
      skills.from_requirements("x", model=lambda p: json.dumps({"skills": [
          {"text": "Something nobody asked for.", "kind": "style"}]})) == [],
      "a draft with no traceable source must not be offered for approval")

print("\n== the PRODUCTION drafting call, not just the injected one ==")
# This shipped broken: the seam that makes drafting testable was a one-argument
# callable, the production default called llm.complete(prompt, label=...) — a signature
# that does not exist — and the resulting TypeError was swallowed by a bare except that
# returned []. Every attempt at path B told the author their own words were untraceable.
# So the default is exercised here against llm.complete's REAL signature.
import inspect
from src import llm
_calls = []
_real_complete = llm.complete
def _spy(*a, **kw):
    # Binding against the real signature is the whole point — a mismatch raises here.
    inspect.signature(_real_complete).bind(*a, **kw)
    _calls.append(kw)
    return json.dumps({"skills": [
        {"text": "Show a code snippet for every concept that has one.",
         "kind": "style", "source_quote": "code snippets should be shown"}]})
llm.complete = _spy
try:
    made = skills.from_requirements("code snippets should be shown")
finally:
    llm.complete = _real_complete
check("the default path reaches the model with a valid call", len(_calls) == 1, str(_calls))
check("…and its answer becomes a draft", len(made) == 1, str(made))
check("…asking for a real configured model",
      bool(_calls and _calls[0].get("model")), str(_calls))

# A failed CALL and an answer nothing survived are the same empty list and completely
# different problems. The author must not be told their text was the trouble when the
# model was never reached.
def _dead(*a, **kw):
    raise RuntimeError("connection refused")
llm.complete = _dead
try:
    skills.from_requirements("code snippets should be shown")
    raised = None
except skills.ModelUnavailable as e:
    raised = e
except Exception as e:
    raised = e
finally:
    llm.complete = _real_complete
check("a model that cannot be reached raises rather than reporting an empty result",
      isinstance(raised, skills.ModelUnavailable), repr(raised))

print("\n== rough notes become a BRIEF, not an echo of the notes ==")
# What shipped: four rough phrases came back as four near-verbatim rules, two of which
# were the same requirement said twice ("code snippets should be small" / "Small code
# snippets to be used"). The prompt said "Split compound requirements; merge nothing"
# and "restate", so that is exactly what it did. A restatement is not a skill — the
# writer gains nothing from being handed the author's own shorthand back.
NOTES = ("code snippets should be small, Syntax should be shown, Small code snippets to "
         "be used, No extra code that is wunwanted that shoulds nto be provided")
merged = skills.from_requirements(NOTES, model=lambda p: json.dumps({"skills": [
    {"text": ("Keep code snippets concise and minimal, including only the syntax needed "
              "to demonstrate the concept; do not include extraneous code."),
     "kind": "content",
     "source_quotes": ["code snippets should be small", "Small code snippets to be used",
                       "No extra code that is wunwanted that shoulds nto be provided"]},
    {"text": ("Display the syntax explicitly in every code snippet so the reader can see "
              "the language structure being taught."),
     "kind": "content", "source_quotes": ["Syntax should be shown"]},
]}))
check("the same requirement said twice becomes ONE skill", len(merged) == 2,
      str([d["text"][:40] for d in merged]))
check("…carrying every phrase it was drawn from",
      len(merged[0]["source_quotes"]) == 3, str(merged[0]["source_quotes"]))
check("…including the one with the author's typos, quoted exactly",
      "wunwanted" in " ".join(merged[0]["source_quotes"]),
      str(merged[0]["source_quotes"]))
check("…and source_quote stays the first, for anything wanting one string",
      merged[0]["source_quote"] == merged[0]["source_quotes"][0],
      str(merged[0]["source_quote"]))
check("a genuinely different requirement is NOT merged away",
      any("syntax" in d["text"].lower() and "explicit" in d["text"].lower()
          for d in merged), str([d["text"][:50] for d in merged]))
check("…and the drafts are articulated, not the notes echoed back",
      all(len(d["text"]) > 60 for d in merged), str([len(d["text"]) for d in merged]))

# The traceability rule still bites, and now on every quote.
invented = skills.from_requirements("code snippets should be small",
    model=lambda p: json.dumps({"skills": [
        {"text": "Use four-space indentation everywhere.", "kind": "style",
         "source_quotes": ["indentation should be four spaces"]}]}))
check("a quote that is not in the input is still dropped", invented == [],
      str(invented))
partial = skills.from_requirements("code snippets should be small",
    model=lambda p: json.dumps({"skills": [
        {"text": "Keep snippets short enough to read at a glance.", "kind": "style",
         "source_quotes": ["code snippets should be small", "never said this"]}]}))
check("…and a skill with one real quote keeps only the real one",
      len(partial) == 1 and partial[0]["source_quotes"] == ["code snippets should be small"],
      str(partial))

stored_id = None
for d in merged:
    stored_id = db.add_skill(REACT, d["text"], kind=d["kind"], source="requirements",
                             created_by=ALICE, source_quotes=d["source_quotes"])
row = next(x for x in db.skills(REACT) if x["id"] == stored_id)
check("the quotes survive the round trip to the store",
      row["source_quotes"] == ["Syntax should be shown"], str(row["source_quotes"]))
# A row written before source_quotes existed has only the single column; a caller must
# not have to know which kind of row it is holding.
_legacy = db.add_skill(REACT, "Written the old way.", created_by=ALICE,
                       source_quote="the old single quote")
_lrow = next(x for x in db.skills(REACT) if x["id"] == _legacy)
check("…and a skill carrying only the old single quote still reads as a list",
      _lrow["source_quotes"] == ["the old single quote"], str(_lrow["source_quotes"]))
check("…while one with no quote at all reads as an empty list",
      db.skills(REACT)[0]["source_quotes"] == [],
      str(db.skills(REACT)[0]["source_quotes"]))

print("\n== the approved skills compose into a BRIEF, not a bullet dump ==")
for x in db.skills(REACT):
    db.approve_skill(x["id"], ALICE)
brief = skills.block(REACT)
check("it is presented as this course's brief", "the course brief" in brief, brief[:120])
check("…grouped by what each kind governs",
      "WHAT THIS COURSE MUST CONTAIN" in brief, brief)
check("…with content before wording, the order a writer needs",
      brief.index("MUST CONTAIN") < brief.index("MUST BE WRITTEN")
      if "MUST BE WRITTEN" in brief else True, brief)
check("…and it still declares precedence", "THE BRIEF WINS" in brief, brief)
check("a course with no approved skills composes nothing",
      skills.block("Course With No Skills At All") == "")

print("\n== the checks a skill carries are a CLOSED vocabulary ==")
ok, why = skills.validate_check({"assert": "block_present", "block": "code",
                                 "on_roles": ["working_example"]})
check("a known assertion is accepted", ok, why)
ok, why = skills.validate_check({"assert": "run_python", "code": "import os"})
check("an unknown assertion is refused", not ok, why)
check("…and says what is allowed", "block_present" in why, why)

print("\n== the run records which set of skills produced it ==")
# Without this there is no way to explain why last month's document differs from today's.
db.add_skill(REACT, "Keep every snippet under twelve lines.", created_by=ALICE)
for x in db.skills(REACT):
    db.approve_skill(x["id"], ALICE)
v1 = db.skills_version(REACT)
check("a course with approved skills has a version", bool(v1), repr(v1))
db.create_run("skillrun", user_email=ALICE, course=REACT, team_id=None,
              session_no=1, title="t", enforce_time=True)
row = next((r for r in db.runs() if r["id"] == "skillrun"), {})
check("…and the run is stamped with it", row.get("skills_version") == v1,
      f"{row.get('skills_version')!r} vs {v1!r}")
db.add_skill(REACT, "Name the file each snippet lives in.", created_by=ALICE)
for x in db.skills(REACT):
    db.approve_skill(x["id"], ALICE)
check("approving another skill changes the version", db.skills_version(REACT) != v1,
      f"{db.skills_version(REACT)!r} vs {v1!r}")
check("…and the old run still says which set IT was written under",
      next(r for r in db.runs() if r["id"] == "skillrun")["skills_version"] == v1,
      str(next(r for r in db.runs() if r["id"] == "skillrun").get("skills_version")))
check("a course with no skills stamps nothing",
      db.skills_version("Untouched Course") == "", repr(db.skills_version("Untouched Course")))

print("\n== deleting a course takes its skills with it ==")
db.add_skill("Doomed Skills Course", "Something.", created_by=ALICE)
check("stored", db.skills("Doomed Skills Course") != [])
db.delete_course("Doomed Skills Course")
check("…and gone with the course",
      db.skills("Doomed Skills Course", include_retired=True) == [],
      str(db.skills("Doomed Skills Course", include_retired=True)))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
