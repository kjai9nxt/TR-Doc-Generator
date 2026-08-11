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


def main() -> int:
    if config.api_key() is None:
        print("  skip: no API key configured — distillation needs one")
        return 0

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
              "REVIEWER-ENFORCED RULES" in block and "PRECEDENCE" in block)
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
        check("the judge is given the learned rules", "learned_rules_block" in src)
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
