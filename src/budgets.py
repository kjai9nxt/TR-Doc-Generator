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

WHAT FOLLOWS FROM THE SLIDE CEILING (added when "raise the slide count" did nothing).
A course that set Slides to 30 got a document that FAILED: the recording gate still
read the harness's 40 minutes, and at 1.5 minutes a slide, 30 slides is 45 — so the
repair pass trimmed the document straight back to 26. And the page ceiling stayed at
the default, so each of the extra slides was given LESS text, not more: the content got
thinner exactly when the author had asked for more of it. So two numbers are now
DERIVED from the slide ceiling rather than read from the harness:

    max_minutes   = max(harness ceiling, slides x minutes_per_slide)   — the gate the
                    time grader and the prompt both use;
    max_pages     = front/back matter + slides x (pages a slide costs by default)
                    — unless Pages was set explicitly, in which case that wins.

Every consumer still just reads the dict; `for_session()` remains the only entry point.
"""
from __future__ import annotations
import math

from . import config


def _recording() -> dict:
    return config.harness()["constraints"]["recording"]


def minutes_for(slides: int) -> float | None:
    """Recording minutes `slides` take under the per-slide pacing model (None when the
    word-count model is active and the slide count says nothing about time)."""
    rec = _recording()
    mps = float(rec.get("minutes_per_slide") or 0)
    if rec.get("pacing") != "per_slide" or not mps:
        return None
    return round(int(slides) * mps, 1)


def harness_defaults() -> dict:
    con = config.harness()["constraints"]
    pages, slides, rec = con["pages"], con["slides"], con["recording"]
    return {
        "max_pages": int(pages["max"]),
        "target_pages": int(pages.get("target", pages["max"])),
        "max_slides": int(slides["max"]),
        "min_slides": int(slides["min"]),
        "max_minutes": int(rec["max_minutes"]),
        "target_minutes": int(rec.get("target_minutes", rec["max_minutes"])),
        "minutes_per_slide": (float(rec.get("minutes_per_slide") or 0)
                              if rec.get("pacing") == "per_slide" else None),
        "source": "harness default",
    }


def _derive(out: dict, *, slides_set: bool, pages_set: bool) -> dict:
    """The minutes (always) and the pages (when not set explicitly) a slide ceiling
    implies. Called after the overrides are applied; see the module docstring."""
    d = harness_defaults()
    rec = _recording()
    mps = d.get("minutes_per_slide") or 0
    base_max, base_target = d["max_minutes"], d["target_minutes"]
    out["max_minutes"], out["target_minutes"] = base_max, base_target
    if mps and out["max_slides"] * mps > base_max:
        out["max_minutes"] = int(math.ceil(out["max_slides"] * mps))
        # Keep the safety margin the harness keeps (40 -> 36 is four minutes).
        out["target_minutes"] = max(base_target, out["max_minutes"] - (base_max - base_target))
    if slides_set and not pages_set:
        pages = config.harness()["constraints"]["pages"]
        matter = float(pages.get("front_back_matter_pages", 1.5))
        per_slide = (d["max_pages"] - matter) / max(d["max_slides"], 1)
        out["max_pages"] = max(4, int(math.ceil(matter + out["max_slides"] * per_slide)))
        ratio = d["target_pages"] / d["max_pages"] if d["max_pages"] else 0.88
        out["target_pages"] = max(1, round(out["max_pages"] * ratio))
        out["pages_derived_from_slides"] = True
    return out


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

    slides_set = pages_set = False
    for level, row in (("course default", course_row or {}),
                       ("session override", session_row or {})):
        mp = _clean(row.get("max_pages"), 4, 160)
        ms = _clean(row.get("max_slides"), 3, 120)
        if mp:
            out["max_pages"] = mp
            # The target trails the ceiling by the same proportion the harness uses, so
            # a revision pass keeps the room it was designed to have.
            d = harness_defaults()
            ratio = d["target_pages"] / d["max_pages"] if d["max_pages"] else 0.88
            out["target_pages"] = max(1, round(mp * ratio))
            out["source"] = level
            pages_set = True
        if ms:
            out["max_slides"] = ms
            out["min_slides"] = min(out["min_slides"], ms)
            out["source"] = level
            slides_set = True
    return _derive(out, slides_set=slides_set, pages_set=pages_set)
