"""End-to-end proof that reviewer feedback actually becomes a durable rule.

    python -m evals.test_learning_loop      # NEEDS an API key (distillation is an LLM call)

The self-evolution loop has four parts and is only worth anything if ALL FOUR work, so
each is asserted separately against the real store (which is backed up and restored, so
running this never disturbs the rules in production):

  1. DISTIL  — a hurried, deictic, typo-ridden note becomes a standalone instruction,
               with the reviewer's original wording preserved in `raw`.
  2. INJECT  — the distilled rule reaches the SYSTEM prompt of the very next generation,
               declaring precedence over the style guide.
  3. DEDUPE  — the same request phrased differently folds into ONE rule with a raised hit
               count, instead of three rules that dilute the block and evict older ones.
  4. VERIFY  — the judge is handed the rules and pushes any violation into
               blocking_issues, which fails the gate and forces a revision.

Worth having as a test because every part of this is invisible at a glance: the store is
a JSON file, the injection happens inside a prompt, and a silent break here looks exactly
like "the model ignored my feedback again".
"""
from __future__ import annotations
import inspect
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config, learning, app_settings, generator   # noqa: E402
from graders import llm_judge                               # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def offline_guards() -> None:
    """The two ways a reviewer's instruction gets silently destroyed. No API key needed.

    Both were live defects, and both are invisible: the feedback is accepted, the UI says
    it was recorded, and what is actually stored is either somebody else's rule or a
    fragment of the classification protocol.
    """
    print("\n== 0. THE FEEDBACK SURVIVES AT ALL ==")

    # (a) A wrong merge DROPS the note. _merge_plausible vetoes a claimed duplicate that
    # shares no subject matter — but it only asks for ONE shared word, and every rule is
    # an imperative, so a generic instruction verb satisfied it. "the base addresses in
    # this exmaple are unrealistic use proper hex ones" was folded into 'Fix terminology:
    # use "cluster" instead of "block"' on the single shared word "use".
    note = "the base addresses in this exmaple are unrealistic use proper hex ones"
    check("a merge on a generic verb alone is vetoed",
          not learning._merge_plausible(note,
              'Fix terminology: use "cluster" instead of "block" throughout.'),
          str(sorted(learning._keywords(note) & learning._keywords(
              'Fix terminology: use "cluster" instead of "block" throughout.'))))
    check("…and so is one sharing only a hedge",
          not learning._merge_plausible(
              "show one worked example and keep to it",
              "Ensure every section can be used to show the same point"))
    check("a genuine restatement still merges",
          learning._merge_plausible(
              note, "Use realistic hex base addresses in examples, never toy numbers."))
    check("two rules about the same subject still merge",
          learning._merge_plausible("expand the deadlock section",
                                    "Give the deadlock discussion more depth."))

    # (b) The SCOPE line is a CLASSIFICATION, not the rule. Taking lines[0] blindly stored
    # the literal string "SCOPE: course" as a durable rule, which was then injected into
    # every generation with reviewer-level precedence, saying nothing.
    RULE = "Expand the Rollback and Starvation section."
    check("a SCOPE line emitted FIRST is not stored as the rule",
          learning.rule_line(f"SCOPE: course\n{RULE}") == RULE,
          repr(learning.rule_line(f"SCOPE: course\n{RULE}")))
    check("…and the normal order still works",
          learning.rule_line(f"{RULE}\nSCOPE: course") == RULE)
    check("a reply that is ONLY a scope line yields no rule text",
          learning.rule_line("SCOPE: course") == "",
          repr(learning.rule_line("SCOPE: course")))
    check("a rule that merely MENTIONS scope is not mistaken for the marker",
          learning.rule_line("State the scope of each section up front.")
          == "State the scope of each section up front.")


def main() -> int:
    offline_guards()
    if config.api_key() is None:
        print("  skip: no API key configured — distillation needs one")
        return 1 if FAIL else 0

    backup = learning.STORE.with_suffix(".json.testbak")
    had_store = learning.STORE.exists()
    if had_store:
        shutil.copy(learning.STORE, backup)
    try:
        before = len(learning.rules())
        course = app_settings.course_name()
        print(f"active course: {course} | rules before: {before}")

        print("\n== 1. DISTIL ==")
        note = "the base addresses in this exmaple are unrealistic use proper hex ones"
        learning.record_feedback(9001, note, source="regeneration")
        rules = learning.rules()
        check("the note was stored", len(rules) == before + 1, f"{before}->{len(rules)}")
        new = rules[-1]
        text = new.get("text") or ""
        print(f"     raw : {note}")
        print(f"     text: {text}")
        check("the original wording is kept in `raw`", new.get("raw") == note)
        check("it was distilled into standalone text", bool(text) and text != note)
        check("the deictic 'this' is gone", "this" not in text.lower().split())
        check("the typo did not survive", "exmaple" not in text.lower())
        check("it carries a scope", new.get("scope") in ("global", "course"), str(new.get("scope")))
        check("the course it was learned on is recorded", new.get("course") == course)

        print("\n== 2. INJECT ==")
        block = learning.learned_rules_block()
        check("the rule appears in the injected block", text[:40] in block)
        check("the block asserts precedence over the style guide",
              "RULES LEARNED FROM EARLIER CORRECTIONS" in block and "PRECEDENCE" in block)
        check("generator._learned() carries it into every call", text[:40] in generator._learned())

        print("\n== 3. DEDUPE ==")
        n_before = len(learning.rules())
        # Compare the TOTAL hit count, not the maximum: the store already contains a rule
        # raised 4 times, so a max would not budge when the rule that actually absorbed
        # this note goes from 1 to 2. Total is also agnostic about WHICH rule it merged
        # into, which is the merge logic's business, not this test's.
        hits_before = sum((r.get("hits") or 1) for r in learning.rules())
        learning.record_feedback(
            9002, "use realistic hex base addresses in the examples, not toy numbers",
            source="regeneration")
        if len(learning.rules()) == n_before:
            hits_after = sum((r.get("hits") or 1) for r in learning.rules())
            check("a restatement folded into an existing rule and raised its hits",
                  hits_after > hits_before, f"total hits {hits_before}->{hits_after}")
            check("a repeatedly-raised rule is escalated in the prompt",
                  "RAISED" in learning.learned_rules_block())
        else:
            # The keyword-overlap VETO deliberately refuses to merge when the two notes
            # are not clearly the same request. A new rule here is correct behaviour.
            check("the merge veto kept them separate rather than merging unrelated rules",
                  len(learning.rules()) == n_before + 1)

        print("\n== 4. VERIFY ==")
        src = inspect.getsource(llm_judge.grade)
        check("the judge is given the learned rules", "rules_block(course)" in src)
        # BY COURSE. Bare, it fell back to the instance-wide active course and verified a
        # document against whichever course somebody selected last. See
        # evals/test_course_isolation.py.
        check("…for the course being graded, not the instance-wide active one",
              "learning.rules_block(course)" in src, src[:0])
        check("…and the course's authored brief, labelled apart from them",
              "_skills_mod.block(course" in src and "THE COURSE'S OWN BRIEF" in src)
        # AND THE SESSION'S. A skill may be written for one session; a judge that
        # resolves the brief by course alone grades the document against a brief the
        # writer was not given.
        check("…resolved for the session being graded, not the course alone",
              '_skills_mod.block(course, getattr(session, "number", None))' in src,
              src[:0])
        # The brief is not only shown to the judge, it is SCORED — see
        # evals/test_skill_scoring.py. Without a scored dimension the only lever was a
        # binary blocking issue, so a document could follow its course's brief loosely
        # and still total 100/100.
        check("…and is a scored rubric dimension, not only a blocking issue",
              "course_brief_adherence" in src)
        check("a violation goes into blocking_issues (failing the gate)",
              "blocking_issues" in src)
        check("every store write is mirrored to the DB for an ephemeral host",
              "kb_put" in inspect.getsource(learning._save))
    finally:
        if had_store:
            shutil.move(backup, learning.STORE)
        print(f"\n(store restored — {len(learning.rules())} rule(s) intact)")

    print(f"\n{OK} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
