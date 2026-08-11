"""Calls the model to draft (and revise) a TR-doc JSON."""
from __future__ import annotations
import json

from . import config, llm

_STRICT_NUDGE = (
    "\n\nIMPORTANT: Respond with STRICT, valid JSON ONLY — no prose before or after it, "
    "and make sure EVERY array item and object property is separated by a comma.")

_SHRINK_NUDGE = (
    "\n\nYOUR PREVIOUS ATTEMPT WAS CUT OFF because it was too long to finish. Produce the "
    "SAME JSON but SMALLER so it completes: use FEWER slides (3-4 is fine for one key "
    "takeaway), keep every bullet under 12 words, and keep speaker_notes to 2-3 sentences "
    "per slide. Completeness of the JSON matters more than volume of prose.")


def _system() -> str:
    # System prompt = generation contract + format spec + style guide, so the
    # model has the full house rules every time (harness engineering).
    return "\n\n".join([
        config.system_prompt(),
        "# FORMAT SPECIFICATION\n" + config.format_spec(),
        "# STYLE GUIDE\n" + config.style_guide(),
    ])


def _learned() -> str:
    """The reviewer-enforced rules, read FRESH on every call.

    Read here rather than baked into the prompt text by the caller: guided mode
    freezes its base_context at /guided/start, so a rule learned from the reviewer's
    feedback on chunk 2 was missing from the prompt for chunks 3..N — the model
    repeated, in the same session, the exact mistake it had just been corrected on.
    Building it per call means feedback applies from the very next chunk onward.
    """
    try:
        from . import learning
        return learning.learned_rules_block()
    except Exception:
        return ""


def _complete_json(user_prompt: str, *, tries: int = 2, label: str = "generate") -> dict:
    """Call the generator and parse its JSON, RETRYING on a parse failure.

    Models occasionally emit slightly malformed JSON (a missing comma, stray
    prose). A fresh sample almost always parses; on the retry we also append a
    strict-JSON nudge. Truncation is NOT retried here (it raises TruncationError
    from llm.complete — a bigger max_tokens is the fix, not a re-sample); the one
    exception is a single guided chunk, see generate_chunk."""
    m = config.harness()["model"]
    last = None
    for attempt in range(tries):
        raw = llm.complete(
            system=_system(), system_extra=_learned(),
            user=user_prompt + (_STRICT_NUDGE if attempt else ""),
            model=m["generator"], max_tokens=m["max_tokens"], temperature=m["temperature"],
            label=label,
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
    try:
        return _complete_json(user_prompt, label="generate_chunk")
    except llm.TruncationError:
        # A whole-doc truncation is unrecoverable (re-sampling truncates the same
        # way), but ONE chunk that ran long is: it only has to cover a single key
        # takeaway, so asking for fewer, tighter slides genuinely fits. Worth the
        # retry — an unhandled truncation here used to abandon a guided run that
        # already had several paid chunks in it.
        llm.log_debug("CHUNK TRUNCATED — retrying with a concision nudge", user_prompt[-800:])
        return _complete_json(
            user_prompt + _SHRINK_NUDGE, tries=1, label="generate_chunk_retry")


def generate_patch(base_context: str, kind: str, prev_fragment: dict,
                   reason: str) -> dict:
    """Ask for a SURGICAL PATCH to one already-generated chunk.

    Returns the raw patch dict; src.patcher applies it. Kept separate from
    generate_chunk because the two have opposite goals: that one writes content, this
    one writes the smallest possible description of a change.
    """
    prev_json = json.dumps(prev_fragment, ensure_ascii=False, indent=2)
    from . import context_builder
    prompt = (f"{base_context}\n"
              + context_builder.patch_instruction(kind, prev_json, reason))
    return _complete_json(prompt, label="regenerate_patch")


def revise(user_prompt: str, prev_doc_json: str, issues: list[str],
           *, enforce_time: bool = True) -> dict:
    """Repair a draft given concrete failures from guardrails + graders.

    When enforce_time is False the 40-minute budget is not a constraint, so we do
    NOT tell the model to trim to it (that would fight depth mode)."""
    issue_block = "\n".join(f"- {i}" for i in issues)
    budget_line = (" and stay within the 40-minute recording budget" if enforce_time
                   else "")
    revise_prompt = f"""{user_prompt}

You previously produced this draft:
{prev_doc_json}

It FAILED review for these reasons — fix EVERY one, keep everything else intact{budget_line}:
{issue_block}

Return the corrected TR doc JSON only."""
    return _complete_json(revise_prompt, label="revise")
