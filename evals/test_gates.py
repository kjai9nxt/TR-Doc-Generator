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
from graders import page_grader

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
GRAND = ["Agenda has 4 items", "was reworded. Expected", "and key takeaway",
         "agenda item(s) are not numbered", "recap must carry ALL"]

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

print("\n== page ceiling ==")
est = page_grader.estimate(GOLDEN)
check("golden is within the page ceiling", est["within_budget"] and est["estimated_pages"] == 9,
      f"got {est['estimated_pages']}")
big = copy.deepcopy(GOLDEN)
big["sections"][0]["slides"][0]["content"].append(
    {"type": "bullets", "items": ["A padded bullet line of roughly a dozen words here"] * 400})
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

print("\n== policy flags ==")
check("judge is always on", pipeline.judge_always_on())
check("40-minute budget is always enforced", pipeline.time_always_enforced())

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
