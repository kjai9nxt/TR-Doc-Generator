"""Calls the model to draft (and revise) a TR-doc JSON."""
from __future__ import annotations
import json

from . import config, llm

_STRICT_NUDGE = (
    "\n\nIMPORTANT: Respond with STRICT, valid JSON ONLY — no prose before or after it, "
    "and make sure EVERY array item and object property is separated by a comma.")

_SHRINK_NUDGE = (
    "\n\nYOUR PREVIOUS ATTEMPT WAS CUT OFF because it was too long to finish. Produce the "
    "SAME JSON but SMALLER so it completes: use FEWER slides — never more than the slide "
    "budget stated above — keep every bullet under 12 words, and keep speaker_notes to 2 "
    "sentences per slide. Completeness of the JSON matters more than volume of prose.")


def _system() -> str:
    # System prompt = generation contract + format spec + style guide, so the
    # model has the full house rules every time (harness engineering).
    return "\n\n".join([
        config.system_prompt(),
        "# FORMAT SPECIFICATION\n" + config.format_spec(),
        "# STYLE GUIDE\n" + config.style_guide(),
    ])


def _learned(course: str | None = None, session=None) -> str:
    """This course's brief and reviewer-enforced rules, read FRESH on every call.

    Read here rather than baked into the prompt text by the caller: guided mode
    freezes its base_context at /guided/start, so a rule learned from the reviewer's
    feedback on chunk 2 was missing from the prompt for chunks 3..N — the model
    repeated, in the same session, the exact mistake it had just been corrected on.
    Building it per call means feedback applies from the very next chunk onward.

    COURSE MUST BE PASSED. Omitted, learned_rules_block falls back to
    app_settings.course_name() — ONE instance-wide setting, whichever course anybody
    selected last. Every other input to a run is already resolved per run (the
    curriculum, the decks, the profile, the prerequisites), and this was the one that
    was not: a document generated for course B while the instance pointed at course A
    was written under A's course-scoped rules ("use 'cluster' instead of 'block'") and
    A's authored brief, and never saw B's own skills at all. Two people on one instance
    is all it takes, and the failure is silent — the document reads fine, it is simply
    written to the wrong course's rules.

    SESSION MUST BE PASSED TOO, for the same reason one step narrower. A course may write
    a brief for one of its sessions — a flow that session needs, an example it must use,
    a correction review made on it. Without the session number the resolver cannot tell
    which of those apply, so it returns none of them and the session brief is authored,
    approved, and never used.
    """
    try:
        from . import learning
        return learning.learned_rules_block(course, session)
    except Exception:
        return ""


def _complete_json(user_prompt: str, *, tries: int = 2, label: str = "generate",
                   cached_context: str = "", course: str | None = None,
                   session=None) -> dict:
    """Call the generator and parse its JSON, RETRYING on a parse failure.

    Models occasionally emit slightly malformed JSON (a missing comma, stray
    prose). A fresh sample almost always parses; on the retry we also append a
    strict-JSON nudge. Truncation is NOT retried here (it raises TruncationError
    from llm.complete — a bigger max_tokens is the fix, not a re-sample); the one
    exception is a single guided chunk, see generate_chunk.

    `cached_context` is run-constant context to send as a CACHED system block rather
    than inside the user message — see generate_chunk."""
    m = config.harness()["model"]
    last = None
    for attempt in range(tries):
        raw = llm.complete(
            system=_system(), system_extra=_learned(course, session),
            cached_context=cached_context,
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


def generate(user_prompt: str, *, course: str | None = None, session=None) -> dict:
    return _complete_json(user_prompt, course=course, session=session)


def generate_chunk(base_context: str, instruction: str, approved_json: str = "",
                   reason: str | None = None, *, course: str | None = None,
                   session=None) -> dict:
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
    # base_context (course + target session + the summary of every prior deck) is
    # IDENTICAL for every chunk of a run and measured 10,430 tokens for S30. Sent inside
    # the user message it was re-billed in full on all six chunk calls and on every
    # regeneration — the single largest reason a guided doc cost ~3x a one-shot one. As a
    # cached system block, chunks 2..N read it from cache instead.
    user_prompt = f"{approved_block}{regen_block}\n{instruction}"
    try:
        return _complete_json(user_prompt, label="generate_chunk",
                              cached_context=base_context, course=course,
                              session=session)
    except llm.TruncationError:
        # A whole-doc truncation is unrecoverable (re-sampling truncates the same
        # way), but ONE chunk that ran long is: it only has to cover a single key
        # takeaway, so asking for fewer, tighter slides genuinely fits. Worth the
        # retry — an unhandled truncation here used to abandon a guided run that
        # already had several paid chunks in it.
        llm.log_debug("CHUNK TRUNCATED — retrying with a concision nudge", user_prompt[-800:])
        return _complete_json(
            user_prompt + _SHRINK_NUDGE, tries=1, label="generate_chunk_retry",
            cached_context=base_context, course=course, session=session)


def generate_patch(base_context: str, kind: str, prev_fragment: dict,
                   reason: str, *, course: str | None = None, session=None) -> dict:
    """Ask for a SURGICAL PATCH to one already-generated chunk.

    Returns the raw patch dict; src.patcher applies it. Kept separate from
    generate_chunk because the two have opposite goals: that one writes content, this
    one writes the smallest possible description of a change.
    """
    prev_json = json.dumps(prev_fragment, ensure_ascii=False, indent=2)
    from . import context_builder
    # Same cached block as generate_chunk, so a regeneration during review re-reads the
    # base context from cache instead of paying for all 10k tokens of it again.
    prompt = context_builder.patch_instruction(kind, prev_json, reason)
    return _complete_json(prompt, label="regenerate_patch",
                          cached_context=base_context, course=course, session=session)


def repair_patch(prev_doc_json: str, issues: list[str], *,
                 enforce_time: bool = True, base_context: str | None = None,
                 course: str | None = None, session=None) -> dict:
    """Ask for a SURGICAL PATCH to the assembled document. src.patcher applies it.

    The repair counterpart of generate_patch, and it exists for the same two reasons:
    the document that comes back should not be a fresh sampling of 21 slides a human
    already approved, and asking for the whole document back costs an output token per
    word of it. On session 33 the one `revise` call spent 42,132 output tokens — $0.48,
    a third of that run's entire cost — to fix a handful of defects; the patch calls in
    the same run averaged ~1,700.

    `base_context` is the same cached block the chunk generators use, so the repair
    reads the course context from cache instead of paying for it again — `revise` was
    the only generator that never passed it.
    """
    from . import context_builder
    prompt = context_builder.repair_instruction(prev_doc_json, issues,
                                                enforce_time=enforce_time)
    return _complete_json(prompt, label="repair_patch", cached_context=base_context,
                          course=course, session=session)


def revise(user_prompt: str, prev_doc_json: str, issues: list[str],
           *, enforce_time: bool = True, course: str | None = None,
           session=None) -> dict:
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
    return _complete_json(revise_prompt, label="revise", course=course,
                          session=session)
