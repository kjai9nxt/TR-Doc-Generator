"""How long a TR doc may be — per course, and per session when one needs different.

WHY THIS EXISTS. The page ceiling and the slide ceiling were single numbers in the
harness, applied to every document of every course. That is right as a default and
wrong as a rule: a semester course covering heavy theory is not the same shape as an
interview-prep course, and inside one course the odd session genuinely needs more room
(or much less) than its neighbours. Editing harness.yaml to change it is not something
a curriculum author should have to do, and it changes it for everyone.

So there are three levels, each falling back to the one below:

    session override   →   course default   →   harness default

Nothing else in the pipeline had to learn where a number came from: every consumer asks
here and gets a plain dict. `for_session()` is the only entry point worth calling.

Deliberately NOT overridable: the recording pace (1.5 min/slide) and the per-slide word
budget, which are measurements of how recording actually works rather than preferences.
The slide ceiling is what a course chooses; the minutes those slides take follow from it.
"""
from __future__ import annotations

from . import config


def harness_defaults() -> dict:
    con = config.harness()["constraints"]
    pages, slides = con["pages"], con["slides"]
    return {
        "max_pages": int(pages["max"]),
        "target_pages": int(pages.get("target", pages["max"])),
        "max_slides": int(slides["max"]),
        "min_slides": int(slides["min"]),
        "source": "harness default",
    }


def _clean(v, lo: int, hi: int):
    """A budget must be a sane integer: a stray 0 or 5000 from a form field would
    otherwise become a ceiling no document could satisfy (or none could bust)."""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


def for_session(course: str | None = None, session_no: int | None = None) -> dict:
    """The budgets that apply to one document.

    Falls back cleanly at every level, including when there is no database at all —
    an offline eval process gets the harness numbers and never knows the difference.
    """
    out = harness_defaults()
    if not course:
        return out
    try:
        from . import db
        course_row = db.course_settings(course)
        session_row = db.session_settings(course, session_no) if session_no else {}
    except Exception:
        return out

    for level, row in (("course default", course_row or {}),
                       ("session override", session_row or {})):
        mp = _clean(row.get("max_pages"), 4, 120)
        ms = _clean(row.get("max_slides"), 3, 120)
        if mp:
            out["max_pages"] = mp
            # The target trails the ceiling by the same proportion the harness uses, so
            # a revision pass keeps the room it was designed to have.
            d = harness_defaults()
            ratio = d["target_pages"] / d["max_pages"] if d["max_pages"] else 0.88
            out["target_pages"] = max(1, round(mp * ratio))
            out["source"] = level
        if ms:
            out["max_slides"] = ms
            out["min_slides"] = min(out["min_slides"], ms)
            out["source"] = level
    return out
