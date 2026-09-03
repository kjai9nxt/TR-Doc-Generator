"""THE DETERMINISTIC GATES, RUN PER CHUNK — and the final check left untouched.

    python -m evals.test_chunk_check      # no API key needed, ~3 seconds

WHY THIS EXISTS. Every deterministic gate ran in one place: `pipeline.finalize`, after
a human had approved all six chunks. A defect found there costs a bounded repair pass
that edits slides the reviewer already signed off, with the review panel gone; the same
defect found at chunk 2 costs one regenerate of one section.

So `guardrails.check` gained a `scope`, and `graders.chunk_check` wraps ONE section in a
synthetic single-takeaway document so the document-level gates can be asked about it.
That design has exactly two ways to be wrong, and this suite pins both:

  1. THE WRAPPER MUST NOT INVENT FAILURES. If the synthetic document trips gates on its
     own — a section that "isn't the whole agenda", slides that don't start at 1, a
     3-slide chunk against a 12-slide minimum — the reviewer gets a page of noise about
     a document this code built itself, learns to ignore the block, and the feature is
     worse than not having it.
  2. DOC SCOPE MUST BE UNCHANGED. The release gate is still finalize. `scope="chunk"`
     may only ever SUBTRACT from what is asked, and never from the default path.
     (evals/test_gates covers this gate by gate; asserted here too, on the golden.)

The four gates chunk scope drops all measure a proportion or a total over the whole
document, so a fragment cannot answer them: the slide floor/ceiling,
max_concept_intro_share, the worked-example rules, and slide numbering 1..N.
"""
import copy
import json
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import course_loader                    # noqa: E402
from guardrails import guardrails                # noqa: E402
from graders import chunk_check                  # noqa: E402

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
prev, cur, nxt = course_loader.neighbours(15, sessions)
GOLDEN = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())


def chunk(i: int) -> dict:
    """Section i of the golden, in the shape a guided chunk's fragment has."""
    return {"section": copy.deepcopy(GOLDEN["sections"][i])}


def fails_of(frag, idx, **kw):
    r = chunk_check.gates(frag, cur, idx, **kw)
    return list(r.failures) if r else None


print("\n== the wrapper does not invent failures ==")
# The whole feature rests on this: if the synthetic document trips gates on its own, the
# reviewer gets noise about a document this code built itself, learns to ignore the
# block, and the feature is worse than not having it.
#
# The categories below are the ones the WRAPPER constructs — the agenda, the key-takeaway
# list, the one-section-per-takeaway mapping, the slide totals and the numbering. Nothing
# in a section's own writing can produce them, so a hit here is this module's bug.
# (It caught one: the agenda item was passed unnumbered, so all four golden sections
# reported "1 agenda item(s) are not numbered".)
#
# Deliberately NOT in this list: the section-name gate. The golden PREDATES it — its
# sections are titled ("SCTP vs TCP vs UDP") rather than carrying the curriculum line
# verbatim — so that failure is real, and evals/test_gates already tracks it as one the
# golden legitimately trips. Filtering it here would hide the thing this feature is for.
WRAPPER_NOISE = ("slides (min", "slides (max", "concept_intro'", "are numbered",
                 "Agenda has", "was reworded", "agenda item(s) are not numbered",
                 "no slide works one through", "are worked examples",
                 "does not appear in any section")
for i in range(len(GOLDEN["sections"])):
    f = fails_of(chunk(i), i)
    bad = [x for x in (f or []) if any(k in x for k in WRAPPER_NOISE)]
    check(f"golden section {i + 1} trips no wrapper-shaped failure",
          f is not None and not bad, "; ".join(bad)[:200])

print("\n== …and it catches the golden's real defect at CHUNK 1, not at finalize ==")
# The golden's section names are titles, not the curriculum lines verbatim. Doc scope
# has always failed that. The point of this feature is that the reviewer now hears it
# while the section is still open and one regenerate away.
_doc_says = [x for x in guardrails.check(GOLDEN, cur, False, False).failures
             if "must carry key takeaway" in x]
_chunk_says = [x for x in fails_of(chunk(0), 0) if "must carry key takeaway" in x]
check("doc scope reports the section-naming defect", bool(_doc_says),
      "; ".join(_doc_says)[:120])
check("…and chunk scope reports it too, on the first chunk", bool(_chunk_says),
      "; ".join(_chunk_says)[:120])

print("\n== the four doc-wide gates are not asked of a fragment ==")
# A two-slide section is not a two-slide document.
tiny = {"section": {"name": GOLDEN["sections"][0]["name"],
                    "slides": copy.deepcopy(GOLDEN["sections"][0]["slides"][:2])}}
f = fails_of(tiny, 0)
check("a 2-slide chunk is not failed against the slide MINIMUM",
      not any("min " in x and "slides" in x for x in f), "; ".join(f)[:160])
check("…and doc scope still fails the same thing",
      any("slides (min" in x for x in guardrails.check(
          {**GOLDEN, "sections": [tiny["section"]]}, cur, False, False).failures))

# A section that introduces one concept is often 100% concept_intro and correct.
allintro = copy.deepcopy(chunk(0))
for sl in allintro["section"]["slides"]:
    sl["role"] = "concept_intro"
    sl.setdefault("analogy", "A shared notice board — one association carries many streams.")
check("an all-concept_intro chunk is not failed on the SHARE cap",
      not any("concept_intro'" in x for x in fails_of(allintro, 0)),
      "; ".join(fails_of(allintro, 0))[:160])

# A chunk numbers its slides after the chunks approved before it.
shifted = copy.deepcopy(chunk(1))
for k, sl in enumerate(shifted["section"]["slides"], start=8):
    sl["n"] = k
check("slides numbered from 8 are not failed against 1..N",
      not any("are numbered" in x for x in fails_of(shifted, 1)),
      "; ".join(fails_of(shifted, 1))[:160])

# "This session teaches an algorithm, so SOME slide must work one through" is answered
# by a different section than the one being checked.
check("a chunk is not asked to satisfy the session's worked-example rule",
      not any("works one through" in x or "are worked examples" in x
              for x in fails_of(chunk(0), 0)))

print("\n== …but every per-slide gate still fires ==")
bad_heading = copy.deepcopy(chunk(0))
bad_heading["section"]["slides"][0]["heading"] = \
    "A heading that is far too long to be a slide label at all"
check("an over-long heading FAILS in chunk scope",
      any("word" in x.lower() and "heading" in x.lower()
          for x in fails_of(bad_heading, 0)),
      "; ".join(fails_of(bad_heading, 0))[:200])

no_analogy = copy.deepcopy(chunk(0))
for sl in no_analogy["section"]["slides"]:
    if sl.get("role") == "concept_intro":
        sl.pop("analogy", None)
        break
check("a concept_intro with no analogy FAILS in chunk scope",
      any("analogy" in x.lower() for x in fails_of(no_analogy, 0)),
      "; ".join(fails_of(no_analogy, 0))[:200])

second_person = copy.deepcopy(chunk(0))
second_person["section"]["slides"][0]["content"] = [
    {"type": "text", "text": "You will now see how you can configure your association."}]
check("second-person voice FAILS in chunk scope",
      any("second person" in x.lower() or "you" in x.lower()
          for x in fails_of(second_person, 0)),
      "; ".join(fails_of(second_person, 0))[:200])

placeholder = copy.deepcopy(chunk(0))
placeholder["section"]["slides"][0]["content"] = [
    {"type": "text", "text": "Set the base to some address and the bound to any value."}]
check("a placeholder figure FAILS in chunk scope",
      any("placeholder" in x.lower() for x in fails_of(placeholder, 0)),
      "; ".join(fails_of(placeholder, 0))[:200])

renamed = copy.deepcopy(chunk(0))
renamed["section"]["name"] = "Something The Curriculum Never Said"
check("a section renamed off its takeaway FAILS in chunk scope",
      any("name" in x.lower() for x in fails_of(renamed, 0)),
      "; ".join(fails_of(renamed, 0))[:200])

print("\n== a chunk that cannot be checked reports so, and never 'clean' ==")
check("the opening chunk (no slides) returns None, not an empty pass",
      chunk_check.gates({"recap": "x", "agenda": ["1. a"]}, cur, 0) is None)
check("a takeaway index outside the curriculum returns None",
      chunk_check.gates(chunk(0), cur, 99) is None)

print("\n== the running length meter ==")
frags = [{"recap": GOLDEN.get("recap"), "agenda": GOLDEN.get("agenda") or []}]
frags += [{"section": copy.deepcopy(s)} for s in GOLDEN["sections"]]
half = frags[:1 + 2]
m = chunk_check.running_length(cur, nxt, half, budgets={})
check("it reports pages, minutes and slides after 2 of N sections",
      m.get("sections_done") == 2 and m.get("pages") and m.get("minutes")
      and m.get("slides"), str(m))
check("…and PROJECTS the finished document from what is spent",
      m.get("projected_pages") and m["projected_pages"] > m["pages"], str(m))
# sections_total is passed explicitly, as the server does from the guided run's own
# chunk count. It is NOT len(curriculum takeaways): the golden covers 7 takeaways in 4
# sections, so deriving the total from the curriculum would leave the finished document
# looking two-thirds written and project a length for sections that do not exist.
full = chunk_check.running_length(cur, nxt, frags, budgets={},
                                  sections_total=len(GOLDEN["sections"]))
check("the full set is reported as complete",
      full.get("sections_done") == len(GOLDEN["sections"])
      and full.get("projected_pages") is None,     # nothing left to project
      str(full))
check("…and the complete golden is inside both ceilings",
      not full.get("over_pages") and not full.get("over_time"), str(full))
check("a course budget override is honoured",
      chunk_check.running_length(cur, nxt, frags, budgets={"max_pages": 4},
                                 sections_total=len(GOLDEN["sections"]))
      ["over_pages"] is True)
check("no sections yet reads as nothing to measure",
      chunk_check.running_length(cur, nxt, frags[:1], budgets={}) == {})

print("\n== DOC SCOPE IS UNCHANGED ==")
# The release gate is still finalize. `scope` may only ever subtract, and never from
# the default path — so the default must be byte-identical to passing scope="doc".
a = guardrails.check(GOLDEN, cur, False, False)
b = guardrails.check(GOLDEN, cur, False, False, scope="doc")
check("the default scope IS doc scope",
      list(a.failures) == list(b.failures) and list(a.warnings) == list(b.warnings))
chunk_run = guardrails.check(GOLDEN, cur, False, False, scope="chunk")
check("chunk scope only ever subtracts, never adds",
      set(chunk_run.failures) <= set(a.failures),
      str(set(chunk_run.failures) - set(a.failures))[:200])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
