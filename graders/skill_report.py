"""PER-SKILL REPORT — which of THIS course's rules the document actually kept.

WHY THIS EXISTS. A course owner writes seven skills, approves them, and gets back one
number: `course_brief_adherence`, 4 out of 5, six weighted points. That number says the
brief was followed "loosely" somewhere. It does not say WHICH line, so the one action it
implies — go and look at the rule that was missed — is the one thing it withholds. Asked
"how do I know my skills were used?", the honest answer was "you don't, you have a score".

So the same judgement is asked for PER SKILL instead of once over the whole brief, and
this module joins the two halves that can answer it:

  · a skill carrying a machine-checkable `check` is settled EXACTLY, by the same
    function the generation gate uses (guardrails._skill_failures). No model opinion is
    involved and none is wanted — the answer is countable.
  · every other skill is prose, and only a reader can weigh it. The judge is given the
    brief with a label on each line and returns a verdict naming the label, with the
    quote that breaks it.

THE SCORE IS DERIVED FROM THE ROWS, not asked for separately. When the two were
independent the judge could report 5/5 while the report listed a broken rule, and a
reviewer had no way to tell which to believe. Now `course_brief_adherence` is computed
from the verdicts — the number is a summary of the list, so it cannot disagree with it.

WHAT A ROW CANNOT SAY. "kept" means nothing was found against the skill, which for a
prose rule is a reader's opinion and not a proof. A skill nobody could rule on comes back
`unknown` and is shown as such rather than being quietly counted as kept — a brief that
was not assessed must not read like a brief that passed.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The dimension the rows summarise. Named here because two modules need to agree on it:
# this one derives the score, llm_judge writes it into the grade.
DIMENSION = "course_brief_adherence"

# HOW MANY BROKEN RULES COST WHAT. The same shape the rubric text already describes —
# 5 when every line is honoured, 3 when one is not, 1 when the document ignores what its
# course requires — and the same thresholds evals/run_sets.py scores the eval set on, so
# the gate, the grade and the eval cannot rank the same document three different ways.
def score_for(broken: int) -> int:
    if broken <= 0:
        return 5
    return 3 if broken == 1 else 1


def _slides(doc: dict) -> list:
    return [sl for sec in (doc.get("sections") or []) for sl in (sec.get("slides") or [])]


def _deterministic(doc: dict, skills: list[dict]) -> dict[str, list[str]]:
    """`{ref: [failure, …]}` for the skills a machine can settle. Absent = not checkable.

    Reuses the gate's own function rather than re-implementing the four assertions, so a
    skill that fails the run cannot be reported as kept by the document that failed it.
    """
    out: dict[str, list[str]] = {}
    try:
        from guardrails import guardrails as _gr
    except Exception:
        return out
    slides = _slides(doc)
    for sk in skills:
        chk = sk.get("check")
        if not isinstance(chk, dict) or not chk.get("assert"):
            continue
        try:
            out[sk["ref"]] = _gr._skill_failures(doc, slides, sk, chk)
        except Exception:
            continue                      # a broken check must not take the report down
    return out


def _verdicts_from_judge(judge_result: dict | None) -> dict[str, dict]:
    """`{ref: {"kept": bool, "evidence": str}}` out of the judge's `brief_verdicts`.

    Tolerant on purpose. This is model output, so a missing field, a stray casing, a
    verdict for a skill that does not exist, or the whole key being absent are all
    ordinary — and none of them may raise. What cannot be read is simply not a verdict,
    and the skill it was for comes back `unknown`.
    """
    out: dict[str, dict] = {}
    for v in ((judge_result or {}).get("brief_verdicts") or []):
        if not isinstance(v, dict):
            continue
        ref = str(v.get("ref") or "").strip().upper()
        if not ref:
            continue
        kept = v.get("kept")
        if isinstance(kept, str):
            kept = kept.strip().lower() in ("true", "yes", "kept", "y")
        if not isinstance(kept, bool):
            continue
        out[ref] = {"kept": kept, "evidence": str(v.get("evidence") or "").strip()[:400]}
    return out


def _tier_label(sk: dict) -> str:
    from src import skills as _skills
    return {"reviewer": "reviewer correction", "session": "this session only",
            "course": "course brief"}.get(_skills._tier_of(sk), "course brief")


def build(doc: dict, *, course: str | None, session=None,
          judge_result: dict | None = None) -> dict:
    """One row per approved skill governing this document, plus the derived score.

    Returns `{}` for a course with NO approved skills — not a row saying it passed and
    not a zero. A course that has not written down what it requires has nothing to be
    graded against, and inventing either verdict is a lie about it. Callers test the
    truthiness of the result exactly as they already test the brief itself.
    """
    if not course:
        return {}
    try:
        from src import skills as _skills
        approved = _skills.numbered(course, getattr(session, "number", None)
                                    if session is not None else None)
    except Exception:
        return {}
    if not approved:
        return {}

    checked = _deterministic(doc, approved)
    judged = _verdicts_from_judge(judge_result)

    rows, broken, kept, unknown = [], 0, 0, 0
    for sk in approved:
        ref = sk["ref"]
        row = {
            "ref": ref,
            "id": sk.get("id"),
            "text": str(sk.get("text") or ""),
            "instructions": _skills_instructions(sk),
            "category": sk.get("category") or sk.get("kind") or "",
            "tier": _tier_label(sk),
            "scope": sk.get("scope") or "course",
        }
        if ref in checked:
            # THE EXACT HALF WINS OUTRIGHT where it applies. A `check` is arithmetic over
            # the document; the judge's reading of the same rule is an opinion about it,
            # and an opinion must not overturn a count. (It is also the half that already
            # failed the run, so a row disagreeing with the gate would be reporting a
            # document that does not exist.)
            fails = checked[ref]
            row.update(how="checked", verdict="broken" if fails else "kept",
                       evidence="; ".join(fails[:3]))
        elif ref in judged:
            v = judged[ref]
            row.update(how="judged", verdict="kept" if v["kept"] else "broken",
                       evidence=v["evidence"])
        else:
            # NOT ASSESSED. The judge returned nothing for this skill — it was off, it
            # errored, or it simply skipped the line. Shown as its own state, because a
            # rule nobody looked at must not read like a rule that passed.
            row.update(how="unreported", verdict="unknown", evidence="")
        rows.append(row)
        if row["verdict"] == "broken":
            broken += 1
        elif row["verdict"] == "kept":
            kept += 1
        else:
            unknown += 1

    assessed = kept + broken
    return {
        "skills": rows,
        "kept": kept, "broken": broken, "unknown": unknown, "total": len(rows),
        # No score when NOTHING could be ruled on — every row unknown means the grader
        # did not run, which is a fact about the grading and not about the document. The
        # caller leaves the dimension as the judge left it rather than inventing a 5.
        "score": score_for(broken) if assessed else None,
        "dimension": DIMENSION,
    }


def _skills_instructions(sk: dict) -> list[str]:
    try:
        from src import skills as _skills
        lines = _skills.instructions_of(sk)
    except Exception:
        return []
    # A single-instruction skill IS its own text, already carried in `text` — repeating
    # it as a one-item list makes every row in the UI look like a checklist of one.
    return lines if len(lines) > 1 else []


def justification(report: dict) -> str:
    """The one-sentence `course_brief_adherence` justification, built from the rows.

    Written here rather than taken from the judge because the score is derived here: a
    number and a sentence that were produced by different steps are exactly how a grade
    comes to say 5/5 above a list containing a broken rule.
    """
    if not report:
        return ""
    broken = [r for r in report["skills"] if r["verdict"] == "broken"]
    unknown = report.get("unknown") or 0
    tail = f" ({unknown} not assessed)" if unknown else ""
    if not broken:
        return (f"All {report['kept']} of this course's approved skills were "
                f"honoured{tail}.")
    named = "; ".join(
        f"{r['ref']} “{r['text'][:80]}” — {r['evidence'][:160] or 'not followed'}"
        for r in broken[:3])
    more = f" and {len(broken) - 3} more" if len(broken) > 3 else ""
    return f"{len(broken)} of {report['total']} skills broken: {named}{more}{tail}."
