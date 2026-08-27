"""A skill with a CHECK is enforced deterministically, not merely asked for.

    python -m evals.test_skill_enforcement        # no API key needed, ~2 seconds

WHY THIS EXISTS. A skill that is only prose in a prompt is unenforceable. The reason this
agent holds its shape is that the rules that matter are deterministic gates — and the
codebase already knows this: `self_evolution.gated_rules` stops sending a rule to the
judge once its enforcement became mechanical, because a judge re-adjudicating a rule from
prose can fail a compliant document.

So a skill is authored as prose and PROMOTED to a check wherever one is expressible. The
check vocabulary is closed (src/skills.py CHECKS) — an open one means arbitrary predicates
from user input and failure messages nobody can maintain.

Two things this asserts beyond the mechanics:
  · a DRAFT skill's check does not fire. Approval gates enforcement exactly as it gates
    the prompt, or the workflow means nothing.
  · a failure QUOTES the skill. "Guardrail failure" against a rule the reviewer wrote
    last month is no use unless it says which rule.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skill_gate_test_")
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


from src import db, course_loader, skills                          # noqa: E402
from guardrails import guardrails                                  # noqa: E402

db.init()
GOLDEN = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())
cur = course_loader.get_session(
    15, course_loader.load_sessions(ROOT / "Final CN Structure.xlsx"))
REACT = "React Fundamentals"
ALICE = "alice@nxtwave.co.in"

CODE_BLOCK = {"type": "code", "language": "jsx", "code": "const x = 1",
              "walkthrough": [{"lines": "1", "text": "One binding."}]}


def doc_with(block=None, role="working_example"):
    d = copy.deepcopy(GOLDEN)
    sl = d["sections"][0]["slides"][0]
    sl["role"] = role
    sl.pop("analogy", None)
    sl["content"] = [{"type": "text", "text": "A framing line about the example."}]
    if block:
        sl["content"].append(dict(block))
    return d


def fails_for(doc, course=REACT):
    return guardrails.check(doc, cur, False, False, course=course,
                            skills=skills.applicable(course)).failures


print("\n== block_present: a role that must carry a kind of block ==")
sid = db.add_skill(REACT, "Every worked example must show the code it walks through.",
                   kind="structure", source="user", created_by=ALICE,
                   check={"assert": "block_present", "block": "code",
                          "on_roles": ["working_example"]})
check("a DRAFT skill's check does not fire",
      not any("worked example must show" in f for f in fails_for(doc_with())),
      "; ".join(fails_for(doc_with()))[:200])
db.approve_skill(sid, ALICE)
f = fails_for(doc_with())
check("approved, the missing block fails", any("code" in x for x in f), "; ".join(f)[:200])
check("…and the failure QUOTES the skill",
      any("Every worked example must show the code" in x for x in f), "; ".join(f)[:250])
check("…naming the slide", any("Slide 1" in x for x in f), "; ".join(f)[:200])
check("a slide that has the block passes",
      not any("worked example must show" in x for x in fails_for(doc_with(CODE_BLOCK))),
      "; ".join(fails_for(doc_with(CODE_BLOCK)))[:200])
check("a slide with a DIFFERENT role is not held to it",
      not any("worked example must show" in x
              for x in fails_for(doc_with(role="mechanism"))),
      "; ".join(fails_for(doc_with(role="mechanism")))[:200])

print("\n== the check is scoped to its course ==")
check("another course is untouched by it",
      not any("worked example must show" in x
              for x in fails_for(doc_with(), course="Operating Systems")),
      "; ".join(fails_for(doc_with(), course="Operating Systems"))[:200])

print("\n== field_present: a block that must carry a field ==")
db.retire_skill(sid, ALICE)
sid2 = db.add_skill(REACT, "Every snippet must be explained line by line.",
                    kind="style", source="user", created_by=ALICE,
                    check={"assert": "field_present", "field": "walkthrough",
                           "when_block": "code"})
db.approve_skill(sid2, ALICE)
bare = doc_with({"type": "code", "language": "jsx", "code": "const x = 1"})
check("a snippet with no walkthrough fails",
      any("explained line by line" in x for x in fails_for(bare)),
      "; ".join(fails_for(bare))[:200])
check("…and one with a walkthrough passes",
      not any("explained line by line" in x for x in fails_for(doc_with(CODE_BLOCK))),
      "; ".join(fails_for(doc_with(CODE_BLOCK)))[:200])

print("\n== min_count: the document must contain enough of something ==")
db.retire_skill(sid2, ALICE)
sid3 = db.add_skill(REACT, "Show at least two snippets per session.",
                    kind="content", source="user", created_by=ALICE,
                    check={"assert": "min_count", "block": "code", "min": 2})
db.approve_skill(sid3, ALICE)
one = doc_with(CODE_BLOCK)
check("one snippet is not enough",
      any("at least two snippets" in x for x in fails_for(one)),
      "; ".join(fails_for(one))[:200])
two = copy.deepcopy(one)
two["sections"][0]["slides"][1]["content"] = [
    {"type": "text", "text": "Another framing line."}, dict(CODE_BLOCK)]
check("…two are", not any("at least two snippets" in x for x in fails_for(two)),
      "; ".join(fails_for(two))[:200])
check("the failure says how many it found",
      any("1" in x and "at least two snippets" in x for x in fails_for(one)),
      "; ".join(fails_for(one))[:200])

print("\n== forbidden_phrase: a course that has moved on ==")
db.retire_skill(sid3, ALICE)
sid4 = db.add_skill(REACT, "This course is hooks-first: never teach class components.",
                    kind="content", source="user", created_by=ALICE,
                    check={"assert": "forbidden_phrase",
                           "phrases": ["class component", "componentDidMount"]})
db.approve_skill(sid4, ALICE)
bad = doc_with(CODE_BLOCK)
bad["sections"][0]["slides"][0]["content"][0]["text"] = \
    "A class component holds state in this.state."
f = fails_for(bad)
check("the banned phrase fails", any("hooks-first" in x for x in f), "; ".join(f)[:200])
check("…quoting where it was found",
      any("class component" in x for x in f), "; ".join(f)[:250])
check("a document that avoids it passes",
      not any("hooks-first" in x for x in fails_for(doc_with(CODE_BLOCK))),
      "; ".join(fails_for(doc_with(CODE_BLOCK)))[:200])
# The snippet itself is code, not slide text — a phrase ban is about what is TAUGHT.
incode = doc_with({"type": "code", "language": "jsx",
                   "code": "// componentDidMount is the old way",
                   "walkthrough": [{"lines": "1", "text": "A note."}]})
check("…and a phrase inside a code comment is not a teaching claim",
      not any("hooks-first" in x for x in fails_for(incode)),
      "; ".join(fails_for(incode))[:200])

print("\n== a skill with no check is left to the judge ==")
db.retire_skill(sid4, ALICE)
sid5 = db.add_skill(REACT, "Keep the tone conversational.", created_by=ALICE)
db.approve_skill(sid5, ALICE)
check("prose-only skills add no guardrail failure",
      not any("conversational" in x for x in fails_for(doc_with(CODE_BLOCK))),
      "; ".join(fails_for(doc_with(CODE_BLOCK)))[:200])
check("…but they still reach the writer",
      "Keep the tone conversational." in skills.block(REACT), skills.block(REACT)[:200])

print("\n== a course with no skills grades exactly as before ==")
before = guardrails.check(GOLDEN, cur, False, False).failures
after = guardrails.check(GOLDEN, cur, False, False, course="Untouched Course",
                         skills=skills.applicable("Untouched Course")).failures
check("identical failures", sorted(before) == sorted(after),
      str(sorted(set(before) ^ set(after))))

print("\n== the eval suite scores it as ONE dimension, for any course ==")
# A set per course, or per skill, would mean re-authoring the suite every time a course
# is created. One dimension, parameterised by the course's own skills.
from evals import run_sets                                         # noqa: E402
import json as _json
idx = _json.loads((ROOT / "evals/sets/index.json").read_text())
check("the set is registered", any(x["file"].startswith("26_") for x in idx["sets"]),
      str([x["file"] for x in idx["sets"]][-2:]))
check("…with a deterministic checker",
      "skill_adherence" in run_sets.DETERMINISTIC, str(sorted(run_sets.DETERMINISTIC)))
sset = _json.loads((ROOT / "evals/sets/26_skill_adherence.json").read_text())
check("…and it declares itself parameterised by the course",
      "course" in sset["description"].lower(), sset["description"][:120])


class _S:
    course = REACT
    number = 15


db.retire_skill(sid5, ALICE)
sid6 = db.add_skill(REACT, "Every worked example must show its code.",
                    created_by=ALICE,
                    check={"assert": "block_present", "block": "code",
                           "on_roles": ["working_example"]})
db.approve_skill(sid6, ALICE)
score, detail = run_sets._chk_skill_adherence(doc_with(CODE_BLOCK), _S(), sset)
check("a document that obeys scores 5", score == 5, f"{score}: {detail}")
score, detail = run_sets._chk_skill_adherence(doc_with(), _S(), sset)
check("…one that does not scores lower", score is not None and score < 4,
      f"{score}: {detail}")
check("…and the detail names the skill", "worked example must show" in detail,
      detail[:200])


class _NoSkills:
    course = "Untouched Course"
    number = 15


score, detail = run_sets._chk_skill_adherence(doc_with(), _NoSkills(), sset)
check("a course with no checkable skills is SKIPPED, not failed", score is None,
      f"{score}: {detail}")

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
