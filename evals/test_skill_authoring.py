"""WHAT REACHES THE PROMPT WHEN A PERSON WRITES A SKILL — and how it is scored.

    python -m evals.test_skill_authoring       # no API key, no network, ~2 seconds

WHY THIS EXISTS. There are two doors into the same store and they were not the same
door. "From my requirements" sent the author's notes to the model, which articulated each
into a standing instruction and quoted the words it came from. "Write one" stored what
was typed, verbatim, and injected it verbatim into every generation for that course. The
live store shows both, from the same author, minutes apart:

    (drafted)  "Keep code snippets small and focused on a single concept; do not extend
                snippets beyond what is necessary to demonstrate the idea being taught."
    (written)  "Explain the code, the student should be able to wrtite the code on their
                own after that for the concpet for any given problem reltated to it"

The second is a note to oneself being handed to a model as policy, with precedence over
the style guide. Both paths articulate now, and both keep the author's own words beside
the result so approving it means something.

Also covered: the eval runner's skill_adherence set, which returned None for a course
with no machine-checkable skills — every course in the live store — and took the whole
run down with a TypeError on `None >= 4` before any set had reported.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skill_authoring_")
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


from src import db, skills                                             # noqa: E402

COURSE = "Responsive"
ALICE = "alice@nxtwave.co.in"
db.init()

TYPED = ("Explain the code, the student should be able to wrtite the code on their own "
         "after that for the concpet")
WRITTEN_UP = ("Explain every code snippet line by line, to the point where a learner "
              "could write comparable code for a new problem unaided.")

print("\n== a hand-written skill is written up, and keeps the author's words ==")
drafted = skills.articulate(TYPED, model=lambda p: {"text": WRITTEN_UP, "kind": "style"})
check("it comes back articulated", drafted["text"] == WRITTEN_UP, str(drafted))
check("…not the author's raw sentence", drafted["text"] != TYPED)
check("the author's own words are kept, verbatim and whole",
      drafted["source_quote"] == TYPED, str(drafted.get("source_quote")))
check("…so the approval is of a rewrite that can be checked against them",
      drafted["source_quotes"] == [TYPED])
check("the kind it governs comes back too", drafted["kind"] == "style")

print("\n== the author's note is shown their rule, not their typos ==")
prompt = {}
skills.articulate(TYPED, model=lambda p: prompt.setdefault("p", p) and None or {"text": "x"})
p = prompt["p"]
check("the model is told to fix the English", "EDITING THEIR ENGLISH" in p)
check("…and told not to invent", "DO NOT INVENT" in p)
# THE OTHER HALF, and the one that shipped broken: the prompt asked for "one or two
# full sentences" and told the model not to echo the author's phrasing, so a note
# carrying three worked examples came back as one sentence carrying none — and the
# author was shown that to approve. Not summarising is as much of a contract as not
# inventing. See evals/test_skill_system.py for the check that enforces it on output.
check("…and told not to summarise", "LOSE NOTHING" in p)
check("…with no length cap to compress them into",
      "one or two full sentences" not in p and "NO LENGTH LIMIT" in p)
check("…and is given exactly what the author typed", TYPED in p)

print("\n== the model being down never loses what the author wrote ==")
check("an unreachable model articulates to nothing",
      skills.articulate(TYPED, model=lambda p: (_ for _ in ()).throw(RuntimeError("502")))
      is None)
check("…as does an answer with no text in it",
      skills.articulate(TYPED, model=lambda p: {"kind": "style"}) is None)
check("…and empty input is not sent anywhere at all",
      skills.articulate("   ", model=lambda p: 1 / 0) is None)
# The endpoint's contract on that None: store the author's own words rather than nothing.
sid = db.add_skill(COURSE, TYPED, kind="style", source="user", created_by=ALICE)
check("the raw text is storable as the fallback", bool(sid))

print("\n== a written-up skill reaches the prompt as part of the brief ==")
sid2 = db.add_skill(COURSE, WRITTEN_UP, kind="content", source="user",
                    created_by=ALICE, source_quote=TYPED, source_quotes=[TYPED])
db.approve_skill(sid2, ALICE)
brief = skills.block(COURSE)
check("the articulated rule is in the brief", WRITTEN_UP in brief)
check("…grouped under what it governs", "WHAT THIS COURSE MUST CONTAIN" in brief)
check("a DRAFT is not — nothing takes effect before approval",
      TYPED not in brief, brief)
check("the quote is stored for the UI to show",
      any(s["id"] == sid2 and (s.get("source_quotes") or [None])[0] == TYPED
          for s in db.skills(COURSE)),
      str([s.get("source_quotes") for s in db.skills(COURSE)]))

print("\n== editing a written-up skill sends it back to draft ==")
db.edit_skill(sid2, "Explain every snippet line by line.")
check("it is a draft again",
      next(s["status"] for s in db.skills(COURSE) if s["id"] == sid2) == "draft")
check("…and out of the brief until re-approved",
      "Explain every snippet line by line." not in skills.block(COURSE))

print("\n== the eval runner survives a course with no checkable skills ==")
from evals import run_sets                                             # noqa: E402


class _Session:
    number = 4
    name = "Flexbox"
    key_takeaways = ["Flex containers"]
    course = COURSE


doc = {"sections": [{"slides": [{"n": 1, "role": "concept_intro",
                                 "content": [{"type": "text", "text": "Flex."}]}]}]}
# This course's only skills at this point are DRAFTS, so it has no approved brief at
# all — not applicable, rather than a number. (A course with an APPROVED prose brief
# abstains from the machine half instead and is scored by the judge; see
# evals/test_skill_scoring.py.)
score, detail = run_sets._chk_skill_adherence(doc, _Session(), {"id": "skill_adherence"})
check("the set answers 'not applicable' rather than a number",
      score is run_sets.NOT_APPLICABLE, str(score))
report = run_sets.run_on_doc(doc, _Session(), use_llm=False, learn=False)
row = next(r for r in report["sets"] if r["id"] == "skill_adherence")
check("run_on_doc records it as a SKIP", row.get("skipped") is True, str(row))
check("…with the reason on it",
      "has not said what it requires" in row.get("reason", ""), str(row))
check("…and the run completes instead of raising", report["scored"] > 5,
      str(report["scored"]))
check("…scoring every other set", all("score" in r for r in report["sets"]
                                      if not r.get("skipped")))

print("\n== a checkable skill IS scored, and the gate and the eval agree ==")
sid3 = db.add_skill(COURSE, "Every worked example shows the code it walks through.",
                    kind="content", source="user", created_by=ALICE,
                    check={"assert": "block_present", "block": "code",
                           "on_roles": ["working_example"]})
db.approve_skill(sid3, ALICE)
bad = {"sections": [{"slides": [{"n": 2, "role": "working_example",
                                 "content": [{"type": "text", "text": "Imagine it."}]}]}]}
score, detail = run_sets._chk_skill_adherence(bad, _Session(), {"id": "skill_adherence"})
check("a violated checkable skill scores below the bar", score is not None and score < 4,
      f"{score}: {detail}")
good = {"sections": [{"slides": [{"n": 2, "role": "working_example", "content": [
    {"type": "code", "language": "css", "code": "display:flex"}]}]}]}
score, _ = run_sets._chk_skill_adherence(good, _Session(), {"id": "skill_adherence"})
check("a satisfied one scores full marks", score == 5, str(score))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
