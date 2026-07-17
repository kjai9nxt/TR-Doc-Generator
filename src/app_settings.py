"""Per-course app settings the user sets at connect time (before the sheet links):

  - reference_date: the "current as of" date so the agent knows the recency
    baseline and keeps the TR doc up to date (no deprecated info as current).
  - course_type: "semester" (deep theoretical dive) or "interview"
    (interview-targeted). EITHER way the doc must help clear interview questions;
    semester additionally goes deeper on theory.

Persisted so the values chosen at connect time survive to generation time.
"""
from __future__ import annotations
import json
from datetime import date

from . import config

STORE = config.KB_DIR / "app_settings.json"
COURSE_TYPES = ("semester", "interview")


def load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def reference_date() -> str:
    """The recency baseline; falls back to today if unset."""
    return load().get("reference_date") or date.today().isoformat()


def course_type() -> str:
    ct = (load().get("course_type") or "semester").lower()
    return ct if ct in COURSE_TYPES else "semester"


def course_name() -> str:
    """Active course grouping label (light multi-course). Defaults to the one
    course the tool has shipped with."""
    return (load().get("course_name") or "Computer Networks").strip()


def save(*, reference_date: str | None = None, course_type: str | None = None,
         course_name: str | None = None) -> dict:
    data = load()
    if reference_date:
        data["reference_date"] = reference_date
    if course_type:
        ct = course_type.lower()
        data["course_type"] = ct if ct in COURSE_TYPES else "semester"
    if course_name:
        data["course_name"] = course_name.strip()
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
