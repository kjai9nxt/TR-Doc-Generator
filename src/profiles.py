"""What a good TR doc looks like — PER COURSE.

WHY THIS EXISTS. All of it was one set of numbers in harness.yaml, applied to every
course on the instance, and several of them are plainly about the course the tool
shipped with:

  · `market_reference_platforms` is Scaler, GeeksforGeeks, TutorialsPoint, JavaTpoint and
    "standard university CN/CS syllabi" — so a React document was graded for market
    parity against a networking syllabus;
  · `slide_roles.values` has no role for a code walkthrough, and the analogy rule is a
    BICONDITIONAL keyed on those roles, so a course could not add one;
  · `content.*` sets one prose density for a semester theory course and a code-along;
  · the rubric's weights and pass bar are identical for a course whose worked examples
    are address translations and one whose worked examples are snippets.

Two levels, the lower one always winning where it says anything:

    course profile   →   harness default

Deliberately shaped like src/budgets.py, which already does exactly this for the page
and slide ceilings, so there is ONE pattern for per-course configuration rather than two.
Every consumer asks here and gets a plain dict; nothing downstream learns where a number
came from.

WHAT MAY BE OVERRIDDEN IS A CLOSED WHITELIST. A profile that can set anything is a
config-injection surface, and a failure message built from arbitrary user config is
unmaintainable. Two rules are enforced beyond the whitelist, both for the same reason —
a gate a course can switch off is a gate that gets switched off:

  · rubric weight may be REDISTRIBUTED but the pass bar may not be LOWERED;
  · a new slide role must come with its analogy rule, or the biconditional has a hole
    exactly where the new role is.
"""
from __future__ import annotations

from . import config

COURSE_TYPES = ("semester", "interview")
DOC_KINDS = ("theory", "code_along")


def harness_defaults() -> dict:
    """The instance-wide profile: what every course gets when it says nothing."""
    h = config.harness()
    con = h["constraints"]
    rub = config.rubric()
    return {
        "source": "harness default",
        "course_type": "semester",
        "doc_kind": "theory",
        "market_reference_platforms": list(h.get("market_reference_platforms") or []),
        "slide_roles": dict(con.get("slide_roles") or {}),
        "analogy": dict(con.get("analogy") or {}),
        "content": dict(con.get("content") or {}),
        "worked_example": dict(con.get("worked_example") or {}),
        "recording": dict(con.get("recording") or {}),
        "rubric_weights": {d["id"]: d["weight"] for d in rub["dimensions"]},
        "gates": dict(h.get("gates") or {}),
        "model": dict(h.get("model") or {}),
    }


# Which keys a course may set, and how each is checked. Anything not named here is
# refused outright rather than stored and silently ignored.
_SIMPLE = {
    "course_type": COURSE_TYPES,
    "doc_kind": DOC_KINDS,
}
_MERGEABLE = ("slide_roles", "analogy", "content", "worked_example", "recording",
              "gates", "model")
_ALLOWED = set(_SIMPLE) | set(_MERGEABLE) | {"market_reference_platforms",
                                             "rubric_weights"}

# Gates a course may tighten but never loosen. The direction is the point: a course that
# can set rubric_min_total to 60 has turned the quality bar off.
_ONLY_STRICTER = ("rubric_min_total", "rubric_min_per_dimension")


def validate(overrides) -> tuple[bool, dict, str]:
    """(ok, cleaned, why). `cleaned` is what should be stored — never the raw input."""
    if overrides in (None, {}):
        return True, {}, ""
    if not isinstance(overrides, dict):
        return False, {}, "a profile must be an object"

    unknown = sorted(set(overrides) - _ALLOWED)
    if unknown:
        return False, {}, (f"unknown profile key(s): {', '.join(unknown)}. A profile may "
                           f"set {', '.join(sorted(_ALLOWED))}.")

    base = harness_defaults()
    cleaned: dict = {}

    for key, allowed in _SIMPLE.items():
        if key not in overrides:
            continue
        v = str(overrides[key] or "").strip().lower()
        if v not in allowed:
            return False, {}, f"{key} must be one of {', '.join(allowed)}, not {v!r}"
        cleaned[key] = v

    if "market_reference_platforms" in overrides:
        v = overrides["market_reference_platforms"]
        if not isinstance(v, list) or not all(str(x).strip() for x in v) or not v:
            return False, {}, ("market_reference_platforms must be a non-empty list of "
                               "platform names")
        cleaned["market_reference_platforms"] = [str(x).strip() for x in v]

    if "rubric_weights" in overrides:
        v = overrides["rubric_weights"]
        if not isinstance(v, dict) or not v:
            return False, {}, "rubric_weights must be an object of dimension -> weight"
        known = set(base["rubric_weights"])
        for did, w in v.items():
            if did not in known:
                return False, {}, (f"rubric_weights names {did!r}, which is not a rubric "
                                   f"dimension. Known: {', '.join(sorted(known))}.")
            try:
                n = float(w)
            except (TypeError, ValueError):
                return False, {}, f"rubric_weights[{did}] must be a number"
            if not 0 < n <= 40:
                return False, {}, (f"rubric_weights[{did}] is {n} — a weight is between "
                                   f"0 and 40. Redistribute, do not zero a dimension out.")
        cleaned["rubric_weights"] = {k: float(w) for k, w in v.items()}

    for key in _MERGEABLE:
        if key not in overrides:
            continue
        v = overrides[key]
        if not isinstance(v, dict):
            return False, {}, f"{key} must be an object"
        cleaned[key] = dict(v)

    # --- the two rules that are not about shape ---------------------------------
    for gate in _ONLY_STRICTER:
        if gate not in (cleaned.get("gates") or {}):
            continue
        try:
            want = float(cleaned["gates"][gate])
        except (TypeError, ValueError):
            return False, {}, f"gates.{gate} must be a number"
        floor = float(base["gates"].get(gate) or 0)
        if want < floor:
            return False, {}, (f"gates.{gate} may be raised but not lowered: the harness "
                               f"requires {floor:g} and this asks for {want:g}. A course "
                               f"that can lower the bar is a course with no bar.")

    roles = (cleaned.get("slide_roles") or {}).get("values")
    if roles is not None:
        if not isinstance(roles, list) or not roles:
            return False, {}, "slide_roles.values must be a non-empty list"
        added = [r for r in roles if r not in base["slide_roles"].get("values", [])]
        if added:
            a_cfg = {**base["analogy"], **(cleaned.get("analogy") or {})}
            covered = set(a_cfg.get("required_on_roles") or []) | \
                set(a_cfg.get("banned_on_roles") or [])
            missing = [r for r in added if r not in covered]
            if missing:
                return False, {}, (
                    f"new slide role(s) {', '.join(missing)} have no analogy rule. An "
                    f"analogy is REQUIRED on some roles and BANNED on the rest, and that "
                    f"is a biconditional — a role in neither list is a hole in it. Add "
                    f"each to analogy.required_on_roles or analogy.banned_on_roles.")
    return True, cleaned, ""


def for_course(course: str | None = None) -> dict:
    """The profile that applies to one course, merged over the harness defaults.

    Falls back cleanly at every level, including when there is no database at all — an
    offline eval process gets the harness numbers and never knows the difference.
    """
    out = harness_defaults()
    if not course:
        return out
    try:
        from . import db
        overrides = db.course_profile(course)
    except Exception:
        return out
    if not overrides:
        return out

    for key, value in overrides.items():
        if key in _MERGEABLE and isinstance(value, dict):
            merged = dict(out.get(key) or {})
            merged.update(value)
            out[key] = merged
        elif key == "rubric_weights" and isinstance(value, dict):
            merged = dict(out["rubric_weights"])
            merged.update({k: float(v) for k, v in value.items()})
            out["rubric_weights"] = merged
        elif key in _ALLOWED:
            out[key] = value
    out["source"] = "course profile"
    return out
