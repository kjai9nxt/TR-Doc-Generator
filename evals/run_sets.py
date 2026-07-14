"""Eval-SET runner — score a generated TR doc against evals/sets/*.json.

Three kinds of set:
  • DETERMINISTIC  — checked in code (structure, chunk count, time, conciseness,
                     slide phrasing, extraction). Free, exact.
  • LLM-JUDGE      — qualitative dimensions scored 1-5 by the model against the
                     set's criterion + rubric (relevance, groundedness, analogies,
                     language, ordering, flow, no-repeat, market, recency, coverage).
  • SKIP           — needs inputs a single finished doc can't provide (curriculum
                     sheet, a before/after regeneration, or cross-session behaviour).

Each set has a `pass_threshold`; a set passes when its 1-5 score >= that.

Usage:
    python -m evals.run_sets --session 10          # generate (or reuse) + score all sets
    python -m evals.run_sets --session 10 --no-llm # deterministic sets only (free)
"""
from __future__ import annotations
import argparse
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config, course_loader, pipeline, llm, pptx_ingest, regen_log  # noqa: E402
from graders import time_grader  # noqa: E402

SETS_DIR = ROOT / "evals" / "sets"
CASES_DIR = ROOT / "evals" / "cases"
_WORD = re.compile(r"[a-z0-9']+", re.I)

SKIP = {
    "self_evolution_loop": "needs behaviour compared across multiple sessions (longitudinal)",
}


def _load_sets() -> list[dict]:
    idx = json.loads((SETS_DIR / "index.json").read_text())
    out = []
    for entry in idx["sets"]:
        out.append(json.loads((SETS_DIR / entry["file"]).read_text()))
    return out


def _slides(doc):
    return [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]


def _wc(text):
    return len(_WORD.findall(str(text or "")))


# --------------------------------------------------------------------------- #
# deterministic checkers -> (score 1-5, detail str)
# --------------------------------------------------------------------------- #
def _chk_recording_time(doc, session, sset):
    te = time_grader.estimate(doc)
    m = te["estimated_minutes"]
    if m > 42:
        score = 1
    elif m > 40 or m < 20:
        score = 3
    else:
        score = 5
    return score, f"estimated {m} min (budget {te['max_minutes']}, within={te['within_budget']})"


def _chk_conciseness(doc, session, sset):
    viol = []
    for s in _slides(doc):
        for b in s.get("content", []):
            if b.get("type") == "bullets":
                for it in b.get("items", []):
                    if _wc(it) > 12:
                        viol.append(f"slide {s.get('n')} bullet {_wc(it)}w")
            elif b.get("type") == "text":
                for sent in re.split(r"(?<=[.!?])\s+", b.get("text", "")):
                    if _wc(sent) > 18:
                        viol.append(f"slide {s.get('n')} sentence {_wc(sent)}w")
        if _wc(s.get("heading")) > 8 or _wc(s.get("title")) > 8:
            viol.append(f"slide {s.get('n')} heading/title >8w")
    score = 5 if not viol else (3 if len(viol) <= 2 else 1)
    return score, ("no over-length lines" if not viol else f"{len(viol)} over-length: {viol[:6]}")


def _chk_slide_phrasing(doc, session, sset):
    banned = [b.lower() for b in sset.get("banned_in_slide_content", [])]
    banned += ["in the previous session", "in the next session"]
    hits = []
    for s in _slides(doc):
        parts = [s.get("heading", ""), s.get("subheading", ""), s.get("title", "")]
        for b in s.get("content", []):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "bullets":
                parts += b.get("items", [])
            elif b.get("type") == "table":
                parts += [str(c) for row in b.get("rows", []) for c in row]
        blob = " ".join(parts).lower()
        for phrase in banned:
            if phrase in blob:
                hits.append(f"slide {s.get('n')}: '{phrase}'")
    score = 5 if not hits else 1
    return score, ("no meta-narration in slide content" if not hits else f"banned phrases: {hits[:6]}")


def _chk_document_structure(doc, session, sset):
    v = []
    slides = _slides(doc)
    is_first = session.number <= 1
    if not doc.get("session_title"):
        v.append("no title")
    if not is_first and not doc.get("recap"):
        v.append("recap missing (non-first session)")
    if is_first and doc.get("recap"):
        v.append("recap present on session 1")
    agenda_n = len(doc.get("agenda", []))
    sec_n = len(doc.get("sections", []))
    if agenda_n != sec_n:
        v.append(f"sections({sec_n}) != agenda items({agenda_n})")
    for s in slides:
        miss = [f for f in ("heading", "subheading", "content", "analogy",
                            "visual_guidance", "speaker_notes") if not s.get(f)]
        if miss:
            v.append(f"slide {s.get('n')} missing {miss}")
    if (doc.get("closing") or "").strip() != "Thank You  |  All the Best":
        v.append("closing != 'Thank You  |  All the Best'")
    score = 5 if not v else 1
    return score, ("exact layout + all six slide fields" if not v else f"{len(v)} issue(s): {v[:6]}")


def _chk_chunk_count(doc, session, sset):
    agenda_n = len(doc.get("agenda", []))
    sec_n = len(doc.get("sections", []))
    is_first = session.number <= 1
    ok = (agenda_n == sec_n) and (is_first or bool(doc.get("recap")))
    score = 5 if ok else 1
    return score, f"agenda={agenda_n}, sections={sec_n}, recap={'yes' if doc.get('recap') else 'no'}"


def _chk_extraction(doc, session, sset):
    rep = pptx_ingest.completeness_report()
    score = 5 if rep["ok"] else (3 if rep["decks_with_issues"] <= 1 else 1)
    return score, f"{rep['decks_checked']} deck(s), {rep['decks_with_issues']} with issues"


def _norm_kt(s):
    # Compare on alphanumeric word-content only, so cosmetic punctuation/spacing
    # differences ("UDP : Header" vs "UDP: Header") don't count as a mismatch —
    # while genuine merge/drop/invent (different word content) still does.
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _chk_curriculum_extraction(doc, session, sset):
    """The doc's key_takeaways must exactly reproduce the curriculum's takeaways
    (session.key_takeaways) — same set, same count, no merge/drop/invent/duplicate."""
    truth = [_norm_kt(k) for k in session.key_takeaways]
    got = [_norm_kt(k) for k in doc.get("key_takeaways", [])]
    if not truth:
        return 3, "no curriculum takeaways to compare against"
    missing = [t for t in truth if t not in got]
    extra = [g for g in got if g not in truth]
    dupes = sorted({g for g in got if got.count(g) > 1})
    if sorted(got) == sorted(truth) and not dupes:
        return 5, f"all {len(truth)} takeaways extracted verbatim"
    bits = []
    if len(got) != len(truth):
        bits.append(f"count {len(got)} vs {len(truth)}")
    if missing:
        bits.append(f"missing {missing[:3]}")
    if extra:
        bits.append(f"extra {extra[:3]}")
    if dupes:
        bits.append(f"duplicates {dupes[:3]}")
    return 1, "; ".join(bits)


DETERMINISTIC = {
    "recording_time_budget": _chk_recording_time,
    "conciseness": _chk_conciseness,
    "slide_phrasing_no_meta_narration": _chk_slide_phrasing,
    "document_structure_layout": _chk_document_structure,
    "chunk_count": _chk_chunk_count,
    "ppt_extraction_completeness": _chk_extraction,
    "curriculum_takeaway_extraction": _chk_curriculum_extraction,
}


# --------------------------------------------------------------------------- #
# generic LLM-judge for qualitative sets
# --------------------------------------------------------------------------- #
def _llm_score(doc, session, sset) -> tuple[int, str]:
    h = config.harness()
    m = h["model"]
    model = m["judge"]
    web = ""
    if sset["id"] in ("market_coverage_completeness", "content_recency") \
            and m.get("enable_web_market_check") and m.get("provider", "openrouter").lower() == "openrouter":
        if not model.endswith(":online"):
            model += ":online"
        web = "\nUse a web search to verify current standards/versions and mainstream coverage."
    rubric = "\n".join(f"  {k} = {v}" for k, v in sset.get("rubric", {}).items())
    system = ("You are a strict curriculum-doc reviewer scoring ONE quality dimension. "
              "Return ONLY JSON: {\"score\": <1-5>, \"justification\": \"<one sentence>\"}. "
              "Be discriminating; reserve 5 for genuinely nothing-to-improve.")
    user = f"""DIMENSION: {sset['title']}
WHAT TO CHECK: {sset.get('criterion', sset.get('description',''))}
SCORING RUBRIC:
{rubric}{web}

SESSION KEY TAKEAWAYS:
{json.dumps(session.key_takeaways, ensure_ascii=False)}

TR DOC (JSON):
{json.dumps(doc, ensure_ascii=False)}

Score this ONE dimension now."""
    raw = llm.complete(system=system, user=user, model=model,
                       max_tokens=m.get("judge_max_tokens", 8000), temperature=0.0)
    obj = llm.extract_json(raw)
    return int(obj.get("score", 0)), str(obj.get("justification", ""))[:300]


def _score_regen_event(event, sset) -> tuple[int, str]:
    """LLM-score whether a regenerated chunk addressed the user's stated reason."""
    m = config.harness()["model"]
    rubric = "\n".join(f"  {k} = {v}" for k, v in sset.get("rubric", {}).items())
    system = ("You score whether a REGENERATED chunk addressed the user's stated reason. "
              "Return ONLY JSON {\"score\": <1-5>, \"justification\": \"<one sentence>\"}. "
              "Reserve 5 for a reason fully addressed while keeping the rest intact.")
    user = f"""REASON THE USER GAVE FOR REGENERATING:
{event.get('reason','')}

SCORING RUBRIC:
{rubric}

BEFORE (the version the user rejected):
{event.get('before','')}

AFTER (the regenerated version):
{event.get('after','')}

Did AFTER address the reason? Score now."""
    raw = llm.complete(system=system, user=user, model=m["judge"],
                       max_tokens=m.get("judge_max_tokens", 8000), temperature=0.0)
    obj = llm.extract_json(raw)
    return int(obj.get("score", 0)), str(obj.get("justification", ""))[:300]


# --------------------------------------------------------------------------- #
def run_on_doc(doc: dict, session, *, use_llm: bool = True, enforce_time: bool = True) -> dict:
    results = []
    for sset in _load_sets():
        sid = sset["id"]
        thr = sset.get("pass_threshold", 4)
        if sid in SKIP:
            results.append({"id": sid, "grader": "skip", "skipped": True,
                            "reason": SKIP[sid]})
            continue
        # 40-minute limit off → the recording-time set does not apply.
        if sid == "recording_time_budget" and not enforce_time:
            results.append({"id": sid, "grader": "skip", "skipped": True,
                            "reason": "40-minute limit is OFF for this run — not assessed"})
            continue
        # Feedback adherence: score a recorded Guided-mode regeneration, if any.
        if sid == "feedback_regeneration_adherence":
            evs = regen_log.events(session.number) or regen_log.events()
            if not evs:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": "no regeneration recorded — regenerate a chunk in Guided mode first"})
                continue
            if not use_llm:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": "LLM disabled (--no-llm)"})
                continue
            try:
                score, detail = _score_regen_event(evs[-1], sset)
            except Exception as e:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": f"llm error: {e}"})
                continue
            results.append({"id": sid, "grader": "llm_judge", "score": score,
                            "threshold": thr, "passed": score >= thr,
                            "detail": f"(scored {len(evs)} recorded regen event(s)) {detail}"})
            continue
        if sid in DETERMINISTIC:
            score, detail = DETERMINISTIC[sid](doc, session, sset)
            grader = "deterministic"
        elif use_llm:
            try:
                score, detail = _llm_score(doc, session, sset)
                grader = "llm_judge"
            except Exception as e:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": f"llm error: {e}"})
                continue
        else:
            results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                            "reason": "LLM disabled (--no-llm)"})
            continue
        results.append({"id": sid, "grader": grader, "score": score,
                        "threshold": thr, "passed": score >= thr, "detail": detail})

    scored = [r for r in results if not r.get("skipped")]
    passed = [r for r in scored if r["passed"]]
    return {
        "session_no": session.number,
        "session_name": session.name,
        "overall_pass": len(passed) == len(scored) and len(scored) > 0,
        "scored": len(scored),
        "passed": len(passed),
        "skipped": len(results) - len(scored),
        "sets": results,
    }


def _load_or_make_doc(session_no: int, regenerate: bool):
    sessions = course_loader.load_sessions(None)
    _, cur, _ = course_loader.neighbours(session_no, sessions)
    out = config.harness()["output"]
    safe = out["docx_filename"].format(N=cur.number, SessionName=cur.name).replace("/", "-")
    doc_path = config.ROOT / out["dir"] / (safe.rsplit(".", 1)[0] + ".doc.json")
    if doc_path.exists() and not regenerate:
        print(f"Reusing saved doc: {doc_path.name}")
        return json.loads(doc_path.read_text()), cur
    print("Generating a fresh doc (no saved .doc.json found or --regenerate) …")
    res = pipeline.run(session_no, use_judge=False, do_sync=False)
    return res["doc"], cur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--no-llm", action="store_true", help="deterministic sets only (free)")
    ap.add_argument("--no-time-limit", action="store_true",
                    help="40-min limit off — skip the recording_time set")
    ap.add_argument("--regenerate", action="store_true", help="regenerate the doc even if saved")
    args = ap.parse_args()

    doc, session = _load_or_make_doc(args.session, args.regenerate)
    report = run_on_doc(doc, session, use_llm=not args.no_llm,
                        enforce_time=not args.no_time_limit)

    print(f"\n== EVAL SETS · Session {session.number}: {session.name} ==")
    for r in report["sets"]:
        if r.get("skipped"):
            print(f"  ⏭  {r['id']:34} skipped — {r['reason']}")
        else:
            mark = "✅" if r["passed"] else "❌"
            print(f"  {mark} {r['id']:34} {r['score']}/5 (>= {r['threshold']}) [{r['grader']}] — {r['detail'][:80]}")
    print(f"\n  RESULT: {report['passed']}/{report['scored']} passed"
          f" ({report['skipped']} skipped) → {'PASS' if report['overall_pass'] else 'FAIL'}")

    CASES_DIR.mkdir(parents=True, exist_ok=True)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path = CASES_DIR / f"sets_result_session_{session.number}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ↳ written to {path.relative_to(ROOT)}")
    sys.exit(0 if report["overall_pass"] else 1)
