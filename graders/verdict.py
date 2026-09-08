"""THE COMBINED VERDICT — one answer per source the document is written against.

    from graders import verdict
    v = verdict.build(report)
    print(v["text"])

WHY THIS EXISTS. Every one of these checks already ran, and their answers were already
in the report — spread across the guardrail failure list, the time and page estimates,
the rubric, and the per-skill rows. So "did this document respect the curriculum?" was
answerable only by reading nineteen failure strings and knowing which of them were about
the curriculum. This says it in five lines.

It ADDS NO JUDGEMENT. Nothing here re-decides anything: `accepted` is still the release
gate, the graders still produce every number, and turning this off would change no
verdict. It is a projection of the report onto the five things a TR doc is held to.

NOT PARSED FROM FAILURE TEXT. Each guardrail failure carries the source it was enforcing
(guardrails._Tagged, set once per gate block), and the skill rows carry their own
verdicts. src/pipeline._repair_reasons says why that matters: the issue strings are
written for the reviewer and reworded freely, so reading them for meaning is a bug
waiting for the next edit.

THE FIVE SOURCES, and what each one is answered from:

  Curriculum     the coverage gates — one agenda item per takeaway verbatim, one section
                 each in order, every sub-topic the curriculum line names, the coverage
                 map, nothing off the agenda and nothing belonging to the next session
  Course Profile the course's own DNA — its slide-role vocabulary, its analogy rule, its
                 prose density, its worked-example policy, and its text not leaking in
  Skills         the per-skill report: broken -> FAIL, loosely followed -> PARTIAL
  Course Memory  what earlier sessions already taught, and the recap that carries the
                 previous session's agenda
  Generic        the house rules that hold for every TR doc, plus the length ceilings and
                 the rubric bar

PARTIAL exists for Skills alone, and deliberately. A gate is binary — it fired or it did
not — while "the same example runs through most of the session and then an unrelated one
appears" is neither followed nor ignored, and forcing it either way was the difference
between passing silently and failing the whole run (see graders/skill_report).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from guardrails import guardrails                      # noqa: E402
from graders import skill_report as _sr                # noqa: E402

PASS, PARTIAL, FAIL = "PASS", "PARTIAL", "FAIL"
OK, NEEDS_REPAIR = "PASS", "NEEDS_REPAIR"

# Rendered in this order, and labelled as the reviewer thinks of them rather than by the
# internal key.
SOURCES = [
    (guardrails.CURRICULUM, "Curriculum"),
    (guardrails.PROFILE, "Course Profile"),
    (guardrails.SKILLS, "Skills"),
    (guardrails.MEMORY, "Course Memory"),
    (guardrails.GENERIC, "Generic TR Requirements"),
]
_LABEL = dict(SOURCES)


def _gate_issues(report: dict) -> dict[str, list[str]]:
    """Guardrail failures grouped by the source each one was enforcing.

    Falls back to GENERIC for a result with no sources — a hand-built fixture, or a
    report written by a version that predates the tagging. Better one line that says
    "something structural failed" than a verdict that quietly claims five passes.
    """
    gr = (report or {}).get("guardrails") or {}
    fails = list(gr.get("failures") or [])
    srcs = list(gr.get("failure_sources") or [])
    out: dict[str, list[str]] = {}
    for i, f in enumerate(fails):
        src = srcs[i] if i < len(srcs) else guardrails.GENERIC
        out.setdefault(src, []).append(f)
    return out


def _skills_status(report: dict) -> tuple[str, list[str]]:
    rows = _sr.repairable((report or {}).get("skill_report"))
    if not rows:
        return PASS, []
    broken = [r for r in rows if r.get("verdict") == _sr.FAIL]
    issues = []
    for r in rows:
        verb = ("is not followed" if r.get("verdict") == _sr.FAIL
                else "is followed only in places")
        issues.append(f"Course skill {r.get('ref', '?')} {verb} — "
                      f"“{str(r.get('text') or '')[:110]}”")
    return (FAIL if broken else PARTIAL), issues


def build(report: dict | None) -> dict:
    """The verdict for one graded document. Never raises: it only reads a report."""
    report = report or {}
    by_src = _gate_issues(report)
    statuses: dict[str, str] = {}
    issues: list[tuple[str, str]] = []          # (source key, line)

    for key, _label in SOURCES:
        if key == guardrails.SKILLS:
            continue
        found = by_src.get(key) or []
        statuses[key] = FAIL if found else PASS
        issues += [(key, f) for f in found]

    st, sk_issues = _skills_status(report)
    statuses[guardrails.SKILLS] = st
    issues += [(guardrails.SKILLS, x) for x in sk_issues]

    # GENERIC also carries the length ceilings and the rubric bar. They are house
    # requirements like the gates above them, they are already computed, and leaving them
    # out would let a 30-page document report five clean lines.
    te, pe = report.get("time") or {}, report.get("pages") or {}
    if report.get("time_enforced", True) and te and not te.get("within_budget", True):
        statuses[guardrails.GENERIC] = FAIL
        issues.append((guardrails.GENERIC,
                       f"Recording estimate {te.get('estimated_minutes')} min is over "
                       f"the {te.get('max_minutes')} min ceiling"))
    if pe and not pe.get("within_budget", True):
        statuses[guardrails.GENERIC] = FAIL
        issues.append((guardrails.GENERIC,
                       f"Document is ~{pe.get('estimated_pages')} pages, over the "
                       f"{pe.get('max_pages')}-page ceiling"))
    jr = report.get("judge") or {}
    if report.get("judge_error"):
        statuses[guardrails.GENERIC] = FAIL
        issues.append((guardrails.GENERIC,
                       "The quality check did not complete, so the document is ungraded"))
    elif jr:
        for line in (jr.get("blocking_issues") or []):
            statuses[guardrails.GENERIC] = FAIL
            issues.append((guardrails.GENERIC, str(line)[:160]))

    # OVERALL follows `accepted` when the report has one. The graders decide release;
    # this only reports. Where they disagree — a source marked FAIL on an accepted
    # document, or the reverse — the report's own verdict wins and the five lines still
    # show what was found, because a summary that overrides its own inputs is worse than
    # no summary.
    accepted = report.get("accepted")
    if accepted is None:
        accepted = all(v == PASS for v in statuses.values())
    overall = OK if accepted else NEEDS_REPAIR

    return {"overall": overall, "sources": statuses,
            "issues": [{"source": k, "detail": d} for k, d in issues],
            "text": render(overall, statuses, issues)}


def render(overall: str, statuses: dict[str, str],
           issues: list[tuple[str, str]]) -> str:
    """The block as a reviewer reads it. Plain text: it goes in a run log and a report."""
    width = max(len(lbl) for _k, lbl in SOURCES) + 1
    out = [f"Overall: {overall}", ""]
    for key, label in SOURCES:
        out.append(f"{(label + ':').ljust(width + 1)} {statuses.get(key, PASS)}")
    bad = [(k, d) for k, d in issues]
    if bad:
        out += ["", "Failed / Partial Checks:"]
        for key, detail in bad:
            out.append(f"- [{_LABEL.get(key, key)}] {detail}")
    return "\n".join(out)
