"""Self-evolution: a persistent, per-course store of LEARNED RULES.

Feedback the human gives (a regeneration reason) and hard defects the judge flags
(blocking issues) are distilled into short, durable rules and saved to
`knowledge_base/learned_rules.json`. Every future generation for this course injects
these rules into its prompt (see context_builder.build_guided_base), so the same
mistake is not repeated across sessions — the agent visibly improves as it is used.

Deliberately simple and TRANSPARENT: rules are plain text you can read, edit, or
delete by hand. No fine-tuning, no hidden state.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import config

STORE = config.KB_DIR / "learned_rules.json"
_MAX_RULES = 40            # keep the injected block small; oldest trimmed first
_MAX_RULE_LEN = 200


def _load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"rules": []}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def rules() -> list[dict]:
    return _load().get("rules", [])


def add_rule(text: str, *, source: str, session_no=None) -> bool:
    """Add a durable rule (deduped by normalised text). Returns True if newly added."""
    text = (text or "").strip()
    if not text:
        return False
    if len(text) > _MAX_RULE_LEN:
        text = text[:_MAX_RULE_LEN].rstrip() + "…"
    data = _load()
    existing = {_norm(r["text"]) for r in data["rules"]}
    if _norm(text) in existing:
        return False
    data["rules"].append({"text": text, "source": source, "session_no": session_no})
    data["rules"] = data["rules"][-_MAX_RULES:]     # trim oldest
    _save(data)
    return True


def record_feedback(session_no, reason: str, *, source: str = "feedback") -> bool:
    """A human reason for rejecting/regenerating content -> a durable preference."""
    return add_rule(reason, source=source, session_no=session_no)


def record_issues(session_no, issues: list[str], *, source: str = "judge") -> int:
    """Persist hard defects (judge blocking issues) as rules VERBATIM (no distil)."""
    n = 0
    for i in issues or []:
        if add_rule(str(i), source=source, session_no=session_no):
            n += 1
    return n


def _self_evo_cfg() -> dict:
    try:
        return config.harness().get("self_evolution", {}) or {}
    except Exception:
        return {}


def distill_rule(issue: str) -> str:
    """Rewrite one concrete grader/judge failure into a short, GENERAL, reusable
    DO/DON'T rule for future generations. Best-effort: on any LLM error it returns
    the raw issue text so learning still happens (just less polished)."""
    from . import llm
    m = config.harness()["model"]
    try:
        out = llm.complete(
            system=(
                "You convert a single QA failure from a teaching-document generator into "
                "ONE short, GENERAL, imperative rule (a DO or DON'T) that would prevent the "
                "same CLASS of mistake next time. Output one line only, no preamble, <=160 "
                "chars, no session-specific nouns/numbers, no quotes."),
            user=f"QA failure:\n{issue}\n\nReusable rule:",
            model=m.get("judge", m["generator"]), max_tokens=120, temperature=0.0,
            label="distill")
        line = (out or "").strip().splitlines()[0].strip().lstrip("-•*").strip().strip('"')
        return line or issue
    except Exception:
        return issue


def learn_from_issues(session_no, issues: list[str], *, source: str = "judge") -> int:
    """Self-evolution entry point: distil the defects that SURVIVED the revision loop
    into durable, cross-session rules. Honors harness `self_evolution` config
    (enabled / learn_from_judge / distill). Returns the number of NEW rules added."""
    cfg = _self_evo_cfg()
    if not cfg.get("enabled", True) or not cfg.get("learn_from_judge", True):
        return 0
    do_distill = cfg.get("distill", True)
    n = 0
    for raw in issues or []:
        text = distill_rule(str(raw)) if do_distill else str(raw)
        if add_rule(text, source=source, session_no=session_no):
            n += 1
    return n


def learned_rules_block() -> str:
    """Formatted block injected into generation prompts. Empty string if no rules."""
    rs = rules()
    if not rs:
        return ""
    lines = "\n".join(f"- {r['text']}" for r in rs)
    return ("=== LEARNED PREFERENCES (from feedback on earlier docs in THIS course — "
            "apply every one) ===\n" + lines + "\n")
