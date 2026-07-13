"""Calls the model to draft (and revise) a TR-doc JSON."""
from __future__ import annotations
import json

from . import config, llm

_STRICT_NUDGE = (
    "\n\nIMPORTANT: Respond with STRICT, valid JSON ONLY — no prose before or after it, "
    "and make sure EVERY array item and object property is separated by a comma.")


def _system() -> str:
    # System prompt = generation contract + format spec + style guide, so the
    # model has the full house rules every time (harness engineering).
    return "\n\n".join([
        config.system_prompt(),
        "# FORMAT SPECIFICATION\n" + config.format_spec(),
        "# STYLE GUIDE\n" + config.style_guide(),
    ])


def _complete_json(user_prompt: str, *, tries: int = 2) -> dict:
    """Call the generator and parse its JSON, RETRYING on a parse failure.

    Models occasionally emit slightly malformed JSON (a missing comma, stray
    prose). A fresh sample almost always parses; on the retry we also append a
    strict-JSON nudge. Truncation is NOT retried here (it raises TruncationError
    from llm.complete — a bigger max_tokens is the fix, not a re-sample)."""
    m = config.harness()["model"]
    last = None
    for attempt in range(tries):
        raw = llm.complete(
            system=_system(), user=user_prompt + (_STRICT_NUDGE if attempt else ""),
            model=m["generator"], max_tokens=m["max_tokens"], temperature=m["temperature"],
        )
        try:
            return llm.extract_json(raw)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            llm.log_debug("UNPARSEABLE JSON", raw, extra=f"attempt {attempt + 1}/{tries}: {e}")
    raise RuntimeError(
        f"Model returned unparseable JSON after {tries} attempts ({last}). "
        f"The raw output was saved to logs/llm_debug.log.")


def generate(user_prompt: str) -> dict:
    return _complete_json(user_prompt)


def generate_chunk(base_context: str, instruction: str, approved_json: str = "",
                   reason: str | None = None) -> dict:
    """Generate ONE chunk (opening or a per-takeaway section) for guided mode.

    base_context   shared course/target/memory block (context_builder.build_guided_base)
    instruction    the per-chunk instruction (opening_instruction / takeaway_instruction)
    approved_json   JSON of chunks already approved, for consistency + no repetition
    reason          if set, the human's reason for rejecting the previous attempt —
                    injected so the redo is targeted, not a blind reroll
    """
    approved_block = ""
    if approved_json.strip():
        approved_block = (f"\nALREADY-APPROVED CHUNKS SO FAR (build on these, do NOT "
                          f"repeat them):\n{approved_json}\n")
    regen_block = ""
    if reason:
        regen_block = (f"\nREGENERATE — the human REJECTED your previous version of this "
                       f"chunk for this reason. Address it specifically:\n{reason}\n")
    user_prompt = f"{base_context}\n{approved_block}{regen_block}\n{instruction}"
    return _complete_json(user_prompt)


def revise(user_prompt: str, prev_doc_json: str, issues: list[str]) -> dict:
    """Repair a draft given concrete failures from guardrails + graders."""
    issue_block = "\n".join(f"- {i}" for i in issues)
    revise_prompt = f"""{user_prompt}

You previously produced this draft:
{prev_doc_json}

It FAILED review for these reasons — fix EVERY one, keep everything else intact,
and stay within the 40-minute recording budget:
{issue_block}

Return the corrected TR doc JSON only."""
    return _complete_json(revise_prompt)
