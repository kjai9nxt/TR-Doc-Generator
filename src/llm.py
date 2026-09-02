"""Provider-aware LLM wrapper with JSON extraction + retries.

Supports two providers, selected by harness model.provider:
  - "openrouter" (or any OpenAI-compatible endpoint): plain HTTP via requests,
    no extra SDK. Serves Claude Sonnet through OpenRouter.
  - "anthropic": the native Anthropic Messages API SDK.

Model ids, base_url, and the key env var all come from the harness — nothing
hardcoded here.
"""
from __future__ import annotations
import json
import re
import time
from datetime import datetime

import requests

from . import config


class TruncationError(RuntimeError):
    """Raised when the model response was cut off at max_tokens. Not retried —
    a re-run would truncate identically; the fix is a larger max_tokens."""


# How long to wait for one non-streaming completion. A whole TR doc is a single
# large request: the model reasons for thousands of tokens, then writes ~10k tokens
# of JSON, which takes minutes rather than seconds. Generous on purpose — a timeout
# here throws away a generation the model was still producing, and each one costs
# real money.
_REQUEST_TIMEOUT_S = 900


# --------------------------------------------------------------------------- #
# Token/cost accounting. Every complete() call appends a record to the METER of the
# run it belongs to; the pipeline opens a meter at the start of a run and reads the
# total at the end. OpenRouter returns the real dollar cost per call (we request it
# via usage.include), so no price table or second API call is needed.
#
# This USED to be one process-wide list, on the stated assumption of "one generation
# at a time". That assumption died at 1.19, when this became ONE SHARED INSTANCE with
# a worker thread per job: reset_usage() cleared the single list, so a run that
# finished reported everything spent since the most recent START ANYWHERE in the
# process — another user's generation, or the guided run the reviewer abandoned ten
# minutes ago — and the run whose records were wiped reported a fraction of its own
# cost. Two S30 guided runs came back at $2.85 and $2.06 for the same session, with
# three abandoned runs of the same session interleaved between them.
#
# So usage is now keyed by RUN. A run spans several threads (guided: generate-all,
# each regenerate, finalize), which is why the key cannot be the thread — each thread
# doing LLM work declares which run it is working for via use_meter(run_id), and
# _record_usage writes to that run's bucket.
# --------------------------------------------------------------------------- #
import threading as _threading

_DEFAULT_METER = "__default__"          # CLI / eval runs that never open a meter
_METERS: dict[str, list[dict]] = {_DEFAULT_METER: []}
_METER_ORDER: list[str] = []            # insertion order, for trimming
_METERS_LOCK = _threading.Lock()
_MAX_METERS = 64                        # a long-lived server must not grow forever
_current = _threading.local()           # this thread's active meter id


def use_meter(run_id: str | None) -> None:
    """Point THIS THREAD's usage accounting at `run_id`'s meter.

    Call it first in every thread that makes LLM calls on behalf of a run. A guided
    run spans several threads, so the meter follows the run, not the thread.
    """
    _current.mid = run_id or _DEFAULT_METER
    _open_meter(_current.mid)


def _open_meter(run_id: str) -> None:
    with _METERS_LOCK:
        if run_id not in _METERS:
            _METERS[run_id] = []
            _METER_ORDER.append(run_id)
        while len(_METER_ORDER) > _MAX_METERS:
            _METERS.pop(_METER_ORDER.pop(0), None)


def _meter_id(run_id: str | None = None) -> str:
    return run_id or getattr(_current, "mid", None) or _DEFAULT_METER


def reset_usage(run_id: str | None = None) -> None:
    """Start counting a run's tokens from zero.

    Only ever clears the named run's own bucket, so opening a second run can no
    longer erase the first one's accounting.
    """
    mid = _meter_id(run_id)
    _open_meter(mid)
    with _METERS_LOCK:
        _METERS[mid] = []
    _current.mid = mid


def seed_meter(run_id: str, records: list | None) -> int:
    """Prime a run's meter with usage it already paid for. Returns how many were taken.

    WHY A METER HAS TO BE RESTORABLE. `_METERS` is process memory. A guided run spans a
    long human review, and on an ephemeral host the process does not survive it — which
    is the whole reason `_guided_rehydrate` exists. Generation records were therefore
    lost on every restart, and because the DB write REPLACES a run's `calls_json` rather
    than appending, the next write — the judge at finalize — overwrote the six correct
    generation rows with whatever had happened since the restart.

    The result was a finished document reporting the cost of its grading and none of its
    writing: seven calls, no `generate_chunk` among them, and a total understated by most
    of the run. Worse, it read as a real finding — "the generator model is never used" —
    because the one row using it was a repair pass.

    Restored ONLY when the meter is empty. A rehydrate that raced a live thread would
    otherwise double-count everything that thread had already recorded.
    """
    mid = run_id or _DEFAULT_METER
    _open_meter(mid)
    rows = [r for r in (records or []) if isinstance(r, dict)]
    if not rows:
        return 0
    with _METERS_LOCK:
        if _METERS.get(mid):
            return 0
        _METERS[mid] = rows
    return len(rows)


def close_usage(run_id: str) -> None:
    """Drop a finished run's meter (best-effort; keeps a long-lived server tidy)."""
    with _METERS_LOCK:
        _METERS.pop(run_id, None)
        if run_id in _METER_ORDER:
            _METER_ORDER.remove(run_id)


def usage_records(run_id: str | None = None) -> list[dict]:
    with _METERS_LOCK:
        return list(_METERS.get(_meter_id(run_id), ()))


def usage_totals(run_id: str | None = None) -> dict:
    """Aggregate the records collected for this run since its last reset.

    `unpriced_calls` COUNTS WHAT THE TOTAL CANNOT INCLUDE. The dollar figure is the
    provider's own — OpenRouter returns `usage.cost`, which already carries the
    prompt-cache discount and the `:online` web-search surcharge, so nothing here has to
    keep a price table that could drift out of date. The native Anthropic SDK returns no
    cost field at all, and `sum(r.get("cost") or 0)` turned that into a confident
    $0.0000: a run that had spent real money reported none, and there was no way to tell
    that total from a run that genuinely cost nothing. Counting the unpriced calls is
    what lets a reader tell "this cost nothing" from "nobody told us what this cost".
    """
    recs = usage_records(run_id)
    return {
        "prompt_tokens": sum(r.get("prompt_tokens") or 0 for r in recs),
        "completion_tokens": sum(r.get("completion_tokens") or 0 for r in recs),
        "total_tokens": sum(r.get("total_tokens") or 0 for r in recs),
        "cost": round(sum(r.get("cost") or 0.0 for r in recs), 6),
        "calls": len(recs),
        "unpriced_calls": sum(1 for r in recs if r.get("cost") is None),
        "cached_prompt_tokens": sum(r.get("cached_prompt_tokens") or 0 for r in recs),
    }


def _record_usage(label: str, model: str, usage: dict | None) -> None:
    """Append one call's usage to the calling thread's meter. Tolerates both
    OpenAI-style (prompt_tokens) and Anthropic-style (input_tokens) keys; cost is
    present only for OpenRouter."""
    if not usage:
        return
    prompt = usage.get("prompt_tokens", usage.get("input_tokens"))
    completion = usage.get("completion_tokens", usage.get("output_tokens"))
    total = usage.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    # Cached input is billed at a fraction of the normal rate, and how much of the
    # prompt was READ FROM CACHE is the single most useful number for understanding
    # why a guided run costs what it does — keep it when the provider reports it.
    details = usage.get("prompt_tokens_details") or {}
    cached = (details.get("cached_tokens") if isinstance(details, dict) else None)
    if cached is None:
        cached = usage.get("cache_read_input_tokens")
    rec = {
        "label": label, "model": model,
        "prompt_tokens": prompt, "completion_tokens": completion,
        "total_tokens": total, "cost": usage.get("cost"),
    }
    if cached is not None:
        rec["cached_prompt_tokens"] = cached
    mid = _meter_id()
    _open_meter(mid)
    with _METERS_LOCK:
        _METERS[mid].append(rec)


def _log_truncation(*, provider: str, model: str, finish_reason, max_tokens: int,
                    usage, content: str) -> None:
    """Record a truncated (max_tokens) response to logs/llm_debug.log so the cause
    is visible instead of surfacing later as an opaque 'No valid JSON' parse error.
    Logging must never crash the run, hence the broad guard."""
    try:
        logs_dir = config.ROOT / "logs"
        logs_dir.mkdir(exist_ok=True)
        entry = (
            f"\n===== {datetime.now().isoformat(timespec='seconds')} TRUNCATED RESPONSE =====\n"
            f"provider={provider}  model={model}  finish_reason={finish_reason}  "
            f"max_tokens={max_tokens}\n"
            f"usage={usage}\n"
            f"content_chars={len(content)} (response was cut off at the token ceiling)\n"
            f"--- last 500 chars of the incomplete response ---\n"
            f"{content[-500:]}\n"
            f"===== END =====\n"
        )
        with open(logs_dir / "llm_debug.log", "a", encoding="utf-8") as f:
            f.write(entry)
    except Exception:
        pass


def log_debug(title: str, text: str, extra: str = "") -> None:
    """Append a diagnostic entry (e.g. an unparseable model response) to
    logs/llm_debug.log. Never raises — logging must not break a run."""
    try:
        logs_dir = config.ROOT / "logs"
        logs_dir.mkdir(exist_ok=True)
        with open(logs_dir / "llm_debug.log", "a", encoding="utf-8") as f:
            f.write(f"\n===== {datetime.now().isoformat(timespec='seconds')} {title} =====\n"
                    f"{extra}\n{text[-2000:]}\n===== END =====\n")
    except Exception:
        pass


def _truncation_error(max_tokens: int) -> TruncationError:
    return TruncationError(
        f"Model output was TRUNCATED — it hit the max_tokens ceiling ({max_tokens}), "
        f"so the JSON is incomplete and cannot be parsed. Increase model.max_tokens "
        f"in harness/harness.yaml. Details saved to logs/llm_debug.log.")


def _key_or_raise() -> str:
    key = config.api_key()
    if not key:
        env = config.harness().get("model", {}).get("api_key_env", "OPENROUTER_API_KEY")
        raise RuntimeError(f"No API key found. Set {env} in the .env file.")
    return key


def _system_blocks(system: str, system_extra: str = "", cached_context: str = ""):
    """The `system` payload: cached static block(s) + an optional uncached tail.

    Both providers accept a list of text blocks here, and both take cache_control on
    a block. When caching is off we just concatenate, since there is nothing to
    protect from invalidation.

    `cached_context` is run-constant material the caller wants CACHED — in practice the
    guided base context (course + target session + the summary of every prior deck),
    which measured 10,430 tokens for Session 30 and was sitting in the USER message, so
    it was re-billed at full price on all six chunk calls plus every regeneration. It
    goes in its own block AFTER the static system prompt (so both stay cacheable as a
    prefix) and BEFORE `system_extra`, which is deliberately uncached because the
    reviewer's learned rules change whenever feedback is given.
    """
    extra = (system_extra or "").strip()
    ctx = (cached_context or "").strip()
    if not config.harness()["model"].get("prompt_caching"):
        return "\n\n".join(x for x in (system, ctx, extra) if x)
    blocks = [{"type": "text", "text": system,
               "cache_control": {"type": "ephemeral"}}]
    if ctx:
        blocks.append({"type": "text", "text": ctx,
                       "cache_control": {"type": "ephemeral"}})
    if extra:
        blocks.append({"type": "text", "text": extra})
    return blocks


# --------------------------------------------------------------------------- #
# OpenAI-compatible providers (OpenRouter)
# --------------------------------------------------------------------------- #
def _complete_openai_compatible(system: str, user: str, *, model: str, max_tokens: int,
                                temperature: float, base_url: str, retries: int,
                                label: str = "", system_extra: str = "",
                                cached_context: str = "") -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {_key_or_raise()}",
        "Content-Type": "application/json",
        # OpenRouter attribution headers (optional but recommended):
        "HTTP-Referer": "https://local.tr-doc-generator",
        "X-Title": "TR Doc Generator",
    }
    # The system prompt (contract + format spec + style guide) is large and
    # identical across generate/revise (and separately across judge calls).
    # A cache_control breakpoint lets Anthropic reuse it (5-min TTL) instead of
    # reprocessing ~10k tokens every call — cheaper, and faster to first token.
    #
    # system_extra goes in a SEPARATE block AFTER the breakpoint: the learned rules
    # need system-level authority, but they change whenever the reviewer gives
    # feedback, so putting them inside the cached block would invalidate the cache
    # on every correction. This way the big static prefix stays cached and only the
    # short volatile tail is re-read.
    system_content = _system_blocks(system, system_extra, cached_context)
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_content},
                     {"role": "user", "content": user}],
        # Ask OpenRouter to include the real dollar cost in the response usage.
        "usage": {"include": True},
    }
    last = None
    for attempt in range(retries):
        try:
            # 180s was tuned when a doc took ~90s to write. It no longer holds:
            # Claude Sonnet 5 thinks by default (adaptive thinking is on when the
            # `thinking` field is omitted, unlike Sonnet 4.6), so a full TR doc now
            # spends thousands of reasoning tokens before the first character of JSON
            # and a single generation runs several minutes. Note `requests`' timeout is
            # per-socket-read, not wall clock, so this is a floor rather than a
            # guarantee — a generation that legitimately runs long is not an error.
            resp = requests.post(url, headers=headers, json=payload,
                                 timeout=_REQUEST_TIMEOUT_S)
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"]["content"] or ""
                if choice.get("finish_reason") == "length":
                    _log_truncation(provider="openrouter", model=model,
                                    finish_reason="length", max_tokens=max_tokens,
                                    usage=data.get("usage"), content=content)
                    # METERED BEFORE IT IS RAISED. A truncated response is the most
                    # expensive call shape in the pipeline — it spent the entire prompt
                    # and the whole output ceiling, which for the generator is 64k
                    # tokens — and it was recorded as nothing at all, because the raise
                    # came before the only line that meters. The caller then retries or
                    # re-drafts, and THAT call was the only one billed in the report. A
                    # run that truncated once therefore understated its own cost by
                    # roughly a full generation, and the money was already spent.
                    _record_usage(f"{label} (truncated)", model, data.get("usage"))
                    raise _truncation_error(max_tokens)
                _record_usage(label, model, data.get("usage"))
                return content
            last = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:400]}")
            if resp.status_code in (400, 401, 403, 404):
                break  # not transient — stop retrying
        except TruncationError:
            raise  # retrying would truncate identically — surface it now
        except Exception as e:
            last = e
        time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed: {last}")


# --------------------------------------------------------------------------- #
# native Anthropic SDK
# --------------------------------------------------------------------------- #
def _complete_anthropic(system: str, user: str, *, model: str, max_tokens: int,
                        temperature: float, retries: int, label: str = "",
                        system_extra: str = "", cached_context: str = "") -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("Install the SDK:  pip install anthropic") from e
    client = anthropic.Anthropic(api_key=_key_or_raise())
    # Cache the large static system prompt across calls (see note in the
    # OpenRouter path). Native SDK takes cache_control on a system text block.
    system_param = _system_blocks(system, system_extra, cached_context)
    last = None
    for attempt in range(retries):
        try:
            resp = client.messages.create(
                model=model, max_tokens=max_tokens, temperature=temperature,
                system=system_param, messages=[{"role": "user", "content": user}])
            text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text")
            if getattr(resp, "stop_reason", None) == "max_tokens":
                _log_truncation(provider="anthropic", model=model,
                                finish_reason="max_tokens", max_tokens=max_tokens,
                                usage=getattr(resp, "usage", None), content=text)
                _u = getattr(resp, "usage", None)
                if _u is not None:                  # billed, so it is metered
                    _record_usage(f"{label} (truncated)", model,
                                  {"input_tokens": getattr(_u, "input_tokens", None),
                                   "output_tokens": getattr(_u, "output_tokens", None)})
                raise _truncation_error(max_tokens)
            u = getattr(resp, "usage", None)
            if u is not None:
                # Native SDK has no cost field; record tokens (cost stays None).
                _record_usage(label, model, {"input_tokens": getattr(u, "input_tokens", None),
                                             "output_tokens": getattr(u, "output_tokens", None)})
            return text
        except TruncationError:
            raise  # retrying would truncate identically — surface it now
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {retries} retries: {last}")


def complete(system: str, user: str, *, model: str, max_tokens: int,
             temperature: float, retries: int = 3, label: str = "",
             system_extra: str = "", cached_context: str = "") -> str:
    """`system_extra` is appended to the system prompt as a separate, UNCACHED block
    — for instructions that must carry system-level authority but change between
    calls (the reviewer-enforced rules)."""
    m = config.harness()["model"]
    provider = m.get("provider", "openrouter").lower()
    if provider == "anthropic":
        return _complete_anthropic(system, user, model=model, max_tokens=max_tokens,
                                   temperature=temperature, retries=retries, label=label,
                                   system_extra=system_extra,
                                   cached_context=cached_context)
    # openrouter / openai-compatible
    return _complete_openai_compatible(
        system, user, model=model, max_tokens=max_tokens, temperature=temperature,
        base_url=m.get("base_url", "https://openrouter.ai/api/v1"), retries=retries,
        label=label, system_extra=system_extra, cached_context=cached_context)


# --------------------------------------------------------------------------- #
# JSON extraction (unchanged)
# --------------------------------------------------------------------------- #
def _repair_json(src: str) -> str:
    """The two defects a model's JSON actually has, repaired. Best effort, never clever.

    Both are things a strict parser rejects and a reader would not notice:

      · a TRAILING COMMA before `}` or `]`, which JSON forbids and most other formats
        allow;
      · a RAW NEWLINE inside a string literal. This is the one that bites here. The
        judge quotes the document back — a slide's content, a speaker note — and the
        moment a quoted passage contains a line break the string is unterminated, the
        parser resynchronises on the next token and reports something like
        `Expecting ',' delimiter: line 25 column 768`. The message names a comma; the
        cause is a newline several hundred characters earlier.

    Anything else is left alone. A repair that guesses at structure would turn a
    malformed grade into a confidently wrong one, which is worse than a failed parse.
    """
    out, in_str, esc = [], False, False
    for ch in src:
        if in_str:
            if esc:
                out.append(ch)
                esc = False
                continue
            if ch == "\\":
                out.append(ch)
                esc = True
                continue
            if ch == '"':
                in_str = False
                out.append(ch)
                continue
            # A literal newline or tab inside a string: escape it rather than drop it,
            # so the quoted evidence survives intact.
            if ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                pass
            elif ch == "\t":
                out.append("\\t")
            else:
                out.append(ch)
            continue
        if ch == '"':
            in_str = True
        out.append(ch)
    fixed = "".join(out)
    return re.sub(r",(\s*[}\]])", r"\1", fixed)


def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response, tolerating fences.

    Three attempts, in order of how much they assume: the whole string, the outermost
    balanced object in it, and then the same object with the two known model defects
    repaired (see `_repair_json`). A JSONDecodeError from the last of those is raised as
    itself, because its message names the position and callers log it.
    """
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    blob = text[start:i + 1]
                    try:
                        return json.loads(blob)
                    except json.JSONDecodeError:
                        return json.loads(_repair_json(blob))
        # Unbalanced: the response was cut off. Repair what is there in case the
        # damage is only a stray newline inside a string.
        return json.loads(_repair_json(text[start:]))
    raise ValueError("No valid JSON object found in model response.")
