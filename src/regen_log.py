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


def record(session_no, reason: str, before: str, after: str) -> None:
    data = _load()
    data.append({
        "session_no": session_no,
        "reason": (reason or "").strip(),
        "before": (before or "")[:2000],
        "after": (after or "")[:2000],
    })
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data[-_MAX:], ensure_ascii=False, indent=2), encoding="utf-8")


def events(session_no=None) -> list[dict]:
    data = _load()
    if session_no is None:
        return data
    return [e for e in data if e.get("session_no") == session_no]
