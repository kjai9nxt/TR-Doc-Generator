"""End-to-end orchestrator: load -> generate -> guardrails -> time -> judge ->
revise -> render. This is the 'agent workflow'.

The product entry point is finalize(): the guided flow generates one chunk per key
takeaway, a human approves each, and finalize() grades and renders the assembled
document. run() is the old whole-doc-in-one-call path, kept only for the offline eval
harness (see its docstring) — no user-facing surface reaches it.
"""
from __future__ import annotations
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import (config, course_loader, context_builder, generator, docx_writer,  # noqa: E402
                 patcher)
from guardrails import guardrails  # noqa: E402
from graders import time_grader, page_grader, llm_judge  # noqa: E402


def _log(msg: str):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def judge_always_on() -> bool:
    """The LLM quality check is not optional. Beyond leaving the doc ungraded, skipping
    it also skips the revision loop and the self-evolution step that turns surviving
    defects into durable rules — so the opt-out quietly disabled the agent's learning."""
    return bool(config.harness()["gates"].get("always_run_llm_judge", False))


def time_always_enforced() -> bool:
    """Every session is a 40-minute session. The old toggle could remove the budget
    altogether, which also removed the only length discipline the doc had."""
    return bool(config.harness()["constraints"]["recording"].get("always_enforced", False))


def _score_key(accepted: bool, report: dict) -> tuple:
    """Rank a draft so the loop can keep the BEST one across rounds. Ordered by:
    accepted, then hard gates (guardrails, time, pages), then rubric total, then fewest
    issues. Higher tuple = better.

    With the 40-minute limit OFF the time term is neutral (always 1) — otherwise a
    deliberately fuller depth-mode draft would be ranked below a thinner one for
    busting a budget that does not apply to this run. The PAGE term has no such
    exemption: the page ceiling applies in every mode."""
    rubric = report.get("judge", {}).get("weighted_total", 0) or 0
    gr_ok = 1 if report.get("guardrails", {}).get("passed") else 0
    enforced = report.get("time_enforced", True)
    time_ok = 1 if (not enforced or report.get("time", {}).get("within_budget", True)) else 0
    page_ok = 1 if report.get("pages", {}).get("within_budget", True) else 0
    n_issues = len(report.get("issues", []) or [])
    return (1 if accepted else 0, gr_ok, time_ok, page_ok, rubric, -n_issues)


def evaluate(doc: dict, session, is_first: bool, is_last: bool, *, use_judge: bool,
             enforce_time: bool = True, budgets: dict | None = None):
    """Run all graders/guardrails on a draft. Returns (accepted, report, issues).

    enforce_time=False keeps the recording-time estimate in the report but stops it
    from gating acceptance or triggering a revision (the '40-min limit' UI toggle).
    It also puts generation in DEPTH MODE (richer doc, higher slide ceiling).

    The PAGE budget is gated unconditionally: it is the ceiling the reviewer actually
    reads against, and there is no mode in which a 25-page TR doc is acceptable."""
    if judge_always_on():
        use_judge = True
    if time_always_enforced():
        enforce_time = True
    gr = guardrails.check(doc, session, is_first, is_last, rich=not enforce_time,
                          budgets=budgets)
    te = time_grader.estimate(doc)
    pe = page_grader.estimate(doc, budgets)
    # time_enforced travels with the report so downstream consumers (draft ranking,
    # the UI, the dashboard) know the estimate is informational on this run.
    report = {"guardrails": gr.as_dict(), "time": te, "pages": pe,
              "time_enforced": enforce_time, "budgets": budgets or {}}
    issues = list(gr.failures)
    time_ok = te["within_budget"] or not enforce_time
    if enforce_time and not te["within_budget"]:
        issues.append(
            f"Recording estimate {te['estimated_minutes']} min exceeds the "
            f"{te['max_minutes']} min ceiling — split/trim content.")

    page_gate = config.harness()["gates"].get("pages_within_budget", True)
    page_ok = pe["within_budget"] or not page_gate
    if page_gate and not pe["within_budget"]:
        # Say WHERE the pages went, so the revision pass cuts ritual rather than
        # coverage — the whole point of the ceiling.
        parts = sorted(pe["pages_by_part"].items(), key=lambda kv: -kv[1])[:4]
        breakdown = ", ".join(f"{k} ~{v:.1f}p" for k, v in parts)
        issues.append(
            f"Document is ~{pe['estimated_pages']} pages, over the "
            f"{pe['max_pages']}-page ceiling (target {pe['target_pages']}). Biggest "
            f"consumers: {breakdown}. Cut analogies that are not on a concept_intro "
            f"slide, worked examples the topic does not need, bullets restating a table "
            f"or lead-in, and prose that should be bullets — do NOT drop a sub-concept.")

    judge_ok = True
    rubric_total = 100
    if use_judge:
        jr = llm_judge.grade(doc, session, te, page_estimate=pe, enforce_time=enforce_time)
        report["judge"] = jr
        rubric_total = jr.get("weighted_total", 0)
        judge_ok, judge_reasons = llm_judge.passes_gates(jr)
        issues += judge_reasons

    accepted = gr.passed and time_ok and page_ok and judge_ok
    report["accepted"] = accepted
    report["issues"] = issues

    # Revising costs another ~1-2 min LLM call, so only do it when it clearly pays:
    # a HARD gate fails (structure/time/pages), or the rubric is badly below bar.
    hard_fail = ((not gr.passed) or (enforce_time and not te["within_budget"])
                 or (page_gate and not pe["within_budget"]))
    revise_floor = config.harness()["gates"].get("rubric_revise_below", 75)
    should_revise = hard_fail or (use_judge and rubric_total < revise_floor)
    return accepted, report, issues, should_revise


def run(session_no: int, *, use_judge: bool = True, course_file=None, do_sync: bool = True,
        enforce_time: bool = True, on_event=None, user: str | None = None,
        run_id: str | None = None) -> dict:
    """OFFLINE EVAL ONLY — draft a whole doc in one call, then grade/revise it.

    This is no longer a product path. The one-shot mode it used to serve (the web
    app's "Generate TR Doc" button, the CLI's --session flag) has been removed: every
    real TR doc is now written chunk by chunk with a human approving each one, via
    the guided endpoints and finalize() below. What remains here is the offline
    harness's way to obtain a doc to score (evals/run_eval.py, evals/run_sets.py)
    without a reviewer in the loop — nothing user-facing reaches it.
    """
    # Harness policy wins over the caller: the quality check and the 40-minute budget
    # are not per-run choices any more (gates.always_run_llm_judge,
    # constraints.recording.always_enforced). Forced here as well as in evaluate() so
    # the PROMPT is built for the same mode the doc is graded in.
    if judge_always_on():
        use_judge = True
    if time_always_enforced():
        enforce_time = True

    def log(msg: str):
        _log(msg)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    # Stay in step with the sheet before generating (if a link is configured).
    if do_sync and course_file is None:
        from src import sync
        c_link = sync.last_link()
        if c_link:
            try:
                sync.sync(c_link, verbose=True)
            except Exception as e:
                log(f"⚠ Sheet sync skipped: {e}")

    sessions = course_loader.load_sessions(course_file)
    prev, cur, nxt = course_loader.neighbours(session_no, sessions)
    is_first, is_last = prev is None, nxt is None
    log(f"Session {cur.number}: {cur.name}  ({cur.key_takeaways_count} key takeaways)")

    # Limit ON -> hard 40-min instruction; OFF -> DEPTH MODE (rich generation, no
    # time constraint anywhere in the prompt).
    user_prompt = (context_builder.build_user_prompt(prev, cur, nxt)
                   + context_builder.time_mode_block(enforce_time))

    from src import llm
    # Count tokens/cost against THIS run's meter. Keyed by run so a second generation
    # starting in another thread cannot wipe or inherit this one's accounting — which is
    # what a single process-wide accumulator did on the shared instance (see src/llm.py).
    llm.use_meter(run_id)
    llm.reset_usage(run_id)

    log("Generating draft 1 … (this LLM step takes ~1-2 minutes)")
    doc = generator.generate(user_prompt)

    max_rounds = config.harness()["gates"]["max_revision_rounds"]
    history = []
    # Keep the BEST draft across rounds so a revision that makes things worse can
    # never become the returned doc. best = (score_key, doc, report).
    best = None
    prev_rubric = None
    for rnd in range(max_rounds + 1):
        log(f"Grading draft {rnd + 1} …" + (" (judging quality, ~40s)" if use_judge else ""))
        accepted, report, issues, should_revise = evaluate(
            doc, cur, is_first, is_last, use_judge=use_judge, enforce_time=enforce_time)
        report["round"] = rnd
        history.append(report)
        te, pe = report["time"], report["pages"]
        log(f"Round {rnd}: accepted={accepted} | est={te['estimated_minutes']}min "
             f"| ~{pe['estimated_pages']}p/{pe['max_pages']} "
             f"| guardrails={'ok' if report['guardrails']['passed'] else 'FAIL'}"
             + (f" | rubric={report.get('judge',{}).get('weighted_total','-')}" if use_judge else ""))

        key = _score_key(accepted, report)
        if best is None or key > best[0]:
            best = (key, doc, report)

        if accepted or not should_revise or rnd == max_rounds:
            break
        # Convergence guard: if the previous revision didn't lift the rubric, stop
        # spending slow LLM calls on a plateau — keep the best draft we have.
        cur_rubric = report.get("judge", {}).get("weighted_total")
        if prev_rubric is not None and cur_rubric is not None and cur_rubric <= prev_rubric:
            log(f"No rubric improvement ({prev_rubric}→{cur_rubric}); stopping revisions.")
            break
        prev_rubric = cur_rubric
        log(f"Revising (round {rnd + 1}) to fix {len(issues)} issue(s) … (~1-2 minutes)")
        doc = generator.revise(user_prompt, json.dumps(doc, ensure_ascii=False), issues,
                               enforce_time=enforce_time)

    # Return the best draft seen, not necessarily the last (avoid regressions).
    _, doc, best_report = best
    if best_report is not history[-1]:
        log(f"Keeping best draft (round {best_report['round']}) over a weaker later revision.")

    # --- Self-evolution: distil the defects that SURVIVED the loop into durable,
    # cross-session rules so future sessions don't repeat them. Best-effort. ---
    surviving = best_report.get("issues") or []
    if surviving:
        try:
            from src import learning
            n = learning.learn_from_issues(cur.number, surviving, source="judge")
            if n:
                log(f"Self-evolution: learned {n} new rule(s) from surviving defects "
                    f"→ apply to all future sessions.")
        except Exception as e:
            log(f"⚠ Self-evolution skipped: {e}")

    out = config.harness()["output"]
    fname = out["docx_filename"].format(N=cur.number, SessionName=cur.name)
    safe = fname.replace("/", "-")
    out_dir = config.DATA_ROOT / out["dir"]
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

    final = best_report      # the chosen (best) draft's report, not necessarily the last

    # --- Cost/token accounting for the dashboard (best-effort) ---
    cost = {"totals": llm.usage_totals(run_id), "calls": llm.usage_records(run_id)}
    n_slides = sum(len(sec.get("slides", [])) for sec in doc.get("sections", []))
    try:
        from src import gen_log
        gen_log.record({
            "session_no": cur.number,
            "title": cur.name,
            "user": user,
            "docx": str(docx_path),
            "accepted": final["accepted"],
            "rubric": final.get("judge", {}).get("weighted_total"),
            "est_minutes": final["time"]["estimated_minutes"],
            "est_pages": final["pages"]["estimated_pages"],
            "enforce_time": enforce_time,
            "rounds": len(history),
            "slides": n_slides,
            "cost": cost["totals"],
            "calls": cost["calls"],
        })
    except Exception as e:
        log(f"⚠ Cost logging skipped: {e}")

    c = cost["totals"]
    log(f"DONE. accepted={final['accepted']}  "
        f"est_minutes={final['time']['estimated_minutes']}  "
        f"est_pages={final['pages']['estimated_pages']}/{final['pages']['max_pages']}  "
        f"cost=${c.get('cost', 0):.4f} ({c.get('total_tokens', 0)} tokens)")
    # See finalize(): `final` is the report of the RENDERED doc. history[-1] is the last
    # draft GRADED, which the best-draft rule may have discarded.
    return {"doc": doc, "history": history, "final": final,
            "docx": str(docx_path), "cost": cost}


# --------------------------------------------------------------------------- #
# Guided (chunk-by-chunk) mode: assemble approved fragments, then grade + render
# --------------------------------------------------------------------------- #
def assemble_doc(cur, nxt, opening: dict, sections: list[dict],
                 coverage: list[dict] | None = None) -> dict:
    """Build the full TR-doc JSON from approved guided chunks + deterministic
    boilerplate. `opening` is {recap, agenda}; `sections` are the inner section
    dicts ({name, slides}) from each takeaway chunk; `coverage` are the per-chunk
    {takeaway, sub_concepts} entries, assembled into the doc-level coverage_map.
    Section indices are assigned here (1..N) so the model never has to track them."""
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

    # RENUMBER slides 1..N across the whole document, and carry the coverage map's
    # slide references along with them. Each chunk numbers its slides against the
    # chunks approved at the time, so a regeneration that ADDS or REMOVES a slide left
    # every later section off by one — and a coverage_map entry pointing at a slide
    # number that no longer exists is now a hard guardrail failure, not a cosmetic
    # nit. Remapping here is the only place that can see all the sections at once.
    remap: dict[tuple[int, object], int] = {}
    # Chunks are told to number their slides consecutively after the approved ones, so an
    # `n` a coverage entry cites may belong to ANOTHER section — a legitimate reference to
    # a slide in an earlier section, or a number left stale by a chunk that was
    # regenerated at a different length. Keyed only by (section, n), those were left
    # UNCHANGED while every real slide was renumbered around them, so the map ended up
    # pointing at whatever slide happened to land on that number. Hence a document-wide
    # fallback, used only where the old number is unambiguous.
    seen_n: dict[object, int] = {}
    global_remap: dict[object, int] = {}
    next_n = 1
    for si, sec in enumerate(doc["sections"]):
        for slide in sec.get("slides") or []:
            old = slide.get("n")
            remap[(si, old)] = next_n
            seen_n[old] = seen_n.get(old, 0) + 1
            global_remap[old] = next_n
            slide["n"] = next_n
            next_n += 1
    # An old number reused by two sections cannot be resolved document-wide; drop it and
    # let the coverage gate report the reference rather than guess.
    global_remap = {k: v for k, v in global_remap.items() if seen_n.get(k) == 1}

    if coverage:
        cmap = []
        for si, entry in enumerate(coverage):
            if not isinstance(entry, dict):
                continue
            subs = []
            for sub in entry.get("sub_concepts") or []:
                if not isinstance(sub, dict):
                    continue
                sub = dict(sub)
                if sub.get("slide") not in (None, ""):
                    # A chunk's coverage refers only to that chunk's own slides, so the
                    # (section, old n) key resolves unambiguously.
                    try:
                        old = int(sub["slide"])
                    except (TypeError, ValueError):
                        old = sub["slide"]
                    if (si, old) in remap:
                        sub["slide"] = remap[(si, old)]
                    elif old in global_remap:
                        # Cited a slide outside its own section. Track it to the slide it
                        # actually named instead of leaving a number that now means
                        # something else; the coverage gate then reports it as
                        # out-of-section, which is the truth about the map.
                        sub["slide"] = global_remap[old]
                subs.append(sub)
            cmap.append({"takeaway": entry.get("takeaway"), "sub_concepts": subs})
        doc["coverage_map"] = cmap
    return doc


def _repair_reasons(doc: dict, report: dict) -> list[str]:
    """Why the assembled guided doc must be repaired — or [] to leave it alone.

    Read from the graders' STRUCTURED output, never by matching issue text (the issue
    strings are written for the reviewer and are edited freely). Three admissible
    reasons, all configured under gates.guided_repair_on:

      length      the slide / recording-time / page ceilings — properties of the
                  assembled whole that no single chunk review can see;
      guardrails  a HARD structural failure on the assembled doc (the prose/bullet mix
                  share, an off-agenda slide, a section opening on a detail, a broken
                  coverage reference). Mechanical and unambiguous;
      accuracy    technical_accuracy scored below the per-dimension bar. A wrong RFC
                  number or bit-width is not a matter of the reviewer's taste, and it
                  is the defect that costs most once the session has been recorded.

    Everything else the human approved chunk by chunk, and their judgement stands.
    """
    cfg = config.harness()["gates"].get("guided_repair_on") or {}
    reasons = []
    if cfg.get("length", True):
        reasons += _too_long(doc, report)
    if cfg.get("guardrails", False) and not report.get("guardrails", {}).get("passed", True):
        n = len(report.get("guardrails", {}).get("failures") or [])
        reasons.append(f"{n} structural guardrail failure(s) on the assembled document")
    if cfg.get("technical_accuracy", False):
        bar = config.harness()["gates"].get("rubric_min_per_dimension", 4)
        score = ((report.get("judge") or {}).get("scores") or {}).get(
            "technical_accuracy") or {}
        got = score.get("score")
        if isinstance(got, (int, float)) and got < bar:
            reasons.append(f"technical accuracy scored {got}/5 (needs {bar})")
    return reasons


def _too_long(doc: dict, report: dict) -> list[str]:
    """Which of the three HARD LENGTH ceilings the assembled doc busts.

    Read from the graders' STRUCTURED output, never by matching issue text — the issue
    strings are written for the reviewer and the revision prompt, and are edited freely.
    """
    over = []
    con = config.harness()["constraints"]["slides"]
    ceiling = int((report.get("budgets") or {}).get("max_slides")
                  or (con.get("max_rich", con["max"])
                      if not report.get("time_enforced", True) else con["max"]))
    n_slides = sum(len(sec.get("slides") or []) for sec in doc.get("sections") or [])
    if n_slides > ceiling:
        over.append(f"slide count ({n_slides}/{ceiling})")
    if report.get("time_enforced", True) and not report.get("time", {}).get("within_budget", True):
        over.append(f"recording time ({report['time']['estimated_minutes']}/"
                    f"{report['time']['max_minutes']} min)")
    if (config.harness()["gates"].get("pages_within_budget", True)
            and not report.get("pages", {}).get("within_budget", True)):
        over.append(f"length ({report['pages']['estimated_pages']}/"
                    f"{report['pages']['max_pages']} pages)")
    return over


def finalize(session_no: int, doc: dict, *, use_judge: bool = True,
             enforce_time: bool = True, on_event=None, run_id: str | None = None,
             budgets: dict | None = None) -> dict:
    """Grade an assembled guided doc and render the .docx + .md + grade report.

    This is THE way a TR doc is produced: the chunks were generated one per key
    takeaway and approved by a human, and this assembles, grades and renders them.

    Style and quality opinions are NOT auto-revised here — the human gated each chunk,
    so their judgement stands. Three exceptions, all in _repair_reasons(): a LENGTH
    ceiling (slides / recording time / pages), a hard GUARDRAIL failure, and a
    TECHNICAL ACCURACY score below the per-dimension bar. The first two are properties
    of the ASSEMBLED document that no chunk review can see; the third is not a matter
    of taste at all, and it is the defect that costs most once a session is recorded.
    finalize used to be a dead end for all of them — the doc came back failing gates
    with the review panel already gone, and the only way forward was to pay for a whole
    new guided run. gates.guided_length_repair_rounds bounds the repair and
    gates.guided_repair_on selects what fires it; the best-scoring draft is kept, so a
    repair that makes things worse cannot win.

    enforce_time: when the 40-minute limit is OFF, the recording-time dimension is
    dropped from the rubric and the budget does not gate acceptance (the estimate is
    still reported)."""
    def log(msg: str):
        _log(msg)
        if on_event:
            try:
                on_event(msg)
            except Exception:
                pass

    if judge_always_on():
        use_judge = True
    if time_always_enforced():
        enforce_time = True
    sessions = course_loader.load_sessions(None)
    prev, cur, nxt = course_loader.neighbours(session_no, sessions)
    is_first, is_last = prev is None, nxt is None

    def grade(d: dict, rnd: int):
        acc, rep, iss, _ = evaluate(d, cur, is_first, is_last, use_judge=use_judge,
                                    enforce_time=enforce_time, budgets=budgets)
        rep["round"] = rnd
        log(f"accepted={acc} | est={rep['time']['estimated_minutes']}min"
            f"{'' if enforce_time else ' (40-min limit OFF — not graded on time)'} "
            f"| {rep['time']['slide_count']} slides "
            f"| ~{rep['pages']['estimated_pages']}p/{rep['pages']['max_pages']} "
            f"| guardrails={'ok' if rep['guardrails']['passed'] else 'FAIL'}"
            + (f" | rubric={rep.get('judge',{}).get('weighted_total','-')}" if use_judge else ""))
        return acc, rep, iss

    log("Grading the assembled doc …" + (" (judging quality, ~15s)" if use_judge else ""))
    accepted, report, issues = grade(doc, 0)
    history = [report]

    # --- REPAIR. Bounded, and only for defects a chunk review could not have caught:
    # the assembled document's length, a hard guardrail failure, or a wrong technical
    # fact (see _repair_reasons). Everything else the human already signed off on. ---
    max_repair = int(config.harness()["gates"].get("guided_length_repair_rounds", 1) or 0)
    best = (_score_key(accepted, report), doc, report)
    rnd = 0
    while not accepted and rnd < max_repair:
        over = _repair_reasons(doc, report)
        if not over:
            break
        rnd += 1
        log(f"Repairing {', '.join(over)} — these are properties of the assembled "
            f"document that no single chunk review could see (repair {rnd}/{max_repair}, "
            f"~1-2 min). Coverage is preserved; ritual and off-agenda material are cut.")
        base = (context_builder.build_user_prompt(prev, cur, nxt)
                + context_builder.time_mode_block(enforce_time, budgets=budgets))
        doc_json = json.dumps(doc, ensure_ascii=False)
        # PATCH FIRST. A repair names a handful of defects; asking for the corrected
        # DOCUMENT back costs an output token per word of a document a human already
        # approved — 42,132 of them on session 33, a third of that run's whole cost,
        # and the slowest call in the pipeline. The patch names only what changes, and
        # patcher applies it, so the untouched slides are the same Python objects and
        # cannot drift. A full re-draft stays as the fallback: a patch that will not
        # apply must not silently leave the document unrepaired.
        try:
            patch = generator.repair_patch(doc_json, issues,
                                           enforce_time=enforce_time,
                                           base_context=base)
            doc, psum = patcher.apply_doc_patch(doc, patch)
            log(f"Repair patch: {len(psum['slides_changed'])} slide(s) edited, "
                f"{len(psum['slides_removed'])} removed, {psum['slides_added']} added, "
                f"{len(psum['slides_untouched'])} of {psum['slides_total']} untouched"
                + (f" — {psum['note']}" if psum.get("note") else ""))
        except (patcher.PatchError, ValueError, KeyError, TypeError) as e:
            log(f"Repair patch unusable ({e}) — falling back to a full re-draft.")
            doc = generator.revise(base, doc_json, issues, enforce_time=enforce_time)
        accepted, report, issues = grade(doc, rnd)
        history.append(report)
        key = _score_key(accepted, report)
        if key > best[0]:
            best = (key, doc, report)
    # Never ship a repair that scored worse than what the reviewer approved.
    if best[2] is not history[-1]:
        log(f"Keeping the draft from round {best[2]['round']} — the trim pass did not "
            f"improve on it.")
    _, doc, report = best
    accepted = report["accepted"]

    out = config.harness()["output"]
    safe = out["docx_filename"].format(N=cur.number, SessionName=cur.name).replace("/", "-")
    out_dir = config.DATA_ROOT / out["dir"]
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
    # Cost of THIS guided doc: usage has been accumulating since /api/guided/start
    # (every chunk generation + regeneration), so the totals here cover the whole
    # guided run, not just this grading call.
    from src import llm
    cost = {"totals": llm.usage_totals(run_id), "calls": llm.usage_records(run_id)}
    c = cost["totals"]
    log(f"DONE. accepted={accepted}  est_minutes={report['time']['estimated_minutes']}  "
        f"cost=${c.get('cost', 0) or 0:.4f} ({c.get('total_tokens', 0)} tokens)")
    # `final` is the report for the doc that was actually RENDERED. Callers must not use
    # history[-1] for that: the best draft is not necessarily the last one graded, so
    # reading the tail can describe a draft that was discarded.
    return {"doc": doc, "history": history, "final": report,
            "docx": str(docx_path), "cost": cost}
