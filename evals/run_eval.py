"""Eval runner. Offline mode validates the golden fixture through the
deterministic graders + invariants (no API). --live runs the full pipeline
on the generate_cases and checks the same invariants on fresh output.
"""
from __future__ import annotations
import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config, course_loader, pptx_ingest  # noqa: E402
from guardrails import guardrails  # noqa: E402
from graders import time_grader  # noqa: E402

EVAL_SET = ROOT / "evals" / "eval_set.yaml"
CASES_DIR = ROOT / "evals" / "cases"


def _write_report(kind: str, report: dict) -> Path:
    """Persist the eval scores/metrics so every run leaves a generated artifact
    (this is the 'eval set' output — previously the runner only printed to stdout)."""
    CASES_DIR.mkdir(parents=True, exist_ok=True)
    path = CASES_DIR / f"{kind}_result.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ↳ eval report written to {path.relative_to(ROOT)}")
    return path


def _slides(doc):
    return [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]


def check_invariants(doc, session, is_first, is_last, *, enforce_time: bool = True):
    """enforce_time=False (the user's 40-minute limit turned off) => the recording
    budget and the concise-mode slide ceiling are not invariants for this doc; the
    estimate is still computed and reported, just not failed on."""
    slides = _slides(doc)
    fails = []
    if len(doc.get("agenda", [])) > session.key_takeaways_count:
        fails.append("agenda > key takeaways")
    te = time_grader.estimate(doc)
    if enforce_time and not te["within_budget"]:
        fails.append(f"time {te['estimated_minutes']} > 40")
    slide_max = 12 if enforce_time else config.harness()["constraints"]["slides"].get("max_rich", 18)
    if not (5 <= len(slides) <= slide_max):
        fails.append(f"slide count {len(slides)} out of [5,{slide_max}]")
    for s in slides:
        for req in ("heading", "subheading", "content",
                    "analogy", "visual_guidance", "speaker_notes"):
            if not s.get(req):
                fails.append(f"slide {s.get('n')} missing required field '{req}'")
    if (doc.get("recap") is not None) != (not is_first):
        fails.append("recap presence != (session_no > 1)")
    analogies = [s["analogy"].strip().lower() for s in slides if s.get("analogy")]
    if len(analogies) != len(set(analogies)):
        fails.append("duplicate analogy across slides")
    return fails, te


def run_offline():
    spec = yaml.safe_load(EVAL_SET.read_text())
    g = spec["golden"]
    doc = json.loads((ROOT / g["json"]).read_text())
    # Load the golden's OWN curriculum (see `course_file` in eval_set.yaml), not the
    # currently-synced course — otherwise this regression anchor drifts every time
    # someone connects a different course's sheets.
    course_file = g.get("course_file")
    sessions = course_loader.load_sessions(ROOT / course_file if course_file else None)
    prev, cur, nxt = course_loader.neighbours(g["session_no"], sessions)
    is_first, is_last = prev is None, nxt is None

    print(f"== OFFLINE: golden Session {g['session_no']} ==")
    gr = guardrails.check(doc, cur, is_first, is_last)
    inv_fails, te = check_invariants(doc, cur, is_first, is_last)

    # Split the guardrail failures into ones that must block and ones the golden is
    # explicitly grandfathered on (see `known_deviations` in eval_set.yaml). Both are
    # printed — a grandfathered gate stays visible instead of quietly disappearing.
    exempt_pats = g.get("known_deviations") or []
    blocking, grandfathered = [], []
    for f in gr.failures:
        (grandfathered if any(p in f for p in exempt_pats) else blocking).append(f)

    ok = True
    print(f"  guardrails passed : {gr.passed}"
          + (f"  ({len(blocking)} blocking, {len(grandfathered)} grandfathered)"
             if gr.failures else ""))
    for f in blocking:
        print(f"     FAIL: {f}")
    for f in grandfathered:
        print(f"     GRANDFATHERED (golden predates this rule): {f}")
    print(f"  est. minutes      : {te['estimated_minutes']} (budget {te['max_minutes']}, "
          f"within={te['within_budget']})")
    print(f"  slides / spoken w : {te['slide_count']} / {te['spoken_words']}")
    if gr.warnings:
        for w in gr.warnings:
            print(f"     warn: {w}")
    if inv_fails:
        ok = False
        for f in inv_fails:
            print(f"     INVARIANT FAIL: {f}")
    if g["expect"]["guardrails_pass"] and blocking:
        ok = False
    print(f"  RESULT: {'PASS' if ok else 'FAIL'}"
          + (f"  ({len(grandfathered)} grandfathered gate(s) — re-author the golden to clear them)"
             if grandfathered else "") + "\n")

    # KB extraction completeness (informational — guideline 2/3)
    ext = pptx_ingest.completeness_report()
    print(f"== KB EXTRACTION: {ext['decks_checked']} deck(s), "
          f"{ext['decks_with_issues']} with issues ==")
    for d in ext["decks"]:
        if not d["ok"]:
            print(f"     S{d['session_no']} ({d['source_file']}): {'; '.join(d['issues'])}")
    print()

    _write_report("offline", {
        "kind": "offline",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall_pass": ok,
        "extraction": ext,
        "golden": {
            "session_no": g["session_no"],
            "pass": ok,
            "guardrails_pass": gr.passed,
            "guardrail_failures": blocking,
            "grandfathered_failures": grandfathered,
            "warnings": list(gr.warnings),
            "invariant_failures": inv_fails,
            "metrics": {
                "estimated_minutes": te["estimated_minutes"],
                "max_minutes": te["max_minutes"],
                "within_budget": te["within_budget"],
                "slide_count": te["slide_count"],
                "spoken_words": te["spoken_words"],
            },
        },
    })
    return ok


def run_live():
    from src import pipeline
    spec = yaml.safe_load(EVAL_SET.read_text())
    sessions = course_loader.load_sessions()
    all_ok = True
    cases = []
    for case in spec["generate_cases"]:
        sn = case["session_no"]
        print(f"== LIVE: Session {sn} ==")
        result = pipeline.run(sn, use_judge=True)
        doc = result["doc"]
        final = result["history"][-1]
        rubric = (final.get("judge") or {}).get("weighted_total")
        prev, cur, nxt = course_loader.neighbours(sn, sessions)
        inv_fails, te = check_invariants(doc, cur, prev is None, nxt is None)
        exp = case["expect"]
        if "recap_present" in exp and (doc.get("recap") is not None) != exp["recap_present"]:
            inv_fails.append("recap_present mismatch")
        if exp.get("upcoming_present") and not doc.get("upcoming_session"):
            inv_fails.append("upcoming missing")
        ok = not inv_fails and final.get("accepted", False)
        all_ok = all_ok and ok
        for f in inv_fails:
            print(f"   FAIL: {f}")
        print(f"  rubric={rubric} | est={te['estimated_minutes']}min | "
              f"slides={te['slide_count']} | RESULT: {'PASS' if ok else 'FAIL'}\n")
        cases.append({
            "session_no": sn, "pass": ok, "accepted": final.get("accepted"),
            "rubric_weighted_total": rubric, "invariant_failures": inv_fails,
            "metrics": {
                "estimated_minutes": te["estimated_minutes"],
                "within_budget": te["within_budget"],
                "slide_count": te["slide_count"],
                "spoken_words": te["spoken_words"],
            },
        })

    scored = [c["rubric_weighted_total"] for c in cases if c["rubric_weighted_total"] is not None]
    _write_report("live", {
        "kind": "live",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "overall_pass": all_ok,
        "cases_passed": sum(1 for c in cases if c["pass"]),
        "cases_total": len(cases),
        "avg_rubric": round(sum(scored) / len(scored), 1) if scored else None,
        "cases": cases,
    })
    return all_ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--live", action="store_true", help="Run full pipeline (needs API key).")
    args = ap.parse_args()
    ok = run_live() if args.live else run_offline()
    sys.exit(0 if ok else 1)
