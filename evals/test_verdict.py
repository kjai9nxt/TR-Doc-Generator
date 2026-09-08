"""THE COMBINED VERDICT — one status per source, and no judgement of its own.

    python -m evals.test_verdict          # no API key needed, ~2 seconds

Every check in here already ran; their answers were already in the report. What did not
exist was one place that said which SOURCE each answer belonged to, so "did this respect
the curriculum?" was answerable only by reading nineteen failure strings and knowing
which of them were about the curriculum.

Two ways that can go wrong, and both are pinned below:

1. IT MUST ADD NO JUDGEMENT. `accepted` is the release gate. A summary that can disagree
   with its own inputs — passing a document the graders rejected, or the reverse — is
   worse than no summary.

2. IT MUST NOT READ FAILURE TEXT. Each guardrail failure carries the source it was
   enforcing (guardrails._Tagged). src/pipeline._repair_reasons says why: the issue
   strings are written for the reviewer and reworded freely, so reading them for meaning
   breaks on the next edit. This suite reworded one on purpose to prove the attribution
   does not depend on it.
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import course_loader, pipeline               # noqa: E402
from guardrails import guardrails as G                # noqa: E402
from graders import verdict, skill_report             # noqa: E402

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

print("\n== every gate declares which source it enforces ==")
r = G.check(GOLDEN, cur, False, False)
check("a failure list comes with a source list of the same length",
      len(r.failures) == len(r.failure_sources),
      f"{len(r.failures)} vs {len(r.failure_sources)}")
check("…and every source is one of the five",
      set(r.failure_sources) <= {G.CURRICULUM, G.PROFILE, G.SKILLS, G.MEMORY, G.GENERIC},
      str(set(r.failure_sources)))
check("warnings are attributed too",
      len(r.warnings) == len(r.warning_sources),
      f"{len(r.warnings)} vs {len(r.warning_sources)}")
check("it survives as_dict, which is what the report carries",
      len(r.as_dict()["failure_sources"]) == len(r.failures))

print("\n== the golden's own defects land under the right sources ==")
# The golden PREDATES several rules (its agenda is not the curriculum lines verbatim,
# its sections are titled rather than named after the takeaways) — which evals/test_gates
# already tracks. That makes it a real fixture for attribution: those are curriculum
# failures and nothing else.
by = {}
for f, src in zip(r.failures, r.failure_sources):
    by.setdefault(src, []).append(f)
check("the reworded agenda is a CURRICULUM failure",
      any("Agenda item 1 was reworded" in f for f in by.get(G.CURRICULUM, [])),
      str(list(by)))
check("the section-opener role is a PROFILE failure",
      any("opens on slide" in f for f in by.get(G.PROFILE, [])),
      str(by.get(G.PROFILE))[:160])
check("nothing was dumped into GENERIC as a catch-all",
      len(by.get(G.GENERIC, [])) < len(r.failures), str(len(by.get(G.GENERIC, []))))

print("\n== attribution does not depend on the wording ==")
# The whole point of tagging. Reword a failure and the source must not move.
r2 = G.check(GOLDEN, cur, False, False)
moved = copy.deepcopy(r2.as_dict())
moved["failures"] = [f.replace("Agenda", "AGENDA LINE") for f in moved["failures"]]
v = verdict.build({"guardrails": moved, "accepted": False})
check("a reworded failure keeps its source", v["sources"][G.CURRICULUM] == verdict.FAIL,
      str(v["sources"]))

print("\n== five sources, each PASS unless something under it failed ==")
clean = {"guardrails": {"passed": True, "failures": [], "warnings": [],
                        "failure_sources": [], "warning_sources": []},
         "time": {"within_budget": True, "estimated_minutes": 36, "max_minutes": 40},
         "pages": {"within_budget": True, "estimated_pages": 22, "max_pages": 26},
         "accepted": True}
v = verdict.build(clean)
check("a clean report is PASS on all five",
      all(s == verdict.PASS for s in v["sources"].values()), str(v["sources"]))
check("…and PASS overall", v["overall"] == verdict.OK, v["overall"])
check("…with nothing listed", v["issues"] == [], str(v["issues"]))

for src in (G.CURRICULUM, G.PROFILE, G.MEMORY, G.GENERIC):
    one = copy.deepcopy(clean)
    one["guardrails"].update(passed=False, failures=["something went wrong"],
                             failure_sources=[src])
    one["accepted"] = False
    v = verdict.build(one)
    others = [k for k, s in v["sources"].items() if s != verdict.PASS]
    check(f"a {src} failure marks {src} and nothing else",
          v["sources"][src] == verdict.FAIL and others == [src], str(v["sources"]))

print("\n== PARTIAL exists for Skills, and only for Skills ==")
part = copy.deepcopy(clean)
part["skill_report"] = {"skills": [
    {"ref": "S1", "text": "Show the snippet first", "verdict": skill_report.PARTIAL},
    {"ref": "S2", "text": "Never teach class components", "verdict": skill_report.PASS}]}
part["accepted"] = False
v = verdict.build(part)
check("a loosely-followed skill is PARTIAL, not FAIL",
      v["sources"][G.SKILLS] == verdict.PARTIAL, str(v["sources"]))
check("…and it is named in the list",
      any("followed only in places" in i["detail"] for i in v["issues"]), str(v["issues"]))
brk = copy.deepcopy(part)
brk["skill_report"]["skills"][0]["verdict"] = skill_report.FAIL
check("a broken skill is FAIL",
      verdict.build(brk)["sources"][G.SKILLS] == verdict.FAIL)
na = copy.deepcopy(part)
na["skill_report"]["skills"][0]["verdict"] = skill_report.NA
check("a skill the session never engaged is not a failure",
      verdict.build(na)["sources"][G.SKILLS] == verdict.PASS)
unk = copy.deepcopy(part)
unk["skill_report"]["skills"][0]["verdict"] = skill_report.UNKNOWN
check("…nor is one nobody could rule on",
      verdict.build(unk)["sources"][G.SKILLS] == verdict.PASS)

print("\n== the length ceilings and the rubric are GENERIC too ==")
# Otherwise a 30-page document reports five clean lines.
long_doc = copy.deepcopy(clean)
long_doc["pages"] = {"within_budget": False, "estimated_pages": 31, "max_pages": 26}
long_doc["accepted"] = False
v = verdict.build(long_doc)
check("over the page ceiling fails GENERIC",
      v["sources"][G.GENERIC] == verdict.FAIL, str(v["sources"]))
check("…and says the number", any("31" in i["detail"] for i in v["issues"]), str(v["issues"]))
slow = copy.deepcopy(clean)
slow["time"] = {"within_budget": False, "estimated_minutes": 52, "max_minutes": 40}
slow["accepted"] = False
check("over the recording ceiling fails GENERIC",
      verdict.build(slow)["sources"][G.GENERIC] == verdict.FAIL)
off = copy.deepcopy(slow)
off["time_enforced"] = False
check("…unless the 40-minute limit is switched off for this run",
      verdict.build(off)["sources"][G.GENERIC] == verdict.PASS,
      str(verdict.build(off)["sources"]))
ungraded = copy.deepcopy(clean)
ungraded["judge_error"] = "Expecting ',' delimiter"
ungraded["accepted"] = False
check("a grader that did not complete is reported, never counted as a pass",
      verdict.build(ungraded)["sources"][G.GENERIC] == verdict.FAIL)

print("\n== it adds no judgement of its own ==")
# `accepted` is the release gate. The summary reports; it must not decide.
disagree = copy.deepcopy(clean)
disagree["accepted"] = False
check("a report the graders rejected is NEEDS_REPAIR even with five PASSes",
      verdict.build(disagree)["overall"] == verdict.NEEDS_REPAIR,
      verdict.build(disagree)["overall"])
odd = copy.deepcopy(clean)
odd["guardrails"].update(passed=False, failures=["x"], failure_sources=[G.MEMORY])
odd["accepted"] = True          # the graders accepted it; we do not overrule them
v = verdict.build(odd)
check("…and one the graders accepted stays PASS overall",
      v["overall"] == verdict.OK, v["overall"])
check("…while still SHOWING what was found",
      v["sources"][G.MEMORY] == verdict.FAIL and len(v["issues"]) == 1, str(v))

print("\n== it never raises, whatever it is handed ==")
for bad in ({}, None, {"guardrails": None}, {"guardrails": {"failures": ["a", "b"]}},
            {"skill_report": {"skills": None}}, {"guardrails": {"failures": [],
                                                                "failure_sources": ["x"]}}):
    try:
        v = verdict.build(bad)
        okk = isinstance(v.get("text"), str) and set(v["sources"]) == {
            G.CURRICULUM, G.PROFILE, G.SKILLS, G.MEMORY, G.GENERIC}
    except Exception as e:
        okk = False
        v = str(e)
    check(f"handled: {str(bad)[:44]}", okk, str(v)[:100])
check("a report with failures but NO sources does not claim five passes",
      verdict.build({"guardrails": {"failures": ["a"]}, "accepted": False})
      ["sources"][G.GENERIC] == verdict.FAIL)

print("\n== the report carries it, so every caller already has it ==")
_acc, rep, _iss, _rev = pipeline.evaluate(GOLDEN, cur, False, False, use_judge=False)
check("evaluate() puts a verdict on the report", isinstance(rep.get("verdict"), dict))
check("…with the five lines rendered", all(
    lbl in rep["verdict"]["text"] for _k, lbl in verdict.SOURCES),
    rep["verdict"]["text"][:200])
check("…and an overall that matches `accepted`",
      (rep["verdict"]["overall"] == verdict.OK) == bool(rep.get("accepted")),
      f"{rep['verdict']['overall']} vs accepted={rep.get('accepted')}")

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
