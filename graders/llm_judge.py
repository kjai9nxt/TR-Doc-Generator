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


def _rubric_text() -> str:
    r = config.rubric()
    lines = ["SCALE:"]
    for k, v in r["scale"].items():
        lines.append(f"  {k} = {v}")
    lines.append("\nDIMENSIONS (id, weight, question):")
    for d in r["dimensions"]:
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


def grade(doc: dict, session, time_estimate: dict) -> dict:
    h = config.harness()
    m = h["model"]
    judge_model = m["judge"]
    web_note = ""
    # Live web check for market_parity + content_recency: OpenRouter's ":online"
    # variant gives the judge web search (uses the existing OpenRouter key). Only
    # meaningful for the openrouter provider.
    if m.get("enable_web_market_check") and m.get("provider", "openrouter").lower() == "openrouter":
        if not judge_model.endswith(":online"):
            judge_model = judge_model + ":online"
        web_note = (
            "\n\nWEB CHECK (do this for the market_parity and content_recency dimensions): "
            "search the web to confirm (a) the topic's mainstream coverage on GeeksforGeeks, "
            "TutorialsPoint, and Scaler, and (b) the CURRENT standards/versions. Penalise "
            "anything missing versus mainstream references, and any deprecated/superseded "
            "info presented as current. Note in the justification what you verified.")
    prompt = f"""RUBRIC
{_rubric_text()}

SESSION KEY TAKEAWAYS (coverage must match these):
{json.dumps(session.key_takeaways, indent=2)}

DETERMINISTIC RECORDING-TIME ESTIMATE (ground truth for the recording_time dimension):
{json.dumps(time_estimate, indent=2)}

TR DOC TO GRADE (JSON):
{json.dumps(doc, ensure_ascii=False, indent=2)}
{web_note}

Grade now. Return only the contract JSON."""
    raw = llm.complete(
        system=JUDGE_SYSTEM, user=prompt,
        model=judge_model, max_tokens=m.get("judge_max_tokens", 8000),
        temperature=0.0,
    )
    result = llm.extract_json(raw)

    # recompute weighted total defensively from per-dimension scores
    dims = {d["id"]: d["weight"] for d in config.rubric()["dimensions"]}
    tot_w = sum(dims.values())
    acc = 0.0
    for did, w in dims.items():
        sc = result.get("scores", {}).get(did, {}).get("score", 0)
        acc += (sc / 5.0) * w
    result["weighted_total"] = round(acc / tot_w * 100, 1)
    return result


def passes_gates(judge_result: dict) -> tuple[bool, list[str]]:
    gates = config.harness()["gates"]
    reasons = []
    if judge_result["weighted_total"] < gates["rubric_min_total"]:
        reasons.append(
            f"Rubric total {judge_result['weighted_total']} < {gates['rubric_min_total']}.")
    for did, obj in judge_result.get("scores", {}).items():
        if obj.get("score", 0) < gates["rubric_min_per_dimension"]:
            reasons.append(f"Dimension '{did}' scored {obj.get('score')} "
                           f"< {gates['rubric_min_per_dimension']}.")
    reasons += [f"Blocking: {b}" for b in judge_result.get("blocking_issues", [])]
    return len(reasons) == 0, reasons
