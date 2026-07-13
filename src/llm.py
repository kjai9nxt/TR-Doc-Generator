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


# --------------------------------------------------------------------------- #
# OpenAI-compatible providers (OpenRouter)
# --------------------------------------------------------------------------- #
def _complete_openai_compatible(system: str, user: str, *, model: str, max_tokens: int,
                                temperature: float, base_url: str, retries: int) -> str:
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
    if config.harness()["model"].get("prompt_caching"):
        system_content = [{"type": "text", "text": system,
                           "cache_control": {"type": "ephemeral"}}]
    else:
        system_content = system
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "messages": [{"role": "system", "content": system_content},
                     {"role": "user", "content": user}],
    }
    last = None
    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=180)
            if resp.status_code == 200:
                data = resp.json()
                choice = data["choices"][0]
                content = choice["message"]["content"] or ""
                if choice.get("finish_reason") == "length":
                    _log_truncation(provider="openrouter", model=model,
                                    finish_reason="length", max_tokens=max_tokens,
                                    usage=data.get("usage"), content=content)
                    raise _truncation_error(max_tokens)
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
                        temperature: float, retries: int) -> str:
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("Install the SDK:  pip install anthropic") from e
    client = anthropic.Anthropic(api_key=_key_or_raise())
    # Cache the large static system prompt across calls (see note in the
    # OpenRouter path). Native SDK takes cache_control on a system text block.
    if config.harness()["model"].get("prompt_caching"):
        system_param = [{"type": "text", "text": system,
                         "cache_control": {"type": "ephemeral"}}]
    else:
        system_param = system
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
                raise _truncation_error(max_tokens)
            return text
        except TruncationError:
            raise  # retrying would truncate identically — surface it now
        except Exception as e:
            last = e
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM call failed after {retries} retries: {last}")


def complete(system: str, user: str, *, model: str, max_tokens: int,
             temperature: float, retries: int = 3) -> str:
    m = config.harness()["model"]
    provider = m.get("provider", "openrouter").lower()
    if provider == "anthropic":
        return _complete_anthropic(system, user, model=model, max_tokens=max_tokens,
                                   temperature=temperature, retries=retries)
    # openrouter / openai-compatible
    return _complete_openai_compatible(
        system, user, model=model, max_tokens=max_tokens, temperature=temperature,
        base_url=m.get("base_url", "https://openrouter.ai/api/v1"), retries=retries)


# --------------------------------------------------------------------------- #
# JSON extraction (unchanged)
# --------------------------------------------------------------------------- #
def extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model response, tolerating fences."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    return json.loads(text[start:i + 1])
    raise ValueError("No valid JSON object found in model response.")
