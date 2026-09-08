"""WHAT THE LEARNED-RULE STORE KEEPS, INJECTS, AND OFFERS.

    python -m evals.test_learning_hygiene      # no API key needed, ~2 seconds

Three defects in how reviewer feedback was remembered, and the ways each fix can go
wrong. Everything here is deterministic — the one judgement that needs a model
(`is_one_time_note`) is stubbed, because what is being tested is the plumbing around it,
not the model's taste. `evals/test_learning_loop` covers the live call.

1. THE CAP WAS SHARED BY EVERY COURSE. One list, one limit of 40, trimmed by hit count.
   So a course under active work generated rules that evicted a DORMANT course's, and
   nothing said so: you would find out months later, regenerating an old session, when a
   mistake that had been fixed came back. The note that prevented it had been pushed out
   by a course you were not even working on. Records — rules a gate took over, or that
   became skills — competed for the same slots, so the store's own history could evict a
   live instruction.

2. EVERY NOTE BECAME A STANDING INSTRUCTION. Including "drop this analogy, it does not
   fit here", which is about one slide. Thirty of those drown the ten that matter, and
   this store already knows the cost: the note above `_gate_specs` calls a rule left in
   the injected block after a gate took it over "worse than redundant". A one-off is now
   stored, marked and injected nowhere — stored, because a misclassification that
   discards the reviewer's words is unrecoverable and silent.

3. THE STORE COUNTED REPEATS AND NEVER SAID SO. `hits` has always been kept and shown as
   a small "x3" badge, so the action it implies — make it a skill, where it gets a
   per-document verdict and a repair, which a rule never gets — depended on someone
   opening that screen and reading the badge.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_learn_hygiene_")
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


from src import learning                                  # noqa: E402

A, B = "Course A", "Course B"


def reset():
    learning._save({"rules": []})


def put(text, *, course=None, scope=None, hits=1, one_off=False, extra=None):
    """A rule straight into the store, so a cap can be exercised without 40 LLM calls."""
    data = learning._load()
    r = {"text": text, "source": "test", "hits": hits,
         "scope": scope or (learning.COURSE if course else learning.GLOBAL),
         "course": course}
    if one_off:
        r["one_off"] = True
    r.update(extra or {})
    data["rules"].append(r)
    learning._save(data)


def texts(rs):
    return [r.get("text") for r in rs]


print("\n== the cap is PER BUCKET, not one limit shared by every course ==")
check("the two caps default to half the legacy total each, so injection is unchanged",
      learning._caps() == (20, 20), str(learning._caps()))
check("…because injection is global + ONE course, which is what the cap is for",
      sum(learning._caps()) == learning._cap(),
      f"{learning._caps()} vs legacy {learning._cap()}")

reset()
g_cap, c_cap = learning._caps()
# Course A fills its bucket. Course B has ONE rule, reinforced, that the old shared cap
# would have evicted the moment A got busy — it is the least-reinforced rule overall
# only because A's are newer, not because anyone stopped asking for it.
put("B's hard-won rule", course=B, hits=4)
for i in range(c_cap):
    put(f"A rule {i}", course=A, hits=1)
learning.add_rule("one more from A", source="test", scope=learning.COURSE, course=A)
kept = texts(learning.rules())
check("a busy course cannot evict a dormant course's rule",
      "B's hard-won rule" in kept, str(kept[:3]))
check("…it evicts one of its OWN instead",
      sum(1 for t in kept if t.startswith("A rule")) == c_cap - 1,
      str(sum(1 for t in kept if t.startswith("A rule"))))
check("…and the course that overflowed stays at its cap",
      sum(1 for r in learning.rules()
          if (r.get("course") or "") == A) == c_cap, str(len(kept)))

reset()
put("house style that keeps being asked for", hits=9)
for i in range(c_cap + 3):
    put(f"A rule {i}", course=A, hits=1)
learning.add_rule("newest from A", source="test", scope=learning.COURSE, course=A)
check("a course overflowing cannot evict HOUSE STYLE either",
      "house style that keeps being asked for" in texts(learning.rules()))

print("\n== within a bucket, the least-reinforced still goes first ==")
reset()
for i in range(c_cap):
    put(f"A rule {i}", course=A, hits=(5 if i == 0 else 1))
learning.add_rule("newest", source="test", scope=learning.COURSE, course=A)
check("a rule the reviewer insisted on survives a one-off nitpick",
      "A rule 0" in texts(learning.rules()))

print("\n== records do not compete with instructions ==")
# A gated or promoted rule is not injected, so it costs no prompt space. Counting it
# against the cap would let the store's own history evict a live instruction.
reset()
for i in range(c_cap):
    put(f"live {i}", course=A, hits=1)
put("already a skill", course=A, hits=1, extra={"promoted_to_skill": 7})
learning.add_rule("newest live", source="test", scope=learning.COURSE, course=A)
_kept = texts(learning.rules())
check("a promoted rule is kept as a record whatever the cap says",
      "already a skill" in _kept)
check("…and did not cost a live rule its place",
      sum(1 for t in _kept if t.startswith("live")) == c_cap - 1,
      str(sum(1 for t in _kept if t.startswith("live"))))

print("\n== ORDER is preserved, because an index is an address ==")
# reinforce, promote_to_skill, set_learned_rule_scope and every /api/learned-rules/{index}
# endpoint address rules POSITIONALLY. Reordering survivors would make a stale index in
# an open browser tab promote or delete the wrong rule, permanently.
reset()
for i in range(c_cap + 2):
    put(f"r{i}", course=A, hits=(9 if i in (0, 3) else 1))
_before = [t for t in texts(learning.rules())]
learning.add_rule("last", source="test", scope=learning.COURSE, course=A)
_after = texts(learning.rules())
check("survivors keep their original relative order",
      _after[:-1] == [t for t in _before if t in set(_after)],
      f"{_after[:5]} vs {_before[:5]}")

print("\n== a ONE-OFF is stored, marked, and injected nowhere ==")
reset()
put("a real standing preference", course=A)
put("State correct RFC numbers", course=A, one_off=True)
check("it is in the store, so the reviewer's words are never lost",
      "State correct RFC numbers" in texts(learning.rules()))
check("…but not in what the writer is given",
      "State correct RFC numbers" not in texts(learning.applicable_rules(A)),
      str(texts(learning.applicable_rules(A))))
check("…and the standing rule beside it still is",
      "a real standing preference" in texts(learning.applicable_rules(A)))
check("…nor in the injected block",
      "State correct RFC numbers" not in learning.learned_rules_block(A),
      learning.learned_rules_block(A)[:120])
check("the predicate says which is which",
      learning.is_one_off({"one_off": True}) and not learning.is_one_off({}))

print("\n== a one-off costs nothing at the cap either ==")
reset()
for i in range(c_cap):
    put(f"live {i}", course=A, hits=1)
put("one-off note", course=A, one_off=True)
learning.add_rule("newest live", source="test", scope=learning.COURSE, course=A)
check("a one-off is kept as a record, like any other non-injected rule",
      "one-off note" in texts(learning.rules()))

print("\n== ASKING TWICE OVERRIDES THE GUESS ==")
# The classification is made from a single sentence, which cannot tell whether a note
# will come back. Recurrence is not a guess, so it wins — and the classifier's mistake
# corrects itself instead of needing to be noticed.
reset()
put("Ensure analogies are relevant", course=A, one_off=True)
_i = next(i for i, r in enumerate(learning.rules()) if r.get("one_off"))
learning.reinforce(_i, 12)
_r = learning.rules()[_i]
check("a second asking clears the one-off mark", not learning.is_one_off(_r), str(_r))
check("…and raises the hit count as it always did", _r.get("hits") == 2, str(_r.get("hits")))
check("…so it is injected from then on",
      "Ensure analogies are relevant" in texts(learning.applicable_rules(A)))

print("\n== and the reviewer can correct it directly ==")
reset()
put("Keep speaker notes to two sentences", course=A, one_off=True)
_i = next(i for i, r in enumerate(learning.rules()) if r.get("one_off"))
_ok, _why = learning.keep_standing(_i)
check("keep_standing clears the mark", _ok and not learning.is_one_off(learning.rules()[_i]))
check("…and it is applied from then on",
      "Keep speaker notes to two sentences" in texts(learning.applicable_rules(A)))
check("doing it twice says so rather than pretending to work",
      learning.keep_standing(_i) == (False, "that rule is already a standing instruction"),
      str(learning.keep_standing(_i)))
check("a rule that does not exist is refused",
      learning.keep_standing(999)[1] == "no such rule")

print("\n== the classifier is only asked about a NEW rule, and fails safe ==")
reset()
_calls = []
_real = learning.is_one_time_note
learning.is_one_time_note = lambda t, r: (_calls.append(t), True)[1]
try:
    learning.record_feedback(1, "the base addresses here are unrealistic", course=A)
    check("a new rule is classified", len(_calls) == 1, str(_calls))
    check("…and stored as a one-off when it says so",
          any(r.get("one_off") for r in learning.rules()), str(learning.rules()))
    _n = len(_calls)
    # A repeat must never be re-classified: a request that comes back is not a one-off,
    # whatever it looked like the first time.
    _idx = 0
    learning.reinforce(_idx, 2)
    check("a reinforced rule is not re-classified", len(_calls) == _n, str(_calls))
finally:
    learning.is_one_time_note = _real
check("on any model error the note stays STANDING, which is the old behaviour",
      learning.is_one_time_note("", "") is False)

print("\n== promotion is OFFERED once a rule has been asked for enough ==")
check("the threshold comes from the harness", learning.promote_suggest_at() == 3,
      str(learning.promote_suggest_at()))
check("below it, nothing is suggested",
      not learning.suggests_promotion({"hits": 2, "course": A, "text": "x"}))
check("at it, promotion is suggested",
      learning.suggests_promotion({"hits": 3, "course": A, "text": "x"}))
check("above it too", learning.suggests_promotion({"hits": 9, "course": A, "text": "x"}))
# Only offered when accepting it would actually do something.
check("not for a rule with no course — promote_to_skill would refuse it",
      not learning.suggests_promotion({"hits": 9, "text": "x"}))
check("not for a rule that is already a skill",
      not learning.suggests_promotion({"hits": 9, "course": A, "text": "x",
                                       "promoted_to_skill": 3}))
check("not for a one-off, which is not being applied in the first place",
      not learning.suggests_promotion({"hits": 9, "course": A, "text": "x",
                                       "one_off": True}))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
