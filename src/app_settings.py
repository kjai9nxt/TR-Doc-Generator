"""Per-course app settings the user sets at connect time (before the sheet links):

  - course_type: "semester" (deep theoretical dive) or "interview"
    (interview-targeted). EITHER way the doc must help clear interview questions;
    semester additionally goes deeper on theory.

Persisted so the values chosen at connect time survive to generation time.
"""
from __future__ import annotations
import json

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


def course_type() -> str:
    ct = (load().get("course_type") or "semester").lower()
    return ct if ct in COURSE_TYPES else "semester"


def course_name() -> str:
    """Active course grouping label (light multi-course). Defaults to the one
    course the tool has shipped with."""
    return (load().get("course_name") or "Computer Networks").strip()


def clear_course_name() -> dict:
    """Forget the active course.

    Needed when that course is DELETED: `course_name()` falls back to a hard-coded
    legacy default, and `save(course_name=...)` ignores an empty string, so without this
    the instance-wide setting would go on naming a course that no longer exists.
    """
    data = load()
    data.pop("course_name", None)
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data


def save(*, course_type: str | None = None,
         course_name: str | None = None) -> dict:
    data = load()
    if course_type:
        ct = course_type.lower()
        data["course_type"] = ct if ct in COURSE_TYPES else "semester"
    if course_name:
        data["course_name"] = course_name.strip()
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return data
