"""THE SLIDE BUDGET IS SHARED OUT BY WHAT EACH TAKEAWAY OWES, not by position.

    python -m evals.test_slide_plan        # no API key needed, ~2 seconds

WHAT THIS REPLACES. `chunk_slide_allowance` divided the ceiling equally among the
sections still to be written — `divmod(remaining, sections_left)` — which was blind
twice over:

  · IT COULD NOT SEE THE CONTRACT. A key takeaway is a promise: `Topic: a; b, c, d, e`
    owes five items and `Topic: a` owes one, and both got the same three slides. Length
    is supposed to be spent on coverage, and the allocator could not see coverage.
  · ORDER DECIDED THE BUDGET. The remainder went to whoever was drafted first — the old
    docstring's own example, "a 14-slide budget over 5 takeaways is 3,3,3,3,2" — so
    takeaway 5 paid for takeaway 1's appetite regardless of which had more to teach.

The two properties that must survive the change, and both are pinned below:

  1. THE PARTS SUM TO THE WHOLE. A budget whose parts cannot add up to the ceiling is
     the prompt-says-X-gate-says-not-X trap the deterministic gates exist to avoid.
  2. THE DOCUMENT STILL FITS. A section that ran long must squeeze the ones after it,
     down to the coverage floor and no further — being under budget is no saving if the
     document is then rejected for missing material.
"""
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, context_builder as CB, course_loader     # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
_p, CUR, _n = course_loader.neighbours(15, sessions)
CEIL = CB.slide_ceiling(True)
FLOOR = config.harness()["constraints"]["coverage"]["min_sub_concepts_per_takeaway"]


def sess(*takeaways):
    return replace(CUR, key_takeaways=list(takeaways))


print("\n== a takeaway's owed items are counted from its own contract ==")
check("items after the colon are counted",
      CB._owed_items("Number Systems: decimal, binary; octal, hex") == 4,
      str(CB._owed_items("Number Systems: decimal, binary; octal, hex")))
check("one item is one", CB._owed_items("SCTP: Stream Control Transmission Protocol") == 1)
# "Cannot tell" is not "one idea" — the padding gate makes the same distinction.
check("NO COLON weighs 1, so it cannot skew the split",
      CB._owed_items("QoS in Switched Networks") == 1)
# The splitter keeps "&" inside a sub-topic on purpose ("Bit & byte" really is one).
check("a coordinated pair is two ideas sharing a line",
      CB._owed_items("Disk Scheduling: LOOK & C-LOOK") == 2,
      str(CB._owed_items("Disk Scheduling: LOOK & C-LOOK")))
check("…and it agrees with the padding gate, which reads the same lines",
      CB._owed_items("Types: IntServ and DiffServ") == 2)

print("\n== the heavy takeaway gets the room, whatever its position ==")
# Real curriculum shapes. `takeaway_subtopics` drops single letters and stopwords, so a
# placeholder line like "Mechanisms: a, b, c" weighs nothing at all — which is correct
# (it names no sub-topic) and made the first version of this suite test uniform weights
# while claiming to test uneven ones.
light = "Introduction: Overview"
heavy = "QoS Parameters: Delay, Jitter, Bandwidth, Loss, Throughput, Latency"
first_heavy = CB.slide_plan(sess(heavy, light, light, light))
last_heavy = CB.slide_plan(sess(light, light, light, heavy))
check("a 6-item takeaway outranks a 1-item one", first_heavy[0] > first_heavy[1],
      str(first_heavy))
check("…and it gets the SAME share whether it is first or last",
      first_heavy[0] == last_heavy[3], f"{first_heavy} vs {last_heavy}")
check("…while the light ones are equal to each other",
      len(set(first_heavy[1:])) == 1, str(first_heavy))
check("…and the heavy one is materially bigger, not bigger by one",
      first_heavy[0] >= first_heavy[1] * 2, str(first_heavy))
# The specific unfairness the old allocator had: position decided the remainder.
check("position no longer decides anything",
      sorted(first_heavy) == sorted(last_heavy), f"{first_heavy} vs {last_heavy}")

print("\n== the parts sum to the whole ==")
for label, sn in [
    ("the real Session 15", CUR),
    ("all takeaways equal", sess(light, light, light)),
    ("one heavy, many light", sess(heavy, light, light, light, light)),
    ("nothing to weigh on", sess("A", "B", "C", "D")),
    ("a single takeaway", sess(heavy)),
    ("many takeaways", sess(*[light] * 12)),
]:
    plan = CB.slide_plan(sn)
    check(f"{label}: {plan} sums to the ceiling", sum(plan) == CEIL,
          f"{sum(plan)} vs {CEIL}")
    check(f"…and no section is below the floor", all(x >= 1 for x in plan), str(plan))

print("\n== drafting forward spends exactly the ceiling ==")
# The property the old equal division had, and the one the prompt depends on: an obedient
# model that takes its stated allowance every time lands exactly on the ceiling.
for label, sn in [("Session 15", CUR), ("one heavy", sess(heavy, light, light))]:
    n = len(sn.key_takeaways)
    used, allocs = 0, []
    for i in range(n):
        a = CB.chunk_slide_allowance(sn, slides_used=used, sections_left=n - i,
                                     takeaway_index=i)
        allocs.append(a)
        used += a
    check(f"{label}: {allocs} spends the ceiling exactly", used == CEIL, f"{used}")
    check(f"…and matches the plan", allocs == CB.slide_plan(sn), str(CB.slide_plan(sn)))

print("\n== a section that runs long squeezes the ones after it ==")
n = CUR.key_takeaways_count
plan = CB.slide_plan(CUR)
# A small overspend does NOT cut the next section, and should not: the plan still fits
# inside what is left, so squeezing early would give the budget back to nobody.
mild = CB.chunk_slide_allowance(CUR, slides_used=plan[0] + 2, sections_left=n - 1,
                                takeaway_index=1)
check("a small overspend does not cut the next section — the plan still fits",
      mild == plan[1], f"{mild} vs planned {plan[1]}")
# A large one must, or the ceiling is unreachable.
over = CB.chunk_slide_allowance(CUR, slides_used=CEIL - 6, sections_left=n - 1,
                                takeaway_index=1)
check("a large overspend does cut it", over < plan[1],
      f"{over} vs planned {plan[1]}")
check("an exhausted budget squeezes to the floor, not below",
      CB.chunk_slide_allowance(CUR, slides_used=CEIL + 5, sections_left=2,
                               takeaway_index=1) == FLOOR,
      str(CB.chunk_slide_allowance(CUR, slides_used=CEIL + 5, sections_left=2,
                                   takeaway_index=1)))
check("…and never returns 0, which would be a budget nothing can satisfy",
      CB.chunk_slide_allowance(CUR, slides_used=999, sections_left=4,
                               takeaway_index=2) >= 1)
# The floor the clamp uses must be the COVERAGE floor, not the smallest planned share —
# otherwise an overspent document has nowhere left to give and the ceiling is unreachable.
check("the clamp floor is the coverage floor, not min(plan)",
      FLOOR < min(plan), f"floor {FLOOR}, min plan {min(plan)}")

print("\n== the last section left gets the room its neighbours did not use ==")
# What server._slide_budget_state documents and depends on: re-drafting section 3 of 5
# leaves this the only section to place, so it may use whatever the ceiling still has.
check("the only section left may use the remaining room",
      CB.chunk_slide_allowance(CUR, slides_used=CEIL - 9, sections_left=1,
                               takeaway_index=2) == 9,
      str(CB.chunk_slide_allowance(CUR, slides_used=CEIL - 9, sections_left=1,
                                   takeaway_index=2)))

print("\n== the index is the takeaway's, not derived from what is left ==")
# On a re-draft every other section exists, so sections_left is 1. Deriving the index
# from it would hand section 3 the LAST takeaway's budget.
heavy_first = sess(heavy, light, light, light)
a_explicit = CB.chunk_slide_allowance(heavy_first, slides_used=4, sections_left=3,
                                      takeaway_index=0)
a_derived = CB.chunk_slide_allowance(heavy_first, slides_used=4, sections_left=3)
check("an explicit index gets that takeaway's share",
      a_explicit == CB.slide_plan(heavy_first)[0], f"{a_explicit}")
check("…and it differs from what the derived index would give",
      a_explicit != a_derived, f"explicit={a_explicit} derived={a_derived}")

print("\n== the floor drops when the ceiling cannot seat everyone ==")
many = sess(*[light] * (CEIL // FLOOR + 3))
plan_many = CB.slide_plan(many)
check("a session with more takeaways than budget/floor still sums to the ceiling",
      sum(plan_many) == CEIL, f"{sum(plan_many)} vs {CEIL}")
check("…by dropping the floor to 1 rather than promising what cannot fit",
      min(plan_many) >= 1 and CB.section_floor(len(many.key_takeaways), CEIL) == 1,
      str(plan_many))
crowded = sess(*[light] * (CEIL + 4))
check("more takeaways than SLIDES shares out what there is, without going negative",
      all(x >= 0 for x in CB.slide_plan(crowded))
      and sum(CB.slide_plan(crowded)) == CEIL, str(CB.slide_plan(crowded)))

print("\n== budgets and depth mode are honoured ==")
small = CB.slide_plan(CUR, budgets={"max_slides": 10})
check("a course's own slide ceiling is what gets shared out", sum(small) == 10, str(small))
check("…and the weighting still applies inside it",
      small[3] >= small[0], str(small))
check("an empty curriculum plans nothing rather than dividing by zero",
      CB.slide_plan(sess()) == [])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
