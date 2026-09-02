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

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# The dimension the rows summarise. Named here because two modules need to agree on it:
# this one derives the score, llm_judge writes it into the grade.
DIMENSION = "course_brief_adherence"

# THE FIVE THINGS A RULE CAN BE. `partial` is the one that was missing, and it is the
# state most real findings are in: "the same example runs through most of the session and
# then an unrelated one appears" is neither followed nor ignored, and forcing it into
# either was the difference between passing silently and failing the whole run.
#
# `not_applicable` is now its own state too, rather than being inferred from a `kept` row
# with `engaged: false`. A rule the session never gave anything to apply to has not been
# honoured — there was nothing to honour — and counting it as a pass inflates every
# compliance figure by however many rules happen not to bite this week.
PASS, PARTIAL, FAIL, NA, UNKNOWN = "kept", "partial", "broken", "not_applicable", "unknown"

# The words the JUDGE answers in. Deliberately not the same strings as the internal
# states: the model is asked for "pass"/"partial"/"fail"/"not_applicable" because that is
# the vocabulary the instruction uses, and the report stores `kept`/`broken` because that
# is what every consumer already reads. Mapping them in one place beats teaching either
# side the other's words.
PASS_W, PARTIAL_W, FAIL_W, NA_W = "pass", "partial", "fail", "not_applicable"

# What each is worth when the rules are totalled. A half for `partial` is the whole point
# of having it: a rule followed in most places is most of the way there, and the number
# should say so rather than rounding to one extreme.
_WEIGHT = {PASS: 1.0, PARTIAL: 0.5, FAIL: 0.0}


# HOW MANY BROKEN RULES COST WHAT — now with a rung for `partial`, which is what the
# rubric's own wording always described: "3 when one is followed LOOSELY or in letter but
# not spirit". Loosely followed is exactly partial, and it had nowhere to land.
#
# The ladder matters because `gates.rubric_min_per_dimension` is 4:
#   · a PARTIAL scores 4 — it does NOT block the release, it triggers a repair;
#   · a FAIL scores 3 — below the bar, so the document is not accepted.
# That is the release condition stated as arithmetic: no unresolved FAIL ships, and a
# PARTIAL gets a repair attempt without throwing away a document over a loose line.
def score_for(broken: int, partial: int = 0) -> int:
    if broken <= 0:
        return 5 if partial <= 0 else (4 if partial == 1 else 3)
    return 3 if broken == 1 else 1


def _slides(doc: dict) -> list:
    return [sl for sec in (doc.get("sections") or []) for sl in (sec.get("slides") or [])]


def _section_of(doc: dict, n) -> str:
    """The section a slide number belongs to, for a report row that has to say WHERE."""
    for sec in (doc.get("sections") or []):
        for sl in (sec.get("slides") or []):
            if sl.get("n") == n:
                return str(sec.get("name") or "")
    return ""


def _site(doc: dict, n, note: str = "") -> dict:
    return {"slide": n, "section": _section_of(doc, n), "note": note}


def _slide_nums(text: str) -> list:
    """Slide numbers a failure message names, so a message becomes a place.

    `guardrails._skill_failures` already writes "Slide 14: …" — the location is in the
    string. Parsing it here rather than changing that function's return type keeps the
    gate and the report reading the same values from the same code.
    """
    return [int(m) for m in re.findall(r"Slide (\d+)", str(text or ""))]


def _applied_sites(doc: dict, skill: dict, chk: dict) -> list[dict]:
    """The slides where a CHECKABLE skill was actually satisfied.

    THE HALF THE REPORT WAS MISSING. A verdict says a rule was kept; it does not say the
    rule did anything. "Every worked example shows its code — kept" reads identically on
    a document with four worked examples that all show code and on one with no worked
    examples at all, because a rule with nothing to apply to is trivially unbroken. This
    lists the slides that actually carried the thing, so "kept" can be told apart from
    "never came up" — which is the question "are my skills participating in building the
    doc?" asked precisely.
    """
    kind = chk.get("assert")
    out: list[dict] = []
    try:
        from guardrails import guardrails as _gr
    except Exception:
        return out
    slides = _slides(doc)
    if kind in ("block_present", "min_count"):
        want = str(chk.get("block") or "")
        roles = [r for r in (chk.get("on_roles") or []) if r]
        for sl in slides:
            if roles and str(sl.get("role") or "") not in roles:
                continue
            n_blocks = len(_gr._blocks_of(sl, want))
            if n_blocks:
                out.append(_site(doc, sl.get("n"),
                                 f"carries {n_blocks} `{want}` block(s)"))
    elif kind == "field_present":
        field, when = str(chk.get("field") or ""), str(chk.get("when_block") or "")
        for sl in slides:
            hits = sum(1 for b in (_gr._blocks_of(sl, when) if when
                                   else (sl.get("content") or []))
                       if isinstance(b, dict) and b.get(field))
            if hits:
                out.append(_site(doc, sl.get("n"),
                                 f"{hits} `{when or 'content'}` block(s) carry `{field}`"))
    elif kind == "forbidden_phrase":
        # Nothing to point at: the rule is satisfied by ABSENCE. Saying so is more use
        # than an empty list the reader has to interpret.
        out.append({"slide": None, "section": "",
                    "note": "satisfied across the whole document — the phrases this "
                            "course does not teach appear nowhere"})
    return out


def _deterministic(doc: dict, skills: list[dict]) -> dict[str, dict]:
    """`{ref: {"failures": [...], "applied": [...]}}` for the skills a machine can settle.

    Reuses the gate's own function rather than re-implementing the four assertions, so a
    skill that fails the run cannot be reported as kept by the document that failed it.
    """
    out: dict[str, dict] = {}
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
            fails = _gr._skill_failures(doc, slides, sk, chk)
            out[sk["ref"]] = {
                "failures": fails,
                "applied": _applied_sites(doc, sk, chk),
                "broke": [_site(doc, n, f) for f in fails for n in _slide_nums(f)],
            }
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
            kept = None
        # A verdict needs EITHER a boolean or a status word. Requiring the boolean meant
        # a judge that answered the new four-way question and omitted the old flag was
        # discarded entirely, and every rule it ruled on came back "not assessed".
        if kept is None and str(v.get("status") or "").strip().lower() not in (
                PASS_W, PARTIAL_W, FAIL_W, NA_W):
            continue
        out[ref] = {"kept": kept,
                    "status": str(v.get("status") or "").strip().lower(),
                    # WHAT WAS MEASURED, in the judge's own words. Shown on the report
                    # beside the verdict: a reader who disagrees with a status can then
                    # see whether the disagreement is about the document or about what
                    # the rule was taken to mean — which is usually the real argument.
                    "criterion": str(v.get("criterion") or "").strip()[:300],
                    "evidence": str(v.get("evidence") or "").strip()[:400],
                    "applied": _sites_from(v.get("applied")),
                    "broke": _sites_from(v.get("broke"))}
    return out


def _sites_from(raw) -> list[dict]:
    """The places a judge says a skill shaped, cleaned. Model output, so nothing raises.

    A slide number is kept only when it is actually a number: "slide 4" and "throughout"
    are both things a model returns, and a report that prints the second one as a
    location is worse than one that prints nothing. The note survives either way, so a
    verdict about the document as a whole still says what it saw.
    """
    out: list[dict] = []
    for it in (raw or [])[:12]:
        if isinstance(it, str):
            out.append({"slide": None, "section": "", "note": it.strip()[:240]})
            continue
        if not isinstance(it, dict):
            continue
        n = it.get("slide")
        try:
            n = int(n)
        except (TypeError, ValueError):
            n = None
        note = str(it.get("note") or it.get("quote") or "").strip()[:240]
        if n is None and not note:
            continue
        out.append({"slide": n, "section": str(it.get("section") or "").strip()[:80],
                    "note": note})
    return out


def _check_criterion(chk: dict) -> str:
    """A machine-checkable rule, written out as the thing being looked for.

    The four assertions in one sentence each, so a `checked` row reads the same way a
    `judged` one does — the reader should not have to know which half produced a verdict
    in order to know what was measured.
    """
    kind, blk = chk.get("assert"), chk.get("block")
    roles = ", ".join(r for r in (chk.get("on_roles") or []) if r)
    if kind == "block_present":
        where = f" on every {roles} slide" if roles else " on every slide"
        return f"a `{blk}` block is present{where}"
    if kind == "field_present":
        when = chk.get("when_block")
        return (f"every `{when}` block carries `{chk.get('field')}`" if when
                else f"every content block carries `{chk.get('field')}`")
    if kind == "min_count":
        return f"the document carries at least {chk.get('min')} `{blk}` block(s)"
    if kind == "forbidden_phrase":
        ph = ", ".join(f"“{pp}”" for pp in (chk.get("phrases") or [])[:4])
        return f"the teaching text never says {ph}"
    return ""


def _reconcile(v: dict) -> str:
    """The judge's status for one prose rule, checked against the sites it itself gave.

    EVIDENCE BEATS CLAIM. The judge returns both a status and the places it saw the rule
    honoured and broken, and those can disagree — a model that has just listed a slide
    where the rule is violated will still sometimes call the rule followed, because the
    document reads well overall. Where they disagree the sites win: they are specific and
    checkable by the reader, and the status is a summary of them.

    So a "pass" carrying broken sites is at best PARTIAL, and a "fail" that also lists
    places the rule WAS followed is PARTIAL rather than FAIL — a rule followed on four
    slides and dropped on the fifth has not been ignored.
    """
    said = str(v.get("status") or "").strip().lower()
    if said not in (PASS_W, PARTIAL_W, FAIL_W, NA_W):
        # No status, or one that is not in the vocabulary: fall back to the boolean,
        # which is the field the judge has always returned.
        said = PASS_W if v.get("kept") else FAIL_W
    has_broke = bool(v.get("broke"))
    has_applied = bool(v.get("applied"))
    if said == NA_W:
        # Only believed when nothing was cited either way. A rule the judge calls
        # inapplicable while naming slides it shaped did apply.
        return NA if not (has_broke or has_applied) else (PARTIAL if has_broke else PASS)
    if said == PASS_W:
        return PARTIAL if has_broke else PASS
    if said == FAIL_W:
        return PARTIAL if has_applied else FAIL
    return PARTIAL


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
            det = checked[ref]
            fails, applied = det["failures"], det["applied"]
            # THE OBSERVABLE CRITERION, read off the two site lists the check produced.
            # Both non-empty means the rule was honoured on some slides and broken on
            # others, which is the definition of partial and is a COUNT here, not an
            # opinion: slide 4 carries the code block, slide 9 does not.
            if fails and applied:
                verdict = PARTIAL
            elif fails:
                verdict = FAIL
            elif applied:
                verdict = PASS
            else:
                # Nothing satisfied it and nothing broke it: the document never put the
                # rule in a position to apply.
                verdict = NA
            row.update(how="checked", verdict=verdict,
                       evidence="; ".join(fails[:3]),
                       # A check IS its criterion, so it can be stated exactly rather
                       # than paraphrased by a model.
                       criterion=_check_criterion(sk.get("check") or {}),
                       applied=applied, broke=det["broke"])
        elif ref in judged:
            v = judged[ref]
            row.update(how="judged", verdict=_reconcile(v),
                       evidence=v["evidence"], criterion=v.get("criterion") or "",
                       applied=v.get("applied") or [], broke=v.get("broke") or [])
        else:
            # NOT ASSESSED. The judge returned nothing for this skill — it was off, it
            # errored, or it simply skipped the line. Shown as its own state, because a
            # rule nobody looked at must not read like a rule that passed.
            row.update(how="unreported", verdict=UNKNOWN, evidence="", criterion="",
                       applied=[], broke=[])
        # WHETHER THE RULE EVER CAME UP. A rule with nothing to apply to is trivially
        # unbroken, so "kept" alone cannot tell a course owner that their skill did any
        # work. This is the flag that separates "followed, here and here" from "nothing
        # in this document engaged it".
        row["engaged"] = bool(row["applied"] or row["broke"])
        rows.append(row)

    counts = {v: sum(1 for r in rows if r["verdict"] == v)
              for v in (PASS, PARTIAL, FAIL, NA, UNKNOWN)}
    # APPLICABLE means "could be ruled on at all". A rule the session never engaged and
    # a rule nobody looked at are both excluded, for opposite reasons — one has no
    # verdict to give, the other has one nobody took — and counting either as a pass is
    # how a compliance figure gets to 100% by having nothing to measure.
    applicable = counts[PASS] + counts[PARTIAL] + counts[FAIL]
    earned = sum(_WEIGHT[r["verdict"]] for r in rows if r["verdict"] in _WEIGHT)
    return {
        "skills": rows,
        # `kept` / `broken` / `unknown` keep their names: every consumer reads them, and
        # renaming a field to match a new vocabulary breaks the stored reports of every
        # run that came before it.
        "kept": counts[PASS], "partial": counts[PARTIAL], "broken": counts[FAIL],
        "not_applicable": counts[NA], "unknown": counts[UNKNOWN], "total": len(rows),
        "applicable": applicable,
        # THE DEVELOPER-FACING NUMBER: what share of the rules that could be judged were
        # actually followed, with a partial counting half. None when nothing was
        # applicable, because 0/0 is not 0% — it is "no answer".
        "compliance_pct": round(100 * earned / applicable) if applicable else None,
        # How many rules the document actually exercised, as opposed to merely not
        # contradicting. The count a course owner is really asking for.
        "engaged": sum(1 for r in rows if r["engaged"]),
        "slides": len(_slides(doc)),
        # No score when NOTHING could be ruled on — every row unknown or inapplicable
        # means the grader had nothing to weigh, which is a fact about the grading and
        # not about the document. The caller then leaves the dimension as the judge left
        # it rather than inventing a 5.
        "score": score_for(counts[FAIL], counts[PARTIAL]) if applicable else None,
        "dimension": DIMENSION,
        # BY CATEGORY, because that is the level the brief is WRITTEN at. An author does
        # not think in six numbered rules; they think "the teaching flow" and "what we
        # show". Six per-rule rows answer "which line was missed"; this answers "is my
        # teaching flow landing?" — and those are different questions with different
        # fixes. A category whose rules keep coming back PARTIAL is a category that needs
        # rewording, which no single row can tell you.
        "by_category": _by_category(rows),
    }


# The four things a skill can govern, in the order a writer needs them, plus the legacy
# labels. Named here so a category with no rules this session is still absent rather than
# showing as an empty row — and so the order does not depend on dict insertion.
_CATEGORY_TITLES = {
    "teaching_flow": "Teaching Flow",
    "teaching_guidelines": "Teaching Guidelines",
    "examples_visuals": "Examples & Visuals",
    "reviewer": "Reviewer Corrections",
    "content": "What it must contain",
    "structure": "How it must be structured",
    "style": "How it must be written",
}


def _by_category(rows: list[dict]) -> list[dict]:
    """One rollup per category the brief actually uses, worst-first within each.

    `status` is the category's own verdict, and it takes the WORST rule in it: a category
    is not passing while one of its rules is broken. `repaired` is carried up too,
    because "Reviewer Corrections — 1 PARTIAL, repaired" is the line that says the rule
    is not landing at generation time.
    """
    seen: dict[str, list[dict]] = {}
    for r in rows:
        seen.setdefault(str(r.get("category") or "other"), []).append(r)
    order = [c for c in _CATEGORY_TITLES if c in seen] + \
            [c for c in seen if c not in _CATEGORY_TITLES]
    out = []
    for cat in order:
        group = seen[cat]
        counts = {v: sum(1 for r in group if r["verdict"] == v)
                  for v in (PASS, PARTIAL, FAIL, NA, UNKNOWN)}
        applicable = counts[PASS] + counts[PARTIAL] + counts[FAIL]
        earned = sum(_WEIGHT[r["verdict"]] for r in group if r["verdict"] in _WEIGHT)
        # Worst wins: a category with any FAIL is failing, whatever else passed in it.
        status = (FAIL if counts[FAIL] else
                  PARTIAL if counts[PARTIAL] else
                  PASS if counts[PASS] else
                  NA if counts[NA] else UNKNOWN)
        out.append({
            "category": cat,
            "title": _CATEGORY_TITLES.get(cat, cat.replace("_", " ").title()),
            "status": status,
            "total": len(group), "applicable": applicable,
            "kept": counts[PASS], "partial": counts[PARTIAL], "broken": counts[FAIL],
            "not_applicable": counts[NA], "unknown": counts[UNKNOWN],
            "repaired": sum(1 for r in group if r.get("repaired")),
            "compliance_pct": round(100 * earned / applicable) if applicable else None,
        })
    return out


def mark_repaired(prev: dict | None, new: dict | None) -> dict | None:
    """Stamp `repaired` on rules the repair pass actually fixed. Returns `new`.

    WHY THIS IS WORTH RECORDING. "Teaching Flow PASS" is a different fact from
    "Teaching Flow 1 PARTIAL → REPAIRED": the first says the document was written right,
    the second says it was written wrong and then corrected. A course owner reading the
    second learns that the rule is not landing at generation time, which is the signal
    that the rule needs rewording — and it is invisible if only the final state is kept.
    """
    if not new or not prev:
        return new
    was = {r.get("ref"): r.get("verdict") for r in (prev.get("skills") or [])}
    for r in new.get("skills") or []:
        before = was.get(r.get("ref"))
        if before in (FAIL, PARTIAL) and r.get("verdict") in (PASS, NA):
            r["repaired"] = True
            r["was"] = before
        elif before == FAIL and r.get("verdict") == PARTIAL:
            # Improved but not finished — worth saying, and not the same as fixed.
            r["repaired"] = "partly"
            r["was"] = before
    new["repaired"] = sum(1 for r in new.get("skills") or [] if r.get("repaired"))
    # Recomputed, because the rollup was built before the rows knew they had been
    # repaired — leaving it stale would put "0 repaired" beside a row marked repaired.
    new["by_category"] = _by_category(new.get("skills") or [])
    return new


def repairable(report: dict | None) -> list[dict]:
    """The rows a repair pass should act on: broken first, then loosely followed.

    N/A and unknown are NOT repairable. A rule the session never engaged cannot be
    satisfied without adding curriculum the session does not own, and a rule nobody
    ruled on has no defect to fix — attempting either is how a repair pass starts
    inventing content to satisfy a brief.
    """
    rows = (report or {}).get("skills") or []
    return ([r for r in rows if r.get("verdict") == FAIL]
            + [r for r in rows if r.get("verdict") == PARTIAL])


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
    bad = repairable(report)
    bits = []
    if report.get("unknown"):
        bits.append(f"{report['unknown']} not assessed")
    if report.get("not_applicable"):
        bits.append(f"{report['not_applicable']} not applicable to this session")
    tail = f" ({'; '.join(bits)})" if bits else ""
    if not bad:
        return (f"All {report['kept']} applicable skills of this course's brief were "
                f"honoured{tail}.")
    named = "; ".join(
        f"{r['ref']} ({'loosely followed' if r['verdict'] == PARTIAL else 'not followed'}) "
        f"“{r['text'][:70]}” — {r['evidence'][:140] or 'no evidence given'}"
        for r in bad[:3])
    more = f" and {len(bad) - 3} more" if len(bad) > 3 else ""
    head = []
    if report.get("broken"):
        head.append(f"{report['broken']} broken")
    if report.get("partial"):
        head.append(f"{report['partial']} followed loosely")
    return (f"{' and '.join(head)} of {report.get('applicable') or 0} applicable "
            f"skills: {named}{more}{tail}.")
