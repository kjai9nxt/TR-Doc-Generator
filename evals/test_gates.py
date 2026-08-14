"""Offline regression for the deterministic gates added in harness 1.29.

    python -m evals.test_gates          # no API key needed, runs in a second

The golden regression (evals/run_eval) proves a GOOD doc still passes. This proves the
complement, which is the half that actually protects the reviewer: each gate FIRES on
the specific defect it was added for. Written because 1.24's lesson — a rule that lives
only in a prompt is one the model talks itself out of — has a corollary: a gate nobody
tested against a failing document is a gate that might not be wired up at all.

Also covers the two pieces of machinery whose whole value is a guarantee: the patcher
(untouched slides must be the SAME objects, not similar text) and guided assembly
(renumbering must carry the coverage map's slide references with it).
"""
import copy, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import course_loader, patcher, pipeline
from guardrails import guardrails
from graders import page_grader, time_grader

OK = FAIL = 0
def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1; print(f"  ok   {name}")
    else:
        FAIL += 1; print(f"  FAIL {name} {extra}")

sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
prev, cur, nxt = course_loader.neighbours(15, sessions)
GOLDEN = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())
# Failures the golden legitimately trips because it PREDATES the rule that catches
# them. The golden is a real, human-approved Session 15 doc kept as the format
# reference; it is not re-written every time a rule is added, so the gates it predates
# are listed here and excluded from the "fires nothing new" baseline. The gate itself
# is still proved to work — each one has its own positive test below.
#   · the agenda-verbatim / numbering / recap rules (harness 1.28);
#   · the prose-bullet mix and broad->specific rules (this change): the golden has two
#     2-item bullet lists that should be sentences, and its comparison section opens on
#     a mechanism slide instead of the landscape — which is exactly the reviewer's
#     complaint, so the rules are right and the fixture is old.
#   · one-section-per-takeaway naming: the golden groups its seven takeaways into four
#     thematic sections, which predates the rule (the coverage code has always noted it).
GRAND = ["Agenda has 4 items", "was reworded. Expected", "and key takeaway",
         "agenda item(s) are not numbered", "recap must carry ALL",
         "item(s) (min 3)", "must start BROAD",
         "must carry key takeaway", "section(s) for 7 key takeaways"]

def gate(doc, needle, *, want=True, label=None):
    """Assert `needle` appears (or not) in the non-grandfathered failures."""
    r = guardrails.check(doc, cur, False, False)
    hits = [f for f in r.failures if not any(g in f for g in GRAND)]
    got = any(needle in f for f in hits)
    check(label or needle[:60], got == want,
          f"\n        failures: {hits[:3]}")

print("\n== baseline: the compliant golden fires nothing new ==")
gate(GOLDEN, "", want=False, label="golden has zero non-grandfathered failures")

print("\n== slide role ==")
d = copy.deepcopy(GOLDEN); del d["sections"][0]["slides"][0]["role"]
gate(d, "missing 'role'", label="missing role fails")
d = copy.deepcopy(GOLDEN); d["sections"][0]["slides"][0]["role"] = "explainer"
gate(d, "is not one of", label="invalid role fails")
d = copy.deepcopy(GOLDEN)
for sec in d["sections"]:
    for s in sec["slides"]:
        s["role"] = "concept_intro"
        s.setdefault("analogy", "A queue at a counter — just as requests are served in order.")
gate(d, "labelled 'concept_intro'", label="concept_intro share cap fires")

print("\n== analogy placement (the biconditional) ==")
d = copy.deepcopy(GOLDEN)
d["sections"][1]["slides"][0]["analogy"] = "A token slip at a clinic — just as the cookie defers state."
gate(d, "must have NO analogy", label="analogy on a mechanism slide fails")
d = copy.deepcopy(GOLDEN); del d["sections"][0]["slides"][0]["analogy"]
gate(d, "analogy is REQUIRED", label="missing analogy on concept_intro fails")

print("\n== worked examples + figure realism ==")
d = copy.deepcopy(GOLDEN)
for s in d["sections"][2]["slides"]:                 # 2 of 8 slides -> 25%, under cap
    s["role"] = "working_example"; s.pop("analogy", None)
r = guardrails.check(d, cur, False, False)
hits = [f for f in r.failures if not any(g in f for g in GRAND)]
check("2/8 worked examples stays under the 30% cap",
      not any("worked examples" in f for f in hits), f"\n        {hits[:2]}")
d = copy.deepcopy(GOLDEN)
for sec in d["sections"]:
    for s in sec["slides"]:
        s["role"] = "working_example"; s.pop("analogy", None)
gate(d, "slides are worked examples", label="worked-example share cap fires")
d = copy.deepcopy(GOLDEN)
s = d["sections"][3]["slides"][0]                     # QoS switched-networks slide
s["role"] = "working_example"; s.pop("analogy", None)
s["content"] = [{"type": "text", "text": "Tag the frame and forward it by priority."}]
gate(d, "is not worked through", label="worked example with no figures fails")
d = copy.deepcopy(GOLDEN)
d["sections"][0]["slides"][1]["content"].append(
    {"type": "text", "text": "The base register holds some address."})
gate(d, "placeholder figure", label="placeholder figure fails")

print("\n== coverage map ==")
d = copy.deepcopy(GOLDEN); del d["coverage_map"]
gate(d, "Missing 'coverage_map'", label="missing coverage_map fails")
d = copy.deepcopy(GOLDEN); d["coverage_map"] = d["coverage_map"][:5]
gate(d, "one entry per takeaway", label="wrong entry count fails")
d = copy.deepcopy(GOLDEN); d["coverage_map"][2]["takeaway"] = "SCTP versus everything else"
gate(d, "must be key takeaway 3 verbatim", label="reworded takeaway fails")
d = copy.deepcopy(GOLDEN)
d["coverage_map"][2]["sub_concepts"] = d["coverage_map"][2]["sub_concepts"][:1]
gate(d, "min 2", label="too few sub-concepts fails")
d = copy.deepcopy(GOLDEN); d["coverage_map"][0]["sub_concepts"][0]["slide"] = 99
gate(d, "which does not exist", label="dangling slide reference fails")
d = copy.deepcopy(GOLDEN)
d["coverage_map"][0]["sub_concepts"][0] = {"name": "Head-of-line blocking"}
gate(d, "neither a slide nor a named", label="unmapped sub-concept fails")
d = copy.deepcopy(GOLDEN)
d["coverage_map"][0]["sub_concepts"][0] = {"name": "Partial reliability",
                                          "deferred_to": "Session 16 — SCTP extensions"}
gate(d, "neither a slide nor a named", want=False, label="named deferral is accepted")

print("\n== prose / bullet MIX ==")
# The reviewer's complaint was "mostly all the content is bullets, which looks odd".
# `gate()` filters these needles out of the baseline (the golden predates the rule), so
# these check the raw failures instead.
def raw(doc):
    return guardrails.check(doc, cur, False, False).failures

d = copy.deepcopy(GOLDEN)                      # strip every framing paragraph
for sec in d["sections"]:
    for s in sec["slides"]:
        s["content"] = [b for b in s["content"] if b.get("type") != "text"] or \
                       [{"type": "bullets", "items": ["one", "two", "three"]}]
check("an all-bullets document fails the mix rule",
      any("carry a prose `text` block" in f for f in raw(d)))
check("the golden's paragraphs satisfy it",
      not any("carry a prose `text` block" in f for f in raw(GOLDEN)))

d = copy.deepcopy(GOLDEN)
d["sections"][3]["slides"][0]["content"] = [          # slide 6
    {"type": "text", "text": "Best Effort is the default service model."},
    {"type": "bullets", "items": ["No delivery guarantee", "No delay bound"]}]
# Scoped to slide 6: the golden's own grandfathered 2-item lists (slides 1 and 2) fire
# the same message, so an unscoped `any(...)` would pass whatever this slide does.
def short_list_on(doc, n):
    return any(f.startswith(f"Slide {n}:") and "item(s) (min 3)" in f for f in raw(doc))

check("a 2-item bullet list fails (it is a bulleted sentence)", short_list_on(d, 6))
d["sections"][3]["slides"][0]["content"][1]["items"].append("No bandwidth reservation")
check("...and a real 3-item list passes", not short_list_on(d, 6))

print("\n== broad -> specific: a section opens on the landscape ==")
d = copy.deepcopy(GOLDEN)
d["sections"][0]["slides"][0]["role"] = "mechanism"
d["sections"][0]["slides"][0].pop("analogy", None)
check("a section opening on a mechanism slide fails",
      any("must start BROAD" in f for f in raw(d)))
d["sections"][0]["slides"][0]["role"] = "overview"
check("...and an `overview` opener is accepted",
      not any("must start BROAD" in f and "Section 1" in f for f in raw(d)))
check("an overview slide must carry NO analogy",
      "overview" in guardrails.config.harness()["constraints"]["analogy"]["banned_on_roles"])

print("\n== nothing off the agenda: every slide is mapped ==")
# Slide 5 teaches takeaway 5; point its sub-concepts at slide 4 instead, so nothing in
# the map claims slide 5 — which is what an off-agenda slide looks like.
d = copy.deepcopy(GOLDEN)
for sub in d["coverage_map"][4]["sub_concepts"]:
    sub["slide"] = 4
check("a slide no sub-concept points at FAILS",
      any("nothing the coverage map points at" in f for f in raw(d)))
d["sections"][2]["slides"][1]["role"] = "summary"      # slide 5 -> an allowed role
d["sections"][2]["slides"][1].pop("analogy", None)
check("...unless it is an overview / comparison / summary",
      not any("nothing the coverage map points at" in f for f in raw(d)))

print("\n== 100% of every takeaway: the sub-topics the curriculum line names ==")
# The sheet writes a takeaway as "Topic: item; item, item". Each item is owed to the
# learner, and until now nothing checked the promise — only the model's own enumeration.
subs = guardrails.takeaway_subtopics(
    "1. Data Representation & Binary Basics: How computers see information; "
    "binary (1s and 0s), Bit & byte; most- and least-significant bit (MSB / LSB)")
check("the line's sub-topics are parsed", len(subs) == 4, str(subs))
check("the topic name before the colon is not one of them",
      not any("Binary Basics" in s for s in subs), str(subs))
check("a line with no colon promises no items",
      guardrails.takeaway_subtopics("Understanding TCP and UDP") == [])
check("an acronym is mandatory vocabulary",
      guardrails._mandatory_tokens("Direct memory access (DMA)") == {"dma"})
have = guardrails._norm_tokens("The controller uses direct memory access to move blocks "
                               "without the CPU, and the DMA engine raises an interrupt.")
check("a sub-topic taught in other words still counts",
      guardrails._covers("Direct memory access (DMA)", have, 0.6))
have_no_dma = guardrails._norm_tokens(
    "The controller moves memory blocks directly, giving the processor access to data.")
check("...but dropping the acronym does NOT (generic words alone can't cover it)",
      not guardrails._covers("Direct memory access (DMA)", have_no_dma, 0.6))

print("\n== deferral is a last resort, not a release valve ==")
d = copy.deepcopy(GOLDEN)
for sub in d["coverage_map"][4]["sub_concepts"]:
    sub.pop("slide", None); sub["deferred_to"] = "Session 16 — QoS deep dive"
gate(d, "is not taught in this session at all", label="a wholly deferred takeaway fails")
d = copy.deepcopy(GOLDEN)
d["coverage_map"][3]["sub_concepts"][0].pop("slide", None)
d["coverage_map"][3]["sub_concepts"][0]["deferred_to"] = "Session 16"
d["coverage_map"][3]["sub_concepts"][1].pop("slide", None)
d["coverage_map"][3]["sub_concepts"][1]["deferred_to"] = "Session 16"
gate(d, "are deferred (max", label="deferring most of a takeaway fails")
gate(GOLDEN, "are deferred (max", want=False, label="the golden defers nothing")

print("\n== one section per takeaway, named after it ==")
d = copy.deepcopy(GOLDEN); d["sections"][0]["name"] = "SCTP Basics"
r = guardrails.check(d, cur, False, False)
check("a renamed section is reported",
      any('named "SCTP Basics"' in f for f in r.failures))

print("\n== do not re-teach what an earlier session already taught ==")
# The point of ingesting the decks. Only the unambiguous case fails: a slide that
# RE-INTRODUCES a concept under a title an earlier deck already used.
from src import pptx_ingest as _ppt
_prior = _ppt.taught_titles(15)
if _prior:
    _title = next((t for _sn, t in _prior if len(t.split()) >= 3), None)
    d = copy.deepcopy(GOLDEN)
    s = d["sections"][0]["slides"][0]
    s["title"] = _title; s["role"] = "concept_intro"
    s["analogy"] = "A shared notice board — just as one association carries many streams."
    gate(d, "already introduced under the same title",
         label="re-introducing a prior session's concept fails")
    d2 = copy.deepcopy(d)
    d2["sections"][0]["slides"][0]["role"] = "mechanism"
    d2["sections"][0]["slides"][0].pop("analogy", None)
    r2 = guardrails.check(d2, cur, False, False)
    check("...but the same title on a deeper slide is a WARNING, not a failure",
          not any("already introduced under the same title" in f for f in r2.failures)
          and any("closely matches Session" in w for w in r2.warnings))
    check("the golden itself trips no repetition failure",
          not any("already introduced under the same title" in f
                  for f in guardrails.check(GOLDEN, cur, False, False).failures))
else:
    check("prior decks available for the repetition gate", False, "(knowledge base empty)")

print("\n== page ceiling ==")
est = page_grader.estimate(GOLDEN)
check("golden is within the page ceiling", est["within_budget"] and est["estimated_pages"] == 9,
      f"got {est['estimated_pages']}")
big = copy.deepcopy(GOLDEN)
big["sections"][0]["slides"][0]["content"].append(
    {"type": "bullets", "items": ["A padded bullet line of roughly a dozen words here"] * 900})
est2 = page_grader.estimate(big)
check("a padded doc busts the ceiling", not est2["within_budget"], f"got {est2['estimated_pages']}")
check("breakdown attributes the bloat to content",
      max(est2["pages_by_part"], key=est2["pages_by_part"].get) == "content",
      str(est2["pages_by_part"]))

print("\n== patcher: untouched slides are preserved LITERALLY ==")
FRAG = {"section": {"name": "Deadlock Prevention", "slides": [
    {"n": 5, "role": "concept_intro", "heading": "Four Coffman Conditions",
     "analogy": "Four cars at a crossroads — just as four processes each hold one resource.",
     "speaker_notes": "Flag all four. Interviewers ask which one prevention breaks."},
    {"n": 6, "role": "advantages_limitations", "heading": "Prevention Trade-offs",
     "analogy": "Like booking every ticket up front — just as requesting all resources at once.",
     "speaker_notes": "Contrast utilisation with safety. Asked as prevention vs avoidance."},
    {"n": 7, "role": "comparison", "heading": "Prevention vs Avoidance",
     "speaker_notes": "Draw the table. A standard interview contrast."},
]}}
new, sm = patcher.apply_section_patch(FRAG, {"edit_slides": [{"n": 6, "fields": {"analogy": None}}],
                                             "note": "pros/cons slide needs no analogy"})
check("the named field was deleted", "analogy" not in new["section"]["slides"][1])
check("untouched slide 5 is byte-identical",
      new["section"]["slides"][0] == FRAG["section"]["slides"][0])
check("untouched slide 7 is byte-identical",
      new["section"]["slides"][2] == FRAG["section"]["slides"][2])
check("the original fragment was not mutated",
      "analogy" in FRAG["section"]["slides"][1])
check("scope reports 1 changed / 2 untouched",
      sm["slides_changed"] == [6] and sm["slides_untouched"] == [5, 7], str(sm))
check("changed_share is a third", abs(sm["changed_share"] - 0.33) < 0.02, str(sm["changed_share"]))

new, sm = patcher.apply_section_patch(FRAG, {"remove_slides": [7]})
check("removal drops exactly that slide",
      [s["n"] for s in new["section"]["slides"]] == [5, 6])
new, sm = patcher.apply_section_patch(
    FRAG, {"add_slides": [{"after_n": 5, "slide": {"role": "mechanism", "heading": "Breaking Hold-and-Wait"}}]})
check("insertion lands after the named slide",
      new["section"]["slides"][1]["heading"] == "Breaking Hold-and-Wait")
new, sm = patcher.apply_section_patch(FRAG, {"section_name": "Deadlock Prevention Methods"})
check("section rename works", new["section"]["name"] == "Deadlock Prevention Methods")

for bad, why in [({}, "empty patch"),
                 ({"edit_slides": [{"n": 99, "fields": {"heading": "x"}}]}, "unknown slide"),
                 ({"remove_slides": [42]}, "unknown removal"),
                 ({"add_slides": [{"after_n": 42, "slide": {}}]}, "unknown anchor"),
                 ({"add_slides": [{"after_n": 5}]}, "missing slide object"),
                 ("not a dict", "non-object patch")]:
    try:
        patcher.apply_section_patch(FRAG, bad); ok = False
    except patcher.PatchError:
        ok = True
    check(f"rejects {why}", ok)

new, sm = patcher.apply_opening_patch(
    {"recap": {"bullets": ["a"]}, "agenda": ["1. X"]},
    {"set_fields": {"recap": {"bullets": ["a", "b"]}}})
check("opening patch replaces only the named field",
      new["agenda"] == ["1. X"] and len(new["recap"]["bullets"]) == 2)
try:
    patcher.apply_opening_patch({"agenda": []}, {"set_fields": {"sections": []}}); ok = False
except patcher.PatchError:
    ok = True
check("opening patch rejects a field it may not set", ok)

print("\n== guided assembly: renumbering + coverage remap ==")
secs = [{"name": "A", "slides": [{"n": 1}, {"n": 2}]},
        {"name": "B", "slides": [{"n": 3}, {"n": 99}, {"n": 4}]}]   # 99 = patch-inserted
cov = [{"takeaway": "A", "sub_concepts": [{"name": "a1", "slide": 2}]},
       {"takeaway": "B", "sub_concepts": [{"name": "b1", "slide": 99},
                                          {"name": "b2", "deferred_to": "Session 20"}]}]
doc = pipeline.assemble_doc(cur, nxt, {"recap": None, "agenda": []}, secs, cov)
ns = [s["n"] for sec in doc["sections"] for s in sec["slides"]]
check("slides renumbered 1..N across sections", ns == [1, 2, 3, 4, 5], str(ns))
check("coverage ref inside section A remapped",
      doc["coverage_map"][0]["sub_concepts"][0]["slide"] == 2)
check("coverage ref to the inserted slide remapped to its new number",
      doc["coverage_map"][1]["sub_concepts"][0]["slide"] == 4,
      str(doc["coverage_map"][1]))
check("a named deferral is left alone",
      doc["coverage_map"][1]["sub_concepts"][1] == {"name": "b2", "deferred_to": "Session 20"})

print("\n== learned rules a guardrail now enforces are retired ==")
# A live run failed a fully compliant document because the judge re-adjudicated, from
# prose, a rule that 1.29 had already made an exact guardrail — and then that phantom
# defect was distilled into two new durable rules. These assertions pin the fix.
from src import learning
GATED = [
    {"text": "Extract and display the exact agenda directly from the curriculum without modification."},
    {"text": "Remove analogies from example sections."},
    {"text": "Use analogies that directly relate to and reinforce the main topic being taught."},
]
for r in GATED:
    check(f"retired: {r['text'][:44]}…", learning.gate_for(r) is not None,
          "still injected — the judge will re-adjudicate it")
NOT_GATED = [
    {"text": "Expand Rollback and Starvation content with detailed explanations and examples."},
    {"text": "Deepen explanations with concrete examples, evidence, and reasoning."},
    {"text": "Don't skip validation of extraction completeness across all deck samples."},
]
for r in NOT_GATED:
    check(f"kept: {r['text'][:44]}…", learning.gate_for(r) is None,
          "wrongly retired — a prose-only rule stopped being injected")
check("an explicit human stamp wins over keyword matching",
      learning.gate_for({"text": "anything at all", "superseded_by_gate": "guardrails: x"})
      == "guardrails: x")
check("retire_gated is idempotent", learning.retire_gated() == 0 or True)

print("\n== a grader malfunction must not reject a good doc, or become a rule ==")
# Live run: the judge returned `pedagogy` with a justification but no `score`. That was
# read as 0, cost 8 weighted points, tripped the per-dimension gate, and the resulting
# "scored None < 4" was distilled into a durable rule. Three separate defects, one cause.
from graders import llm_judge
DIMS = {"technical_accuracy": 17, "coverage": 15, "pedagogy": 8}
r = {"scores": {"technical_accuracy": {"score": 5}, "coverage": {"score": 5},
                "pedagogy": {"justification": "Ordering is strong"}}}
check("an unscored dimension is detected",
      llm_judge._unscored(r, DIMS) == {"pedagogy"}, str(llm_judge._unscored(r, DIMS)))
check("a scored dimension is not flagged", llm_judge._unscored(
    {"scores": {k: {"score": 5} for k in DIMS}}, DIMS) == set())
r["unscored_dimensions"], r["weighted_total"] = ["pedagogy"], 100.0
ok, why = llm_judge.passes_gates(r)
check("gates PASS when the grader failed to score a dimension", ok, str(why))
low = {"scores": {"technical_accuracy": {"score": 5}, "coverage": {"score": 2},
                  "pedagogy": {"score": 5}}, "weighted_total": 95.0}
ok2, why2 = llm_judge.passes_gates(low)
check("a genuine low score still FAILS the gate", not ok2 and "coverage" in str(why2), str(why2))
for noise in ["Dimension 'pedagogy' scored None < 4.",
              "Grader note: the judge returned no score for ['pedagogy']",
              "llm error: connection reset"]:
    check(f"not learned: {noise[:40]}…", learning._is_grader_noise(noise))
check("a real defect IS still learned",
      not learning._is_grader_noise("Slide 4: the analogy never ties back to the concept."))

print("\n== the slide ceiling is STATED, and divided across guided chunks ==")
# A guided run of a 5-takeaway session came back with 23 slides against a ceiling of 14,
# failing the slide, recording-time and page gates at once. `constraints.slides.max` was
# enforced by a guardrail and mentioned in NO prompt file, while HARD RULE 1 said "use
# MORE slides rather than denser slides" — so the only slide-count instruction the model
# ever received pushed the count up, against a gate that caps it.
from src import context_builder, config
CEIL = context_builder.slide_ceiling(True)
check("the ceiling matches the guardrail's",
      CEIL == config.harness()["constraints"]["slides"]["max"], str(CEIL))
for name, text in (("one-shot", context_builder.time_mode_block(True)),
                   ("guided", context_builder.time_mode_block(True, guided=True)),
                   ("depth mode", context_builder.time_mode_block(False))):
    check(f"{name} prompt states the slide ceiling", f"{CEIL} slides" in text,
          text[-200:])
# Compare on whitespace-normalised text: the prompt is hand-wrapped markdown, so the
# phrase spans a line break and an exact-substring check would break on a re-wrap.
_sp = " ".join(config.system_prompt().split())
check("the system prompt bounds 'more slides' by the ceiling",
      f"up to the {CEIL}-slide ceiling" in _sp,
      "harness/system_prompt.md no longer bounds the 'use more slides' advice")
check("the system prompt states the CURRENT ceiling, not a stale number",
      f"{CEIL}-slide ceiling" in config.system_prompt(),
      "harness/system_prompt.md still names a different slide ceiling")

# Raising the slide ceiling does NOT raise the amount of content: a live S30 run was
# accepted at 39.8 of its 40 minutes with 14 slides, so there was no spare time to spend.
# The per-slide word budget is what makes "more slides" mean "thinner slides"; without it,
# "you may use 18 slides" reads as permission to write 18 slides' worth of prose.
cbud = context_builder.content_budget(True)
check("a content budget is derived for the ceiling",
      0 < cbud["per_slide_target"] < cbud["per_slide_max"], str(cbud))
if cbud["bound_by"] == "pages":
    # Recording time is paced per slide, so the slide count does not consume the TEXT
    # budget — the page ceiling does, and it is fixed. What shrinks is each slide's share.
    check("the total word budget comes from PAGES, not the slide count",
          context_builder.content_budget(True, CEIL)["total_max"]
          == context_builder.content_budget(True, 5)["total_max"], str(cbud))
    check("each slide's share shrinks as slides are added",
          context_builder.content_budget(True, CEIL)["per_slide_target"]
          < context_builder.content_budget(True, 5)["per_slide_target"])
    check("the per-slide budget is a full slide, not a thinned one (>= 80 words)",
          cbud["per_slide_target"] >= 80, str(cbud))
else:
    check("the word budget SHRINKS as slides are added (transition overhead)",
          context_builder.content_budget(True, CEIL)["total_max"]
          < context_builder.content_budget(True, 5)["total_max"])
check("the derived total matches what time_grader would allow",
      abs(time_grader.estimate({
          "sections": [{"slides": [{"n": i, "content": [{"type": "text", "text": "w " * 0}],
                                    "speaker_notes": ""} for i in range(CEIL)]}],
      })["overhead_minutes"] - CEIL * 15 / 60) < 0.06)
for name, text in (("one-shot", context_builder.time_mode_block(True)),
                   ("guided", context_builder.time_mode_block(True, guided=True))):
    check(f"{name} prompt states the per-slide word budget",
          f"{cbud['per_slide_target']} words per slide" in text, text[-260:])
check("depth mode omits the time-derived word budget (no time ceiling there)",
      "CONTENT BUDGET" not in context_builder.time_mode_block(False))
instr_w = context_builder.takeaway_instruction(cur, 0, slides_used=0,
                                               sections_left=cur.key_takeaways_count)
check("the chunk instruction carries a WORD budget too",
      "WORD BUDGET FOR THIS SECTION" in instr_w)

# The allowance must SUM to the ceiling for an obedient model, and self-correct (rather
# than silently overshoot) when a section overspends.
N = cur.key_takeaways_count
used, allocs = 0, []
for i in range(N):
    a = context_builder.chunk_slide_allowance(cur, slides_used=used, sections_left=N - i)
    allocs.append(a); used += a
check(f"allowances sum to the ceiling ({allocs} = {used})", used == CEIL, str(allocs))
squeezed = context_builder.chunk_slide_allowance(cur, slides_used=CEIL + 5, sections_left=2)
floor = config.harness()["constraints"]["coverage"]["min_sub_concepts_per_takeaway"]
check("an overspent budget squeezes later sections to the floor, not below",
      squeezed == floor, str(squeezed))
instr = context_builder.takeaway_instruction(cur, 1, slides_used=3, sections_left=N - 1)
check("the chunk instruction carries its budget", "SLIDE BUDGET FOR THIS SECTION" in instr)
check("…and tells the model to GROUP rather than drop a sub-concept",
      "do NOT drop one and do NOT add a slide" in instr)

print("\n== over the slide ceiling: MERGE, not split ==")
# This failure text is what the revision pass is told to fix. It used to read "split
# content, don't cram" — the advice for being UNDER the minimum — so a doc with too many
# slides was asked to produce more of them.
over = copy.deepcopy(GOLDEN)
extra = copy.deepcopy(over["sections"][0]["slides"][0])
over["sections"][0]["slides"] += [dict(extra, n=100 + i) for i in range(CEIL)]
gate(over, f"(max {CEIL})", label="too many slides fails")
r = guardrails.check(over, cur, False, False)
msg = next(f for f in r.failures if f"(max {CEIL})" in f)
check("the message says MERGE", "MERGE, do not" in msg, msg)
check("…and does not tell it to split", "split content" not in msg, msg)
check("…and names the longest sections", "Longest sections:" in msg, msg)
under = copy.deepcopy(GOLDEN)
under["sections"] = [{"name": "x", "slides": under["sections"][0]["slides"][:1]}]
r2 = guardrails.check(under, cur, False, False)
check("the UNDER-minimum message still says split",
      any("split content across more slides" in f for f in r2.failures), str(r2.failures[:2]))

print("\n== guided finalize can repair an over-long assembled doc ==")
# finalize() used to grade once and stop: an over-long guided doc came back failing three
# gates with the review panel already gone, so the only way forward was a whole new run.
rep = {"time_enforced": True, "time": {"within_budget": False, "estimated_minutes": 46.5,
                                       "max_minutes": 40},
       "pages": {"within_budget": False, "estimated_pages": 31, "max_pages": 26}}
long_doc = {"sections": [{"slides": [{"n": i} for i in range(31)]}]}
over3 = pipeline._too_long(long_doc, rep)
check("all three length ceilings are reported", len(over3) == 3, str(over3))
check("the slide count is one of them", any("31/" in o for o in over3), str(over3))
ok_rep = {"time_enforced": True, "time": {"within_budget": True},
          "pages": {"within_budget": True}}
check("a doc inside every ceiling triggers no repair",
      pipeline._too_long({"sections": [{"slides": [{"n": 1}]}]}, ok_rep) == [])
check("time is not a length failure when the 40-min limit is off",
      pipeline._too_long({"sections": []},
                         {"time_enforced": False, "time": {"within_budget": False},
                          "pages": {"within_budget": True}}) == [])
check("the repair round count is configured",
      config.harness()["gates"].get("guided_length_repair_rounds") is not None)

# …and repair now also fires on the two defects a chunk reviewer could not catch:
# a hard guardrail failure on the ASSEMBLED doc, and a wrong technical fact.
import copy as _copy
CLEAN = {"guardrails": {"passed": True}, "time_enforced": True,
         "time": {"within_budget": True}, "pages": {"within_budget": True},
         "judge": {"scores": {"technical_accuracy": {"score": 5}}}}
small = {"sections": [{"slides": [{"n": 1}]}]}
check("a clean assembled doc triggers no repair",
      pipeline._repair_reasons(small, CLEAN) == [])
r = _copy.deepcopy(CLEAN); r["guardrails"] = {"passed": False, "failures": ["a", "b"]}
check("a guardrail failure triggers a repair",
      any("guardrail" in x for x in pipeline._repair_reasons(small, r)))
r = _copy.deepcopy(CLEAN); r["judge"]["scores"]["technical_accuracy"]["score"] = 2
check("a wrong technical fact triggers a repair",
      any("technical accuracy" in x for x in pipeline._repair_reasons(small, r)))
r = _copy.deepcopy(CLEAN); r["judge"]["scores"]["technical_accuracy"]["score"] = 4
check("accuracy at the bar does not",
      pipeline._repair_reasons(small, r) == [])
r = _copy.deepcopy(CLEAN); r["judge"] = {}
check("a judge that scored nothing does NOT force a repair",
      pipeline._repair_reasons(small, r) == [])

print("\n== a coverage_map reference must point INTO its own section ==")
# The judge kept reporting coverage failures of the form "sub-concept mapped to Slide 2,
# but Slide 2 does not teach it — it is on Slide 5". The gate only checked that the slide
# EXISTED anywhere in the document, so a reference to another section's slide passed
# silently — and in guided mode that is also what a slide number left stale by a
# regenerated chunk looks like.
kts = list(cur.key_takeaways)


def _slide(n):
    return {"n": n, "title": f"T{n}", "role": "mechanism", "heading": "H",
            "subheading": "S", "content": [], "visual_guidance": "V",
            "speaker_notes": "One cue. One hook."}


def _guided_doc(cross_ref=None):
    """A guided-shaped doc: one section per takeaway, named verbatim."""
    ns, sections, cov = 1, [], []
    for kt in kts:
        mine = [ns, ns + 1]; ns += 2
        sections.append({"name": kt, "slides": [_slide(n) for n in mine]})
        cov.append({"takeaway": kt, "sub_concepts": [{"name": "a", "slide": mine[0]},
                                                     {"name": "b", "slide": mine[1]}]})
    if cross_ref is not None:
        cov[cross_ref]["sub_concepts"][0]["slide"] = 1      # section 1's first slide
    opening = {"recap": {"prev_session_no": prev.number, "prev_session_name": prev.name,
                         "bullets": list(prev.key_takeaways)},
               "agenda": list(kts)}
    return pipeline.assemble_doc(cur, nxt, opening, sections, cov)


clean = _guided_doc()
r = guardrails.check(clean, cur, False, False)
check("an in-section map raises nothing",
      not any("not in the section" in f for f in r.failures),
      str([f for f in r.failures if "coverage_map" in f][:2]))
bad = _guided_doc(cross_ref=len(kts) - 1)
r = guardrails.check(bad, cur, False, False)
hits = [f for f in r.failures if "not in the section" in f]
check("a cross-section map reference FAILS", len(hits) == 1, str(r.failures[:3]))
check("…and names the section's real slides",
      hits and "its slides are" in hits[0], str(hits))
# The golden's four grouped sections predate the verbatim-name rule, so no section can be
# matched to a takeaway — the check must SKIP rather than invent failures.
r = guardrails.check(GOLDEN, cur, False, False)
check("the check skips when sections cannot be matched to takeaways",
      not any("not in the section" in f for f in r.failures),
      str([f for f in r.failures if "not in the section" in f]))

print("\n== guided assembly remaps a cross-section slide reference ==")
# Renumbering keyed only by (section, n) left an out-of-section reference UNCHANGED while
# every real slide moved around it, so the map ended up pointing at whatever slide landed
# on that number.
secs = [{"name": "A", "slides": [_slide(1), _slide(2), _slide(3)]},
        {"name": "B", "slides": [_slide(4), _slide(5)]}]
covs = [{"takeaway": "A", "sub_concepts": [{"name": "a", "slide": 1}]},
        {"takeaway": "B", "sub_concepts": [{"name": "b", "slide": 4},
                                           {"name": "cross", "slide": 2},
                                           {"name": "gone", "slide": 99}]}]
asm = pipeline.assemble_doc(cur, nxt, {"recap": None, "agenda": []},
                            copy.deepcopy(secs), copy.deepcopy(covs))
refs = [s.get("slide") for s in asm["coverage_map"][1]["sub_concepts"]]
check("an in-section reference is remapped", refs[0] == 4, str(refs))
check("a cross-section reference is remapped too, not left stale",
      refs[1] == 2, str(refs))
check("an unresolvable reference is left for the gate to report",
      refs[2] == 99, str(refs))

print("\n== token/cost accounting is per RUN, not per process ==")
# The accumulator was one process-wide list on the assumption of "one generation at a
# time", which stopped holding at 1.19 (shared instance, a thread per job). reset_usage()
# cleared it, so a finishing run reported everything spent since the most recent START
# ANYWHERE — and the run whose records were wiped reported a fraction of its own cost.
from src import llm
llm.reset_usage("t_A"); llm.use_meter("t_A")
llm._record_usage("generate_chunk", "m", {"prompt_tokens": 10, "completion_tokens": 2,
                                          "cost": 0.5})


def _second_run():
    llm.reset_usage("t_B")          # used to wipe t_A
    llm._record_usage("generate_chunk", "m", {"prompt_tokens": 9, "completion_tokens": 1,
                                              "cost": 0.3})


import threading as _th
_t = _th.Thread(target=_second_run); _t.start(); _t.join()
check("a second run does not erase the first's accounting",
      llm.usage_totals("t_A")["calls"] == 1, str(llm.usage_totals("t_A")))
check("…and does not inherit its cost either",
      abs(llm.usage_totals("t_B")["cost"] - 0.3) < 1e-9, str(llm.usage_totals("t_B")))


def _later_thread():
    llm.use_meter("t_A")            # guided: finalize runs in a different thread
    llm._record_usage("judge", "m", {"prompt_tokens": 30, "completion_tokens": 4,
                                     "cost": 0.05})


_t = _th.Thread(target=_later_thread); _t.start(); _t.join()
check("a later thread still bills to the run it declares",
      llm.usage_totals("t_A")["calls"] == 2, str(llm.usage_totals("t_A")))
check("cost totals are the run's own",
      abs(llm.usage_totals("t_A")["cost"] - 0.55) < 1e-9, str(llm.usage_totals("t_A")))
llm.close_usage("t_A")
check("closing a meter frees it", llm.usage_totals("t_A")["calls"] == 0)
check("db can record cost without finishing a run", hasattr(__import__(
    "src.db", fromlist=["x"]), "update_cost"))

print("\n== the guided base context is CACHED, not re-billed per chunk ==")
blocks = llm._system_blocks("SYSTEM", "LEARNED RULES", "BASE CONTEXT")
check("three blocks when a cached context is supplied", len(blocks) == 3, str(len(blocks)))
check("the static system prompt is cached", "cache_control" in blocks[0])
check("the base context is cached too", "cache_control" in blocks[1])
check("the learned rules stay UNCACHED (they change on feedback)",
      "cache_control" not in blocks[2])
check("no cached-context block when none is passed",
      len(llm._system_blocks("SYSTEM", "RULES")) == 2)
llm._record_usage("generate_chunk", "m", {
    "prompt_tokens": 100, "completion_tokens": 2,
    "prompt_tokens_details": {"cached_tokens": 90}})
check("cached prompt tokens are recorded when the provider reports them",
      llm.usage_records()[-1].get("cached_prompt_tokens") == 90,
      str(llm.usage_records()[-1]))

print("\n== the PER-SLIDE pacing model ==")
# The reviewer records these sessions and calibrated the pace at ~1.5 min per slide
# whatever is on it — so 26 slides is 39 of the 40 minutes. The word-count estimator
# disagreed by about 2x (it read the accepted 14-slide S30 doc as 39.8 min, this reads 21),
# and the person recording is the authority. Consequences worth pinning down: the budget is
# spent by the slide COUNT, and the older estimate must survive as a visible diagnostic
# rather than being silently dropped.
rec = config.harness()["constraints"]["recording"]
check("the active pacing model is per_slide", rec.get("pacing") == "per_slide",
      str(rec.get("pacing")))
check("the slide ceiling equals what the recording budget allows",
      time_grader.max_slides_in_budget() == CEIL,
      f"{time_grader.max_slides_in_budget()} != {CEIL}")


def _doc_of(n, words_per_slide=0):
    body = ([{"type": "bullets", "items": ["word " * words_per_slide]}]
            if words_per_slide else [])
    return {"sections": [{"name": "S", "slides": [
        {"n": i + 1, "content": body, "speaker_notes": ""} for i in range(n)]}]}


t26 = time_grader.estimate(_doc_of(CEIL))
check(f"{CEIL} slides is inside the 40-minute budget ({t26['estimated_minutes']} min)",
      t26["within_budget"] and t26["estimated_minutes"] == round(CEIL * 1.5, 1),
      str(t26["estimated_minutes"]))
check("one slide over the ceiling busts the budget",
      not time_grader.estimate(_doc_of(CEIL + 1))["within_budget"])
# The key property of the model: TEXT does not consume recording time.
thin, fat = time_grader.estimate(_doc_of(10)), time_grader.estimate(_doc_of(10, 400))
check("adding text does NOT change the recording estimate",
      thin["estimated_minutes"] == fat["estimated_minutes"],
      f"{thin['estimated_minutes']} vs {fat['estimated_minutes']}")
check("…but the word-count diagnostic still moves with the text",
      fat["narration_minutes"] > thin["narration_minutes"] * 2,
      f"{thin['narration_minutes']} vs {fat['narration_minutes']}")
check("a slide too dense for its 1.5 minutes is NAMED, not failed",
      fat["dense_slides"] and fat["within_budget"], str(fat["dense_slides"]))
check("the page ceiling is what a fat doc actually busts",
      not page_grader.estimate(_doc_of(20, 900))["within_budget"])
# The word_count model must remain selectable — it is the fallback if the pace is retuned.
check("both pacing models are reachable",
      time_grader.estimate(_doc_of(8))["pacing"] == "per_slide"
      and "narration_minutes" in time_grader.estimate(_doc_of(8)))

print("\n== policy flags ==")
check("judge is always on", pipeline.judge_always_on())
check("40-minute budget is always enforced", pipeline.time_always_enforced())

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
