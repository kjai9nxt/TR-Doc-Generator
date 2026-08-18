"""LLM-as-judge grader. Scores a TR-doc JSON against the rubric, 1-5 per
dimension, weighted to /100. The deterministic time estimate is passed in as
ground truth so the judge doesn't have to guess the recording length.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config, llm  # noqa: E402
from guardrails import guardrails  # noqa: E402


def _rubric_text(exclude: tuple = ()) -> str:
    r = config.rubric()
    lines = ["SCALE:"]
    for k, v in r["scale"].items():
        lines.append(f"  {k} = {v}")
    lines.append("\nDIMENSIONS (id, weight, question):")
    for d in r["dimensions"]:
        if d["id"] in exclude:
            continue
        lines.append(f"  [{d['id']}] weight={d['weight']}\n    {d['question'].strip()}")
    lines.append("\nOUTPUT CONTRACT:\n" + r["output_contract"])
    return "\n".join(lines)


JUDGE_SYSTEM = (
    "You are a strict, fair curriculum reviewer grading a TR (Teaching Reference) "
    "doc for a technical course session. Score honestly against the rubric. "
    "Reward technical precision and penalise any factual error hard. "
    "Be DISCRIMINATING: reserve 5 for a dimension with genuinely nothing to improve. "
    "If a dimension is strong but has even a minor nit, give 4 and name the nit in the "
    "justification. Do NOT default to straight 5s — a perfect 100 should be rare. "
    "Return ONLY the JSON described in the output contract."
)


_COUNTABLE_CLAIMS = (
    # (regex over the judge's own words, the deterministic rule it contradicts)
    # Deliberately NARROW. Each pattern matches a claim about a COUNT that a gate has
    # already measured — never a claim about quality. "This bullet list is only one
    # item" is refutable; "this bullet is vague" is the judge's job and is left alone.
    (r"\b(one|two|1|2|single)[\s-]item\b|\bsingle[- ]bullet\b"
     r"|\bbullet list (?:with|of|has) (?:only )?(?:one|two|1|2)\b",
     "bullet_list_sizes"),
    (r"speaker[_ ]notes?\b[^.]{0,80}?\b(three|four|five|3|4|5)\s+sentences"
     r"|\b(three|four|five|3|4|5)\s+sentences\b[^.]{0,40}?speaker[_ ]notes?",
     "speaker_notes_sentences"),
)


def _contradicted(text: str, facts: dict) -> list[str]:
    """Which PASSED deterministic rules this piece of judge prose claims were broken.

    The judge is required to quote its evidence, and nothing verified the claim built on
    that quote. On session 33 it failed a document for a "one-item bullet list" on a
    slide holding four items — the quote was real, the claim about it was not, and no
    slide in that document was under the minimum. A claim about a count the code has
    already taken is not a matter of judgement; it is either true or false, and this
    says which.

    Conservative by construction: a rule is only contradicted when its gate PASSED on
    every slide, so a real violation is never explained away.
    """
    import re
    if not text:
        return []
    passed = {r["field"] for r in facts.get("rules") or [] if r.get("passed")}
    hits = []
    low = str(text).lower()
    for pattern, field in _COUNTABLE_CLAIMS:
        if field in passed and re.search(pattern, low):
            rule = next(r["rule"] for r in facts["rules"] if r["field"] == field)
            hits.append(rule)
    return hits


def grade(doc: dict, session, time_estimate: dict, *, page_estimate: dict | None = None,
          enforce_time: bool = True) -> dict:
    h = config.harness()
    m = h["model"]
    judge_model = m["judge"]
    # When the user turns OFF the 40-minute limit, the recording_time dimension is
    # DROPPED entirely — not shown, not scored, not weighted (the remaining dimensions
    # are renormalised to /100). So the toggle removes it from grading altogether.
    exclude = () if enforce_time else ("recording_time",)
    web_note = ""
    # Live web check for market_parity + content_recency: OpenRouter's ":online"
    # variant gives the judge web search (uses the existing OpenRouter key). Only
    # meaningful for the openrouter provider.
    if m.get("enable_web_market_check") and m.get("provider", "openrouter").lower() == "openrouter":
        if not judge_model.endswith(":online"):
            judge_model = judge_model + ":online"
        web_note = (
            "\n\nWEB CHECK — do this for technical_accuracy FIRST, then market_parity and "
            "content_recency.\n"
            "(a) TECHNICAL ACCURACY: search to VERIFY the document's concrete specifics "
            "rather than judging whether they look plausible — RFC/IEEE/standard numbers, "
            "port numbers, header field names and bit-widths, numeric thresholds and "
            "limits, acronym expansions, complexities and formulas, version numbers, and "
            "attributions. Check the ones that would be embarrassing to teach wrong. For "
            "each value you find to be incorrect, put a `blocking_issues` entry naming the "
            "slide, quoting the wrong value, and giving the verified correct one — that "
            "entry is what the repair pass acts on, so a vague 'some figures look off' is "
            "useless. List in the justification which specifics you checked and confirmed.\n"
            "(b) MARKET PARITY / RECENCY: confirm the topic's mainstream coverage on "
            "GeeksforGeeks, TutorialsPoint and Scaler, and the CURRENT standards/versions. "
            "Penalise anything missing versus mainstream references, and any "
            "deprecated/superseded info presented as current. Note what you verified.")
    # Depth mode (40-min limit off): the doc is INTENDED to be fuller — richer bullets
    # and tables, more thorough sub-concept treatment. Judge clarity/filler, not brevity.
    # Note what depth mode does NOT buy: the page ceiling holds in every mode, and the
    # analogy/worked-example rules are unchanged, so length_discipline is graded normally.
    depth_note = "" if enforce_time else (
        "\n\nDEPTH MODE: the 40-minute limit is OFF, so this doc is deliberately fuller "
        "(fuller bullets and tables, more thorough treatment of each sub-concept). Do NOT "
        "penalise the absence of terse 12-word bullets. For the slide-content-style "
        "dimension, penalise only genuine filler, redundancy, or off-topic text — reward "
        "clear, complete teaching. Still penalise meta-narration in slide content. "
        "The PAGE ceiling applies in this mode too, so grade length_discipline normally: "
        "depth mode buys thoroughness within the page budget, not an unbounded document. "
        "The analogy-placement and worked-example rules are also unchanged here.")
    # Only feed the recording-time estimate when that dimension is actually graded.
    time_block = "" if not enforce_time else (
        "DETERMINISTIC RECORDING-TIME ESTIMATE (ground truth for the recording_time dimension):\n"
        + json.dumps(time_estimate, indent=2) + "\n\n")
    # The page estimate is ground truth in EVERY mode — the page ceiling is the one
    # length limit no mode relaxes. `pages_by_part` is what makes the judgement useful:
    # it separates "long because it covers a lot" from "long because of decoration".
    page_block = "" if not page_estimate else (
        "DETERMINISTIC PAGE-COUNT ESTIMATE (ground truth for the length_discipline "
        "dimension; pages_by_part shows where the length went):\n"
        + json.dumps(page_estimate, indent=2) + "\n\n")
    # WHAT THE CODE ALREADY COUNTED. Same treatment as time and pages, for the countable
    # structure rules — and for the same reason. On session 33 the judge scored
    # slide_content_style 3/5 (below the per-dimension bar, so it failed the run on its
    # own) citing a "one-item bullet list" on a slide that has four, and speaker_notes
    # rule violations on two slides that are correctly shaped. All three rules have
    # deterministic gates that had already PASSED on that document. Handing the judge the
    # measured counts stops it grading a style dimension on arithmetic it gets wrong.
    facts_block = ""
    try:
        facts = guardrails.deterministic_facts(doc)
        verified = [r for r in facts["rules"] if r["passed"]]
        if verified:
            facts_block = (
                "DETERMINISTIC STRUCTURE CHECKS — ALREADY VERIFIED BY CODE (ground truth).\n"
                "Each rule below was checked mechanically against THIS document and passed "
                "on every slide. Do NOT report a violation of one, do not put it in "
                "`blocking_issues`, and do not lower any dimension's score for it — if you "
                "think you see a counter-example, you have miscounted. Grade these "
                "dimensions on judgement (is the writing tight, is the cue useful), never "
                "on the counts.\n"
                + "".join(f"  - PASSED: {r['rule']}\n" for r in verified)
                + "\nPer-slide counts (authoritative — use these instead of counting "
                  "yourself):\n"
                + json.dumps(facts["per_slide"], indent=1) + "\n\n")
    except Exception:
        pass
    # Close the self-evolution loop: the judge is also the VERIFIER that the rules
    # learned from the reviewer's earlier corrections were actually applied. Without
    # this, nothing ever checked, so a rule could be silently ignored on every run
    # and the reviewer had to catch it by hand each time. A violation goes into
    # blocking_issues, which fails the gate -> triggers a revision round -> and is
    # re-learned (bumping that rule's hit count).
    rules_note = ""
    try:
        from src import learning
        block = learning.learned_rules_block()
        if block:
            rules_note = (
                "\n\nREVIEWER-ENFORCED RULES — these were learned from corrections a human "
                "made to EARLIER docs in this course, and the writer was required to follow "
                "them here. Check EACH one against the doc. For every rule that was NOT "
                "followed, add a specific entry to `blocking_issues` naming the rule and "
                "where the doc breaks it. Also reflect the violation in the most relevant "
                "dimension's score. If a rule genuinely does not apply to this doc, ignore "
                "it silently.\n"
                # EVIDENCE REQUIREMENT. A blocking_issue fails the run outright and is then
                # re-learned as a durable rule, so a hallucinated violation does lasting
                # damage. This happened: a compliant doc was failed for putting an analogy
                # on a worked-example slide that had no `analogy` field at all. Rules whose
                # enforcement is now deterministic are no longer sent here (see
                # self_evolution.gated_rules), and the rest require quoted evidence.
                "EVIDENCE REQUIRED: only report a rule violation you can prove by QUOTING "
                "the exact offending text and naming the slide number and JSON field it "
                "came from. If the field you would need to quote is absent from the doc, "
                "the rule was FOLLOWED — say nothing. Never infer a violation from a "
                "slide's topic, title, or role. When in doubt, leave it out: a false "
                "blocking issue discards a correct document and is then learned as a "
                "permanent rule.\n"
                f"\n{block}")
    except Exception:
        pass
    # WHAT EARLIER SESSIONS ALREADY TAUGHT. The judge scores a "no re-teaching a prior
    # session's concept" rule under `coverage`, and it used to do so with no idea what
    # the prior sessions contained — so the one dimension that could catch repetition was
    # judged blind, and the ingested decks influenced nothing at grading time. The taught
    # index (each earlier session's distinct topics, straight out of its deck) is cheap:
    # ~8k characters for a 17-session course, deduplicated.
    taught_note = ""
    try:
        from src import pptx_ingest
        digest = pptx_ingest.taught_digest(session.number)
        if digest.strip():
            taught_note = (
                "\n\nALREADY TAUGHT IN EARLIER SESSIONS (extracted from their actual "
                "decks). Use this for the `coverage` dimension's no-re-teaching rule and "
                "for `pedagogy`:\n"
                "  · A slide that re-introduces, re-defines or re-explains something "
                "listed here is REPETITION — the learner has already sat through it. "
                "Name the slide in `blocking_issues` and cap `coverage` at 3.\n"
                "  · Revisiting a topic to go DEEPER is not repetition and must not be "
                "penalised: if the curriculum's takeaway names it, the session owes it. "
                "Judge whether the slide starts ABOVE what the earlier deck covered.\n"
                "  · A one-line reminder in the Recap is allowed and is not repetition.\n"
                "  · Say in the justification which slides you checked against this.\n"
                f"{digest}")
    except Exception:
        pass

    # The rubric is the same text on every judge call — every document, every retry,
    # every repair round — so it is sent as CACHED context rather than inside the user
    # prompt, where it was re-charged at full price each time. It sits behind the
    # system prompt, which is also static, so the two form one cacheable prefix.
    rubric_block = f"RUBRIC\n{_rubric_text(exclude)}"
    prompt = f"""SESSION KEY TAKEAWAYS (coverage must match these):
{json.dumps(session.key_takeaways, indent=2)}

{time_block}{page_block}{facts_block}TR DOC TO GRADE (JSON):
{json.dumps(doc, ensure_ascii=False, indent=2)}
{web_note}{depth_note}{taught_note}{rules_note}

Grade now. Return only the contract JSON."""
    dims = {d["id"]: d["weight"] for d in config.rubric()["dimensions"]
            if d["id"] not in exclude}

    def _ask(extra: str = "") -> dict:
        raw = llm.complete(
            system=JUDGE_SYSTEM, user=prompt + extra,
            model=judge_model, max_tokens=m.get("judge_max_tokens", 8000),
            temperature=0.0, label="judge", cached_context=rubric_block,
        )
        r = llm.extract_json(raw)
        # Drop the excluded dimension from the scores entirely (not shown/gated).
        for ex in exclude:
            r.get("scores", {}).pop(ex, None)
        return r

    result = _ask()
    missing = _unscored(result, dims)
    if missing:
        # RETRY ONCE. The judge sometimes returns a dimension with a justification but
        # no `score` — observed live on `pedagogy`, whose justification read "Ordering is
        # strong: problem → idea → mechanism". The old code read that as 0 via
        # `.get("score", 0)`, which cost the doc 8 weighted points AND tripped the
        # per-dimension gate, so a document that passed every guardrail at 12/16 pages
        # with 5/5 on eleven dimensions was rejected — and the phantom "scored None < 4"
        # defect was then distilled into a durable learned rule. A missing score is a
        # malformed response, not a verdict of zero. Re-asking is cheap on Haiku.
        llm.log_debug("JUDGE RETURNED UNSCORED DIMENSION(S) — retrying",
                      json.dumps(result.get("scores", {}), indent=2)[:2000],
                      extra=f"missing: {sorted(missing)}")
        retry = _ask()
        if len(_unscored(retry, dims)) < len(missing):
            result, missing = retry, _unscored(retry, dims)

    # VERIFY THE JUDGE AGAINST THE GATES. The facts block above tells it the counts; this
    # checks it used them. A claim about a count the code already took is true or false,
    # not a matter of taste — and on session 33 three such claims were false and one of
    # them (slide_content_style 3/5) discarded a document the reviewer had approved.
    #
    # Two different corrections, because the two failure surfaces differ:
    #   · a false BLOCKING ISSUE is simply dropped — it fails the run by itself, and a
    #     provably false one must not;
    #   · a false claim inside a SCORE's justification cannot be un-scored, so the judge
    #     is re-asked ONCE with the contradiction named. That call only ever happens when
    #     the judge has provably miscounted, and it is far cheaper than discarding a good
    #     document and paying for a repair round that cannot fix a defect that isn't there.
    contradicted: list[dict] = []
    try:
        facts_v = guardrails.deterministic_facts(doc)
    except Exception:
        facts_v = {"rules": []}

    kept_blocking = []
    for b in result.get("blocking_issues") or []:
        bad = _contradicted(str(b), facts_v)
        if bad:
            contradicted.append({"where": "blocking_issues", "claim": str(b)[:300],
                                 "contradicts": bad, "action": "discarded"})
        else:
            kept_blocking.append(b)
    if len(kept_blocking) != len(result.get("blocking_issues") or []):
        result["blocking_issues"] = kept_blocking

    scored_lies = []
    for did, obj in (result.get("scores") or {}).items():
        if not isinstance(obj, dict):
            continue
        bad = _contradicted(str(obj.get("justification") or ""), facts_v)
        if bad:
            scored_lies.append((did, bad))
    if scored_lies:
        names = ", ".join(d for d, _ in scored_lies)
        llm.log_debug("JUDGE CONTRADICTED A PASSED DETERMINISTIC GATE — retrying",
                      json.dumps({d: b for d, b in scored_lies}, indent=2)[:2000],
                      extra=f"dimensions: {names}")
        note = ("\n\nCORRECTION — YOUR PREVIOUS GRADE MISCOUNTED.\n"
                "You reported these rules as broken. Each was measured by code against "
                "this exact document and PASSED on every slide, and the per-slide counts "
                "were given to you above:\n"
                + "".join(f"  - {r}  (cited under '{d}')\n" for d, rs in scored_lies for r in rs)
                + "Re-grade. Do not repeat those claims, and do not let them affect any "
                  "dimension's score. Judge these dimensions on style and usefulness, not "
                  "on counts. Everything else about your grade may stand.")
        retry = _ask(note)
        still = [d for d, _ in scored_lies
                 if _contradicted(str((retry.get("scores") or {}).get(d, {}).get(
                     "justification") or ""), facts_v)]
        for did, bad in scored_lies:
            contradicted.append({
                "where": f"scores.{did}", "contradicts": bad,
                "claim": str((result.get("scores") or {}).get(did, {})
                             .get("justification") or "")[:300],
                "action": "re-graded" if did not in still else "re-grade still disputed"})
        # Only take the retry if it actually stopped repeating the false claims; a retry
        # that repeats them is not an improvement worth swapping a whole grade for.
        if len(still) < len(scored_lies) and not _unscored(retry, dims):
            result = retry
            for ex in exclude:
                result.get("scores", {}).pop(ex, None)
            missing = _unscored(result, dims)
    if contradicted:
        result["contradicted_claims"] = contradicted

    # Recompute the weighted total over the dimensions that were ACTUALLY scored, and
    # renormalise. Counting an unscored dimension as 0 would invent a verdict the judge
    # never gave; excluding it reports the score honestly over what was assessed.
    scored = {did: w for did, w in dims.items() if did not in missing}
    tot_w = sum(scored.values())
    acc = 0.0
    for did, w in scored.items():
        acc += (_score_of(result, did) / 5.0) * w
    result["weighted_total"] = round(acc / tot_w * 100, 1) if tot_w else 0.0
    # Ship the WEIGHTS and the BAR alongside the scores, so a total can be read instead
    # of just seen. Without them "86/100" is a number with no story: the reviewer cannot
    # tell that it is mostly 4-out-of-5s ("strong, negligible issues"), which dimension
    # cost the most, or that a doc needs 90 AND at least 4 everywhere to be accepted.
    # Stored per run, so an old result stays self-describing after the rubric changes.
    gates = config.harness().get("gates", {})
    result["weights"] = scored
    result["gates"] = {
        "min_total": gates.get("rubric_min_total"),
        "min_per_dimension": gates.get("rubric_min_per_dimension"),
    }
    if missing:
        # Surfaced, never silent: the reviewer must be able to see that part of the
        # rubric was not assessed rather than reading a total that looks complete.
        result["unscored_dimensions"] = sorted(missing)
        result.setdefault("suggested_fixes", []).append(
            f"Grader note: the judge returned no score for {sorted(missing)} even after a "
            f"retry, so those dimensions were excluded from the total (scored over "
            f"{len(scored)} of {len(dims)} dimensions). Re-run the grading to assess them.")
    return result


def _score_of(result: dict, did: str):
    obj = (result.get("scores") or {}).get(did) or {}
    return obj.get("score") if isinstance(obj, dict) else None


def _unscored(result: dict, dims: dict) -> set:
    """Active dimensions the judge did not return a usable 1-5 score for."""
    out = set()
    for did in dims:
        sc = _score_of(result, did)
        if not isinstance(sc, (int, float)) or isinstance(sc, bool):
            out.add(did)
    return out


def passes_gates(judge_result: dict) -> tuple[bool, list[str]]:
    gates = config.harness()["gates"]
    reasons = []
    if judge_result["weighted_total"] < gates["rubric_min_total"]:
        reasons.append(
            f"Rubric total {judge_result['weighted_total']} < {gates['rubric_min_total']}.")
    # A dimension the judge never scored is a GRADER failure, not a document failure —
    # it is reported in `unscored_dimensions` and excluded from the total, and must not
    # be gated on. Gating on it rejected a compliant doc for "scored None < 4".
    unscored = set(judge_result.get("unscored_dimensions") or [])
    for did, obj in judge_result.get("scores", {}).items():
        if did in unscored:
            continue
        score = obj.get("score") if isinstance(obj, dict) else None
        if not isinstance(score, (int, float)) or isinstance(score, bool):
            continue
        if score < gates["rubric_min_per_dimension"]:
            reasons.append(f"Dimension '{did}' scored {score} "
                           f"< {gates['rubric_min_per_dimension']}.")
    reasons += [f"Blocking: {b}" for b in judge_result.get("blocking_issues", [])]
    return len(reasons) == 0, reasons
