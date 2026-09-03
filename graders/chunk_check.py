"""THE DETERMINISTIC CHECKS, RUN WHILE THE REVIEW PANEL IS STILL OPEN.

    from graders import chunk_check
    findings = chunk_check.review(state, index)

WHY THIS EXISTS. Every deterministic gate in this project ran in exactly one place:
`pipeline.finalize`, after a human had approved all six chunks. That is the most
expensive moment to learn anything. A length overrun or a structural failure found
there costs a bounded repair pass that EDITS SLIDES THE REVIEWER ALREADY SIGNED OFF,
with the review panel gone; the same defect found at chunk 2 costs one regenerate of
one section, while the reviewer is looking at it.

Nothing here is new judgement. It is the same `guardrails.check`, the same
`time_grader`, the same `page_grader`, reading the same harness config — run earlier.
Two consequences worth being explicit about:

  · THE FINAL CHECK IS UNCHANGED AND REMAINS THE AUTHORITY. A chunk cannot see the
    document, and roughly a third of the gates are about the whole: agenda equals the
    takeaways, one section per takeaway in order, the recap carries the previous
    session's agenda, the same thing is not taught in two DIFFERENT sections, the
    coverage map resolves, slides run 1..N, the totals. Those can only be answered at
    the end, and the perspective genuinely changes once the document exists. This is an
    early warning, never a replacement.
  · A CHUNK IS WRAPPED IN A SYNTHETIC ONE-TAKEAWAY DOCUMENT rather than checked by a
    second, chunk-shaped copy of the rules. `synthetic()` below builds a structurally
    complete document containing this section alone, and a session whose curriculum is
    this section's one takeaway. Every doc-level gate is then either satisfied by
    construction (the agenda IS the takeaway, verbatim; there IS one section per
    takeaway, named after it) or genuinely meaningful at this scale ("does this section
    teach the sub-topics its own curriculum line promised?"). Only the four gates that
    measure a proportion or a total over the whole document are dropped, by
    `scope="chunk"` — see guardrails.check for the list and the reasoning.

So there is ONE implementation of every rule. A rule cannot warn one thing at review
and fail another thing at finalize, which is the failure a separate chunk checker would
have shipped on its first divergent edit.
"""
from __future__ import annotations

import re
import sys
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardrails import guardrails                      # noqa: E402
from graders import page_grader, time_grader           # noqa: E402
from src import config, pipeline                       # noqa: E402


def _section_of(fragment: dict) -> dict | None:
    """The section dict inside a takeaway chunk, whichever shape it arrived in."""
    if not isinstance(fragment, dict):
        return None
    sec = fragment.get("section")
    if isinstance(sec, dict) and sec.get("slides"):
        return sec
    return fragment if fragment.get("slides") else None


def synthetic(fragment: dict, session, takeaway_index: int):
    """(doc, session) that put ONE section through the document-level gates.

    The wrapper is built to satisfy every structural gate BY CONSTRUCTION, so a failure
    that comes back is about the section's content and never about the wrapper:

      · agenda and key_takeaways are the section's own curriculum line, verbatim — so
        the agenda-equals-takeaway gate compares a string to itself;
      · there is exactly one section, named after the one takeaway, at index 1 — so the
        one-section-per-takeaway gate and the coverage gate are satisfied;
      · is_first/is_last are both True at the call site, which is what switches off the
        recap and upcoming-session gates: a chunk has neither and owes neither.

    SLIDE NUMBERS ARE LEFT ALONE. A chunk numbers its slides after the chunks approved
    at the time, so they do not start at 1 — and its `coverage` entries cite those
    numbers. Renumbering here to please the 1..N gate would invalidate every coverage
    reference and produce a page of failures about a wrapper this module built itself.
    The 1..N gate is dropped in chunk scope for exactly that reason.
    """
    sec = _section_of(fragment)
    if sec is None:
        return None, None
    kts = list(getattr(session, "key_takeaways", []) or [])
    if not (0 <= takeaway_index < len(kts)):
        return None, None
    takeaway = kts[takeaway_index]
    # THE AGENDA ITEM CARRIES ITS NUMBER. Two separate gates read this list: one compares
    # the text to the curriculum line on NORMALISED lines (so a numbering prefix cannot
    # be what makes them differ), and one requires every item to be numbered at all. A
    # bare takeaway satisfies the first and fails the second — which is a defect in this
    # wrapper, not in the section, and it fired on all four golden sections until the
    # test below caught it. Curriculum lines that already carry their own number are left
    # alone rather than numbered twice.
    agenda_item = (takeaway if re.match(r"^\s*\d+\s*[.)\-:]", takeaway)
                   else f"{takeaway_index + 1}. {takeaway}")
    one = dict(sec)
    one["index"] = 1
    # The section's NAME is a gate of its own (it must be the takeaway line verbatim),
    # so it is deliberately NOT overwritten here — a section named wrongly must still
    # fail, and that is one of the cheapest things to fix at review.
    doc = {
        "session_no": getattr(session, "number", None),
        "session_title": getattr(session, "name", ""),
        "recap": None,
        "agenda": [agenda_item],
        "sections": [one],
        "key_takeaways": [agenda_item],
        "upcoming_session": None,
        "closing": "Thank You  |  All the Best",
        "coverage_map": [fragment.get("coverage")] if fragment.get("coverage") else [],
    }
    return doc, replace(session, key_takeaways=[takeaway],
                        prev_key_takeaways=[], next_key_takeaways=list(
                            getattr(session, "next_key_takeaways", []) or []))


def gates(fragment: dict, session, takeaway_index: int, *, rich: bool = False,
          budgets: dict | None = None, course: str | None = None,
          profile: dict | None = None, skills: list | None = None):
    """Run the gates over one takeaway chunk. Returns a GuardrailResult, or None.

    None means "not checkable", not "clean" — the opening chunk has no slides, and a
    fragment whose takeaway cannot be resolved is a caller bug rather than a defect in
    the document. Reporting either as a pass is the one outcome that would make this
    worse than not running at all.
    """
    doc, one_session = synthetic(fragment, session, takeaway_index)
    if doc is None:
        return None
    return guardrails.check(doc, one_session, True, True, rich=rich, budgets=budgets,
                            course=course, profile=profile, skills=skills,
                            scope="chunk")


def running_length(cur, nxt, fragments: list[dict], *, budgets: dict | None = None,
                   sections_total: int | None = None) -> dict:
    """How much of the length budget the chunks so far have spent — and the projection.

    THE GAP THIS FILLS. Recording time and page count were computed in exactly one
    place: finalize. So a reviewer approved six sections with no idea they were at 22 of
    26 pages, and the overrun arrived after the last approval, when the only remedy left
    was a repair pass over work they had already accepted.

    Assembled through `pipeline.assemble_doc`, the same function finalize uses, so the
    numbers are produced the way the real ones will be — renumbered, coverage remapped —
    rather than by summing fragments and hoping that is the same thing.

    The PROJECTION is deliberately the crudest possible: spent-so-far scaled by the
    share of sections written. It is honest about being an extrapolation, and it is the
    number that actually changes a decision at chunk 2, when precision is worthless and
    direction is everything.
    """
    if not fragments:
        return {}
    opening = fragments[0] if isinstance(fragments[0], dict) else {}
    sections, coverage = [], []
    for f in fragments[1:]:
        sec = _section_of(f)
        if sec is not None:
            sections.append(sec)
            coverage.append(f.get("coverage") or {})
    if not sections:
        return {}
    try:
        doc = pipeline.assemble_doc(cur, nxt, opening, sections, coverage)
        pe = page_grader.estimate(doc, budgets)
        te = time_grader.estimate(doc)
    except Exception:
        return {}
    done = len(sections)
    total = int(sections_total or len(getattr(cur, "key_takeaways", []) or []) or done)
    pages_cfg = config.harness()["constraints"]["pages"]
    matter = float(pages_cfg.get("front_back_matter_pages", 1.5))
    out = {
        "sections_done": done, "sections_total": total,
        "slides": te.get("slide_count"),
        "pages": pe.get("estimated_pages"), "pages_max": pe.get("max_pages"),
        "minutes": te.get("estimated_minutes"), "minutes_max": te.get("max_minutes"),
        "over_pages": not pe.get("within_budget", True),
        "over_time": not te.get("within_budget", True),
    }
    if 0 < done < total:
        # Front/back matter is a fixed cost that is already in the figure, so it is taken
        # out before scaling and added back — scaling it would inflate the projection by
        # a page and a half of boilerplate the document only pays for once.
        body = max(float(out["pages"] or 0) - matter, 0.0)
        out["projected_pages"] = round(matter + body * total / done, 1)
        out["projected_minutes"] = round(float(out["minutes"] or 0) * total / done, 1)
    return out
