"""Append-only log of every generation run — the data source for the
approved-TR-docs dashboard and per-generation cost tracking.

One record per pipeline.run(), holding the outcome (accepted, rubric, est time)
plus the token/dollar cost captured from the LLM client (src/llm.usage_*). Read
by the server's /api/dashboard endpoint. Plain JSON you can inspect by hand.
"""
from __future__ import annotations
import json
from datetime import datetime
from pathlib import Path

from . import config

STORE = config.OUTPUTS_DIR / "generation_log.json"
_MAX = 500          # keep the log bounded; oldest trimmed first


def _load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"runs": []}


def record(entry: dict) -> dict:
    """Append one run record (a timestamp is added here). Never raises — cost
    logging must not break a generation."""
    try:
        entry = {"ts": datetime.now().isoformat(timespec="seconds"), **entry}
        data = _load()
        data["runs"].append(entry)
        data["runs"] = data["runs"][-_MAX:]
        STORE.parent.mkdir(parents=True, exist_ok=True)
        STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    return entry


def runs() -> list[dict]:
    """All run records, newest first."""
    return list(reversed(_load().get("runs", [])))
