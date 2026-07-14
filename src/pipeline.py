"""End-to-end orchestrator: load -> generate -> guardrails -> time -> judge ->
revise (up to N) -> render. This is the 'agent workflow'.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config, course_loader, context_builder, generator, docx_writer  # noqa: E402
from guardrails import guardrails  # noqa: E402
from graders import time_grader, llm_judge  # noqa: E402


def _log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def evaluate(doc: dict, session, is_first: bool, is_last: bool, *, use_judge: bool,
             enforce_time: bool = True):
    """Run all graders/guardrails on a draft. Returns (accepted, report, issues).

    enforce_time=False keeps the recording-time estimate in the report but stops it
    from gating acceptance or triggering a revision (the '40-min limit' UI toggle)."""
    gr = guardrails.check(doc, session, is_first, is_last)
    te = time_grader.estimate(doc)
    report = {"guardrails": gr.as_dict(), "time": te}
    issues = list(gr.failures)
    time_ok = te["within_budget"] or not enforce_time
    if enforce_time and not te["within_budget"]:
        issues.append(
            f"Recording estimate {te['estimated_minutes']} min exceeds the "
            f"{te['max_minutes']} min ceiling — split/trim content.")

    judge_ok = True
    rubric_total = 100
    if use_judge:
        jr = llm_judge.grade(doc, session, te, enforce_time=enforce_time)
        report["judge"] = jr
        rubric_total = jr.get("weighted_total", 0)
        judge_ok, judge_reasons = llm_judge.passes_gates(jr)
        issues += judge_reasons

    accepted = gr.passed and time_ok and judge_ok
    report["accepted"] = accepted
    report["issues"] = issues

    # Revising costs another ~1-2 min LLM call, so only do it when it clearly pays:
    # a HARD gate fails (structure/time), or the rubric is badly below bar.
    hard_fail = (not gr.passed) or (enforce_time and not te["within_budget"])
    revise_floor = config.harness()["gates"].get("rubric_revise_below", 75)
    should_revise = hard_fail or (use_judge and rubric_total < revise_floor)
    return accepted, report, issues, should_revise


def run(session_no: int, *, use_judge: bool = True, course_file=None, do_sync: bool = True,
        enforce_time: bool = True, on_event=None) -> dict:
    def log(msg: str):
        _log(msg)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    # Stay in step with the sheets before generating (if links are configured).
    if do_sync and course_file is None:
        from src import sync
        c_link, d_link = sync.last_links()
        if c_link and d_link:
            try:
                sync.sync(c_link, d_link, verbose=True)
            except Exception as e:
                log(f"⚠ Sheet sync skipped: {e}")

    sessions = course_loader.load_sessions(course_file)
    prev, cur, nxt = course_loader.neighbours(session_no, sessions)
    is_first, is_last = prev is None, nxt is None
    log(f"Session {cur.number}: {cur.name}  ({cur.key_takeaways_count} key takeaways)")

    user_prompt = context_builder.build_user_prompt(prev, cur, nxt)
    if enforce_time:
        user_prompt += (
            "\nHARD TIME LIMIT: the entire session MUST be recordable within 40 minutes "
            "(aim ~36). Be concise and use MORE slides rather than denser ones. Exceeding "
            "40 minutes fails the run.\n")
    else:
        user_prompt += (
            "\n(Recording-time limit relaxed for this run — still write concisely, but "
            "completeness may take priority over the 40-minute budget.)\n")

    log("Generating draft 1 … (this LLM step takes ~1-2 minutes)")
    doc = generator.generate(user_prompt)

    max_rounds = config.harness()["gates"]["max_revision_rounds"]
    history = []
    for rnd in range(max_rounds + 1):
        log(f"Grading draft {rnd + 1} …" + (" (judging quality, ~40s)" if use_judge else ""))
        accepted, report, issues, should_revise = evaluate(
            doc, cur, is_first, is_last, use_judge=use_judge, enforce_time=enforce_time)
        report["round"] = rnd
        history.append(report)
        te = report["time"]
        log(f"Round {rnd}: accepted={accepted} | est={te['estimated_minutes']}min "
             f"| guardrails={'ok' if report['guardrails']['passed'] else 'FAIL'}"
             + (f" | rubric={report.get('judge',{}).get('weighted_total','-')}" if use_judge else ""))
        if not should_revise or rnd == max_rounds:
            break
        log(f"Revising (round {rnd + 1}) to fix {len(issues)} issue(s) … (~1-2 minutes)")
        doc = generator.revise(user_prompt, json.dumps(doc, ensure_ascii=False), issues)

    out = config.harness()["output"]
    fname = out["docx_filename"].format(N=cur.number, SessionName=cur.name)
    safe = fname.replace("/", "-")
    out_dir = config.ROOT / out["dir"]
    docx_path = docx_writer.write_docx(doc, out_dir / safe)
    log(f"Wrote {docx_path}")
    # Persist the doc JSON so the eval-set runner can re-score it without regenerating.
    (out_dir / (safe.rsplit(".", 1)[0] + ".doc.json")).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

    if out.get("also_write_markdown"):
        md = docx_writer.write_markdown(doc, out_dir / (safe.rsplit(".", 1)[0] + ".md"))
        log(f"Wrote {md}")

    if out.get("write_grade_report"):
        rep_path = out_dir / (safe.rsplit(".", 1)[0] + ".grade.json")
        rep_path.write_text(json.dumps({"session": cur.number, "history": history},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Wrote {rep_path}")

    final = history[-1]
    log(f"DONE. accepted={final['accepted']}  "
         f"est_minutes={final['time']['estimated_minutes']}")
    return {"doc": doc, "history": history, "docx": str(docx_path)}


# --------------------------------------------------------------------------- #
# Guided (chunk-by-chunk) mode: assemble approved fragments, then grade + render
# --------------------------------------------------------------------------- #
def assemble_doc(cur, nxt, opening: dict, sections: list[dict]) -> dict:
    """Build the full TR-doc JSON from approved guided chunks + deterministic
    boilerplate. `opening` is {recap, agenda}; `sections` are the inner section
    dicts ({name, slides}) from each takeaway chunk. Section indices are assigned
    here (1..N) so the model never has to track them."""
    doc = {
        "session_no": cur.number,
        "session_title": cur.name,
        "recap": opening.get("recap"),
        "agenda": opening.get("agenda", []),
        "sections": [],
        "key_takeaways": list(cur.key_takeaways),
        "upcoming_session": (nxt.name if nxt else None),
        "closing": "Thank You  |  All the Best",
    }
    for i, sec in enumerate(sections, start=1):
        s = dict(sec)
        s["index"] = i
        doc["sections"].append(s)
    return doc


def finalize(session_no: int, doc: dict, *, use_judge: bool = True, on_event=None) -> dict:
    """Grade an assembled guided doc ONCE (no auto-revise — the human already gated
    each chunk) and render the .docx + .md + grade report. Same result shape as run()."""
    def log(msg: str):
        _log(msg)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    sessions = course_loader.load_sessions(None)
    prev, cur, nxt = course_loader.neighbours(session_no, sessions)
    is_first, is_last = prev is None, nxt is None

    log("Grading the assembled doc …" + (" (judging quality, ~15s)" if use_judge else ""))
    accepted, report, issues, _ = evaluate(doc, cur, is_first, is_last, use_judge=use_judge)
    report["round"] = 0
    history = [report]
    log(f"accepted={accepted} | est={report['time']['estimated_minutes']}min "
        f"| guardrails={'ok' if report['guardrails']['passed'] else 'FAIL'}"
        + (f" | rubric={report.get('judge',{}).get('weighted_total','-')}" if use_judge else ""))

    out = config.harness()["output"]
    safe = out["docx_filename"].format(N=cur.number, SessionName=cur.name).replace("/", "-")
    out_dir = config.ROOT / out["dir"]
    docx_path = docx_writer.write_docx(doc, out_dir / safe)
    log(f"Wrote {docx_path}")
    (out_dir / (safe.rsplit(".", 1)[0] + ".doc.json")).write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    if out.get("also_write_markdown"):
        docx_writer.write_markdown(doc, out_dir / (safe.rsplit(".", 1)[0] + ".md"))
    if out.get("write_grade_report"):
        rep_path = out_dir / (safe.rsplit(".", 1)[0] + ".grade.json")
        rep_path.write_text(json.dumps({"session": cur.number, "history": history},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"DONE. accepted={accepted}  est_minutes={report['time']['estimated_minutes']}")
    return {"doc": doc, "history": history, "docx": str(docx_path)}
