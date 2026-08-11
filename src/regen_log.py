"""Log Guided-mode regeneration events (before + reason + after) so the
`feedback_regeneration_adherence` eval set can score whether each redo actually
addressed the user's stated reason. Runtime data — gitignored, capped in size."""
from __future__ import annotations
import json

from . import config

STORE = config.KB_DIR / "regen_events.json"
_MAX = 100


def _load() -> list[dict]:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def record(session_no, reason: str, before: str, after: str,
           scope: dict | None = None) -> None:
    """`scope` is the applied-patch summary from src.patcher (which slides changed,
    which were left untouched, the share of the section touched) — or {"mode": "full"}
    when the patch path fell back to a whole-chunk re-draft. It is what lets the
    regeneration_scope_discipline eval judge whether the edit stayed inside the
    reviewer's complaint instead of rewriting content they had accepted."""
    data = _load()
    data.append({
        "session_no": session_no,
        "reason": (reason or "").strip(),
        "before": (before or "")[:2000],
        "after": (after or "")[:2000],
        "scope": scope or {},
    })
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data[-_MAX:], ensure_ascii=False, indent=2), encoding="utf-8")
    # Same reason as learning._save(): on an ephemeral host this file is wiped when the
    # instance spins down, and the sync-time backup may never run in between — which
    # would leave the feedback_regeneration_adherence eval set with nothing to score.
    try:
        from . import db
        db.kb_put(STORE.name)
    except Exception:
        pass


def events(session_no=None) -> list[dict]:
    data = _load()
    if session_no is None:
        return data
    return [e for e in data if e.get("session_no") == session_no]
