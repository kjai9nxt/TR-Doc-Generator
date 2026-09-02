"""Eval-SET runner — score a generated TR doc against evals/sets/*.json.

Three kinds of set:
  • DETERMINISTIC  — checked in code (structure, chunk count, time, conciseness,
                     slide phrasing, extraction). Free, exact.
  • LLM-JUDGE      — qualitative dimensions scored 1-5 by the model against the
                     set's criterion + rubric (relevance, groundedness, analogies,
                     language, ordering, flow, no-repeat, market, recency, coverage).
  • SKIP           — needs inputs a single finished doc can't provide (curriculum
                     sheet, a before/after regeneration, or cross-session behaviour).

Each set has a `pass_threshold`; a set passes when its 1-5 score >= that.

Usage:
    python -m evals.run_sets --session 10          # generate (or reuse) + score all sets
    python -m evals.run_sets --session 10 --no-llm # deterministic sets only (free)
"""
from __future__ import annotations
import argparse
import glob
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from src import config, course_loader, pipeline, llm, pptx_ingest, regen_log, learning  # noqa: E402
from guardrails import guardrails  # noqa: E402
from graders import time_grader, page_grader  # noqa: E402

# TWO DIFFERENT "no score", and collapsing them is how the brief went unmeasured.
#
#   NOT_APPLICABLE — the set does not apply to this document at all (a course with no
#                    skills has no brief to follow). It is skipped, and skipping is the
#                    honest answer: scoring it would be inventing a verdict.
#   None           — the DETERMINISTIC half has nothing it can assert, but there is
#                    still something to judge. The judge half then stands alone. This is
#                    the ordinary case for skills, which are prose: no machine can settle
#                    "explain the code line by line", and a reader settles it easily.
#
# Returning None for both meant every real course's brief was skipped rather than read.
NOT_APPLICABLE = "n/a"

SETS_DIR = ROOT / "evals" / "sets"
CASES_DIR = ROOT / "evals" / "cases"
_WORD = re.compile(r"[a-z0-9']+", re.I)

# self_evolution_loop is now MEASURED (longitudinally, against the prior session's
# saved eval result) — handled specially in run_on_doc, not skipped outright.
SKIP: dict[str, str] = {}


def _load_sets() -> list[dict]:
    idx = json.loads((SETS_DIR / "index.json").read_text())
    out = []
    for entry in idx["sets"]:
        out.append(json.loads((SETS_DIR / entry["file"]).read_text()))
    return out


def _slides(doc):
    return [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]


def _wc(text):
    return len(_WORD.findall(str(text or "")))


# --------------------------------------------------------------------------- #
# deterministic checkers -> (score 1-5, detail str)
# --------------------------------------------------------------------------- #
def _chk_recording_time(doc, session, sset):
    """Score the recording budget from the ACTIVE pacing model, thresholds from config.

    The old version hardcoded 42/40/20 and treated "under 20 minutes" as thin. Under the
    per-slide pacing model a low minute count is nothing but a low SLIDE count, and
    whether that means thin coverage is what the coverage and chunk_count sets decide —
    scoring it here punished a short syllabus line twice for the same thing.
    """
    te = time_grader.estimate(doc)
    m, cap = te["estimated_minutes"], te["max_minutes"]
    marginal = cap + 2                     # 40 -> 42: over, but only just
    score = 1 if m > marginal else (3 if m > cap else 5)
    detail = f"estimated {m} min (budget {cap}, within={te['within_budget']}"
    if te.get("pacing") == "per_slide":
        detail += (f", paced at {te['minutes_per_slide']} min x {te['slide_count']} slides"
                   f"; word-count model would read {te['narration_minutes']} min")
        if te.get("dense_slides"):
            detail += f"; dense slides {te['dense_slides']}"
    return score, detail + ")"


def _chk_conciseness(doc, session, sset):
    viol = []
    for s in _slides(doc):
        for b in s.get("content", []):
            if b.get("type") == "bullets":
                for it in b.get("items", []):
                    if _wc(it) > 12:
                        viol.append(f"slide {s.get('n')} bullet {_wc(it)}w")
            elif b.get("type") == "text":
                for sent in re.split(r"(?<=[.!?])\s+", b.get("text", "")):
                    if _wc(sent) > 18:
                        viol.append(f"slide {s.get('n')} sentence {_wc(sent)}w")
        # heading/subheading are 4-word labels; title keeps the ≤8-word phrase cap.
        # Counted by whitespace (same as guardrails), so a hyphenated label like
        # "Cookie-Based 4-Way Handshake" is 3 words, not 5.
        hcap = config.harness()["constraints"].get("headings", {})
        for fld, cap in (("heading", hcap.get("max_words", 4)),
                         ("subheading", hcap.get("max_words", 4)),
                         ("title", hcap.get("title_max_words", 8))):
            n = len(str(s.get(fld) or "").split())
            if n > cap:
                viol.append(f"slide {s.get('n')} {fld} {n}w >{cap}w")
    score = 5 if not viol else (3 if len(viol) <= 2 else 1)
    return score, ("no over-length lines" if not viol else f"{len(viol)} over-length: {viol[:6]}")


def _chk_prose_bullet_mix(doc, session, sset):
    """The countable half of the mix rule — the judge takes the rest.

    Three symptoms, all counted the same way the guardrail counts them (thresholds come
    from the harness, so this set and the gate can never drift apart):
      · slides with no framing paragraph at all — the wall of bullets;
      · "lists" of one or two items, which are really a sentence somebody bulleted;
      · bullets that RESTATE the paragraph above them, measured by word overlap.
    The third is the expensive one: on a fixed page ceiling a repeated line is a line
    that cannot teach anything new. What word overlap cannot see — a bullet that
    paraphrases the paragraph while sharing almost no vocabulary — is what the judge
    half of this hybrid set is for.
    """
    from guardrails.guardrails import _norm_tokens
    c = config.harness()["constraints"].get("content", {})
    share_floor = c.get("min_slides_with_text_share", 0.6)
    min_items = c.get("min_bullet_items", 3)
    echo_thr = float(c.get("bullet_echo_overlap", 0.5))
    slides = _slides(doc)
    if not slides:
        return 1, "no slides"
    bare, short, echoes = [], [], []
    for s in slides:
        blocks = s.get("content") or []
        texts = [str(b.get("text") or "") for b in blocks if b.get("type") == "text"]
        if not any(t.strip() for t in texts):
            bare.append(s.get("n"))
        clauses = [c2 for t in texts for c2 in re.split(r"[;.!?]", t)
                   if len(_norm_tokens(c2)) >= 3]
        for b in blocks:
            if b.get("type") != "bullets":
                continue
            items = b.get("items") or []
            if 0 < len(items) < min_items:
                short.append(f"slide {s.get('n')} ({len(items)} items)")
            for it in items:
                bt = _norm_tokens(it)
                if len(bt) < 3 or not clauses:
                    continue
                best = 0.0
                for c2 in clauses:
                    shared = bt & _norm_tokens(c2)
                    if len(shared) >= 2:
                        best = max(best, len(shared) / len(bt))
                if best >= echo_thr:
                    echoes.append(f"slide {s.get('n')}: \"{str(it)[:44]}\" ({best:.0%})")
    share = (len(slides) - len(bare)) / len(slides)
    broken = sum([share < share_floor, bool(short), bool(echoes)])
    detail_bits = []
    if share < share_floor:
        detail_bits.append(f"only {share:.0%} of slides carry prose (floor "
                           f"{share_floor:.0%}); bare slides {bare[:6]}")
    if short:
        detail_bits.append(f"{len(short)} one/two-item 'list(s)': {short[:3]}")
    if echoes:
        detail_bits.append(f"{len(echoes)} bullet(s) restate their paragraph — that is "
                           f"page budget spent teaching nothing: {echoes[:3]}")
    if broken == 0:
        return 5, (f"{share:.0%} of slides carry prose; every list has >= {min_items} "
                   f"items; no bullet restates its paragraph")
    return (3 if broken == 1 else 1), "; ".join(detail_bits)


def _chk_slide_phrasing(doc, session, sset):
    banned = [b.lower() for b in sset.get("banned_in_slide_content", [])]
    banned += ["in the previous session", "in the next session"]
    hits = []
    for s in _slides(doc):
        parts = [s.get("heading", ""), s.get("subheading", ""), s.get("title", "")]
        for b in s.get("content", []):
            if b.get("type") == "text":
                parts.append(b.get("text", ""))
            elif b.get("type") == "bullets":
                parts += b.get("items", [])
            elif b.get("type") == "table":
                parts += [str(c) for row in b.get("rows", []) for c in row]
        blob = " ".join(parts).lower()
        for phrase in banned:
            if phrase in blob:
                hits.append(f"slide {s.get('n')}: '{phrase}'")
    score = 5 if not hits else 1
    return score, ("no meta-narration in slide content" if not hits else f"banned phrases: {hits[:6]}")


def _chk_document_structure(doc, session, sset):
    v = []
    slides = _slides(doc)
    is_first = session.number <= 1
    if not doc.get("session_title"):
        v.append("no title")
    if not is_first and not doc.get("recap"):
        v.append("recap missing (non-first session)")
    if is_first and doc.get("recap"):
        v.append("recap present on session 1")
    agenda_n = len(doc.get("agenda", []))
    sec_n = len(doc.get("sections", []))
    if agenda_n != sec_n:
        v.append(f"sections({sec_n}) != agenda items({agenda_n})")
    # `analogy` is NOT in the required set: it is conditional on the slide's role, and
    # the analogy_placement set owns that biconditional. Requiring it here is what used
    # to force an analogy onto comparison and worked-example slides.
    for s in slides:
        miss = [f for f in ("role", "heading", "subheading", "content",
                            "visual_guidance", "speaker_notes") if not s.get(f)]
        if miss:
            v.append(f"slide {s.get('n')} missing {miss}")
    if config.harness()["constraints"].get("coverage", {}).get("require_coverage_map"):
        cmap = doc.get("coverage_map")
        if not isinstance(cmap, list) or not cmap:
            v.append("coverage_map missing")
        elif len(cmap) != len(session.key_takeaways):
            v.append(f"coverage_map has {len(cmap)} entries vs "
                     f"{len(session.key_takeaways)} takeaways")
    if (doc.get("closing") or "").strip() != "Thank You  |  All the Best":
        v.append("closing != 'Thank You  |  All the Best'")
    score = 5 if not v else 1
    return score, ("exact layout, all required slide fields, coverage_map present"
                   if not v else f"{len(v)} issue(s): {v[:6]}")


def _chk_chunk_count(doc, session, sset):
    agenda_n = len(doc.get("agenda", []))
    sec_n = len(doc.get("sections", []))
    is_first = session.number <= 1
    ok = (agenda_n == sec_n) and (is_first or bool(doc.get("recap")))
    score = 5 if ok else 1
    return score, f"agenda={agenda_n}, sections={sec_n}, recap={'yes' if doc.get('recap') else 'no'}"


def _chk_extraction(doc, session, sset):
    rep = pptx_ingest.completeness_report()
    score = 5 if rep["ok"] else (3 if rep["decks_with_issues"] <= 1 else 1)
    return score, f"{rep['decks_checked']} deck(s), {rep['decks_with_issues']} with issues"


def _norm_kt(s):
    # Compare on alphanumeric word-content only, so cosmetic punctuation/spacing
    # differences ("UDP : Header" vs "UDP: Header") don't count as a mismatch —
    # while genuine merge/drop/invent (different word content) still does.
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def _chk_curriculum_extraction(doc, session, sset):
    """The doc's key_takeaways must exactly reproduce the curriculum's takeaways
    (session.key_takeaways) — same set, same count, no merge/drop/invent/duplicate."""
    truth = [_norm_kt(k) for k in session.key_takeaways]
    got = [_norm_kt(k) for k in doc.get("key_takeaways", [])]
    if not truth:
        return 3, "no curriculum takeaways to compare against"
    missing = [t for t in truth if t not in got]
    extra = [g for g in got if g not in truth]
    dupes = sorted({g for g in got if got.count(g) > 1})
    if sorted(got) == sorted(truth) and not dupes:
        return 5, f"all {len(truth)} takeaways extracted verbatim"
    bits = []
    if len(got) != len(truth):
        bits.append(f"count {len(got)} vs {len(truth)}")
    if missing:
        bits.append(f"missing {missing[:3]}")
    if extra:
        bits.append(f"extra {extra[:3]}")
    if dupes:
        bits.append(f"duplicates {dupes[:3]}")
    return 1, "; ".join(bits)


def _chk_document_length(doc, session, sset):
    """The 16-page ceiling. A doc that came in very SHORT is flagged too: the usual
    cause is dropped coverage, not good editing, and that is the worse failure."""
    pe = page_grader.estimate(doc)
    p, cap, target = pe["estimated_pages"], pe["max_pages"], pe["target_pages"]
    if p > cap:
        score = 1
    elif p < 6:
        score = 1
    elif p > target:
        score = 3
    else:
        score = 5
    top = sorted(pe["pages_by_part"].items(), key=lambda kv: -kv[1])[:3]
    where = ", ".join(f"{k} ~{v:.1f}p" for k, v in top)
    return score, f"~{p} pages (target {target}, max {cap}); biggest: {where}"


def _chk_analogy_placement(doc, session, sset):
    """The exact biconditional: analogy present iff role == concept_intro. Also checks
    the roles are declared, valid, and not all labelled concept_intro to keep the
    analogies — the cheat that would otherwise make this set trivially passable."""
    con = config.harness()["constraints"]
    valid = set(con.get("slide_roles", {}).get("values", []))
    req = set(con.get("analogy", {}).get("required_on_roles", []))
    ban = set(con.get("analogy", {}).get("banned_on_roles", []))
    cap = con.get("slide_roles", {}).get("max_concept_intro_share", 1.0)
    slides = _slides(doc)
    viol, n_intro = [], 0
    for s in slides:
        role = str(s.get("role") or "").strip()
        has = bool(str(s.get("analogy") or "").strip())
        if not role:
            viol.append(f"slide {s.get('n')} has no role")
            continue
        if role not in valid:
            viol.append(f"slide {s.get('n')} role '{role}' invalid")
            continue
        if role == "concept_intro":
            n_intro += 1
        if role in ban and has:
            viol.append(f"slide {s.get('n')} ({role}) has an analogy")
        if role in req and not has:
            viol.append(f"slide {s.get('n')} ({role}) is missing its analogy")
    if slides and n_intro > cap * len(slides):
        viol.append(f"{n_intro}/{len(slides)} slides labelled concept_intro (max {cap:.0%})")
    score = 5 if not viol else (3 if len(viol) == 1 else 1)
    detail = ("analogies exactly on first-introduction slides "
              f"({n_intro}/{len(slides)} concept_intro)" if not viol
              else f"{len(viol)} violation(s): {viol[:6]}")
    return score, detail


def _chk_worked_example_share(doc, session, sset):
    """Deterministic HALF of the worked-example set: the share cap. Whether each example
    was WARRANTED (and whether a procedural topic went without one) needs judgement, so
    the LLM half is combined in via HYBRID below."""
    con = config.harness()["constraints"]
    cap = con.get("worked_example", {}).get("max_share_of_slides", 1.0)
    slides = _slides(doc)
    we = [s for s in slides if str(s.get("role") or "") == "working_example"]
    if slides and len(we) > cap * len(slides):
        return 1, (f"{len(we)}/{len(slides)} slides are worked examples "
                   f"(max {cap:.0%}) — the concepts have stopped being taught")
    return 5, f"{len(we)}/{len(slides)} worked-example slide(s), within the {cap:.0%} cap"


def _chk_example_realism(doc, session, sset):
    """Deterministic HALF of the realism set: worked examples must carry concrete values,
    and no slide may use a placeholder. Whether a figure is PLAUSIBLE for the domain
    (a 1000-byte page, a decimal where hex is conventional) is the LLM half."""
    con = config.harness()["constraints"]
    ex = con.get("examples", {})
    min_lits = ex.get("min_numeric_literals", 2)
    banned = [b.lower() for b in ex.get("banned_placeholders", [])]
    viol = []
    for s in _slides(doc):
        blob = " ".join([
            str(s.get(f) or "") for f in ("title", "heading", "subheading", "analogy",
                                          "speaker_notes")]
            + [str(b.get("text", "")) for b in (s.get("content") or [])
               if b.get("type") == "text"]
            + [str(i) for b in (s.get("content") or []) if b.get("type") == "bullets"
               for i in (b.get("items") or [])]
            + [str(c) for b in (s.get("content") or []) if b.get("type") == "table"
               for row in (b.get("rows") or []) for c in row])
        low = blob.lower()
        for ph in banned:
            if re.search(r"(?<![a-z])" + re.escape(ph) + r"(?![a-z])", low):
                viol.append(f"slide {s.get('n')}: placeholder '{ph}'")
        if str(s.get("role") or "") == "working_example":
            lits = re.findall(r"0x[0-9a-f]+|\b\d[\d,._]*\b", blob, re.I)
            if len(lits) < min_lits:
                viol.append(f"slide {s.get('n')}: worked example with {len(lits)} "
                            f"concrete value(s) (min {min_lits})")
    score = 5 if not viol else (3 if len(viol) == 1 else 1)
    return score, ("figures concrete, no placeholders" if not viol
                   else f"{len(viol)} issue(s): {viol[:6]}")


def _chk_skill_adherence(doc, session, sset):
    """Does the document obey the rules THIS COURSE is written under?

    Parameterised by the course's own approved skills rather than by a fixed rule — one
    dimension for every course, instead of a set per course. Only the skills carrying a
    machine-checkable assertion are scored; prose-only skills reach the writer through
    the rules block and are weighed by the judge, which is required to quote evidence.

    Reuses guardrails._skill_failures, so the eval and the gate cannot disagree about
    whether a skill was obeyed.
    """
    from src import skills as _skills
    # THE COURSE THIS DOCUMENT WAS WRITTEN FOR. `session.course` is now really carried
    # (src/course_loader.Session) — before it was, this getattr could only ever miss and
    # every document was scored against whichever course the instance-wide dropdown was
    # showing. Evaluate a React doc while a colleague has Operating Systems selected and
    # it was graded against Operating Systems' brief, silently and plausibly.
    course = getattr(session, "course", None) or _active_course_name()
    # AND THE SESSION, so session-scoped skills count. A skill written for session 12
    # governs session 12: the writer was given it (src/pipeline.py resolves with the
    # session), so a grader that resolves without one marks the document against a
    # SMALLER brief than it was written under — it can be let off a rule it broke, or
    # marked down for one it was never told.
    session_no = getattr(session, "number", None)
    approved = _skills.applicable(course, session_no)
    rs = [s for s in approved if isinstance((s or {}).get("check"), dict)]
    if not rs and approved:  # prose-only brief -> the judge half scores it alone
        # THE COMMON CASE, and it used to score nothing. Every skill in the live store is
        # PROSE — "explain the code line by line" is not a machine-checkable assertion —
        # so this set skipped for every real course, and the one dimension that measures
        # what makes a course different from every other course reported nothing at all.
        # Prose is the LLM half's job (see HYBRID below): the deterministic half has
        # nothing to assert, which is not the same as the document being fine.
        return None, (f"{len(approved)} approved skill(s), none machine-checkable — "
                      f"scored by the judge half instead")
    if not rs:
        # NO BRIEF AT ALL. Not scoreable, and that is not the same as scoring zero — a
        # course that has not written its rules yet must not be marked down for it, nor
        # handed a free 5. (Returning a bare None here used to go straight into
        # `score >= threshold` and take the WHOLE eval run down with a TypeError.)
        return NOT_APPLICABLE, ("this course has no approved skills — it has not said "
                                "what it requires, so there is nothing to score")
    # SCORED BY THE SAME CODE THE GATE USES, on the same ladder. Counting violations here
    # and deriving the grade's dimension from statuses there let the two rank one document
    # differently — a doc with one rule followed on three slides and dropped on a fourth
    # was a 3 to this set and a 4 (PARTIAL) to the grade. One builder, one ladder.
    from graders import skill_report as _sr
    rep = _sr.build(doc, course=course, session=session, judge_result=None)
    checked = [r for r in (rep.get("skills") or []) if r.get("how") == "checked"]
    if rep.get("score") is None or not checked:
        return None, (f"{len(approved)} approved skill(s), nothing the machine half "
                      f"could settle — scored by the judge half instead")
    bad = [r for r in checked if r["verdict"] in ("broken", "partial")]
    n_f = sum(1 for r in bad if r["verdict"] == "broken")
    score = _sr.score_for(n_f, len(bad) - n_f)
    detail = (f"{len(checked)} checkable skill(s); "
              + ("all satisfied" if not bad
                 else "; ".join(f"{r['ref']} {r['verdict']}: {r['evidence'][:90]}"
                                for r in bad[:3])))
    return score, detail


def _active_course_name() -> str:
    from src import app_settings
    return app_settings.course_name() or ""


DETERMINISTIC = {
    "recording_time_budget": _chk_recording_time,
    "document_length_pages": _chk_document_length,
    "analogy_placement": _chk_analogy_placement,
    "conciseness": _chk_conciseness,
    "slide_phrasing_no_meta_narration": _chk_slide_phrasing,
    "document_structure_layout": _chk_document_structure,
    "chunk_count": _chk_chunk_count,
    "ppt_extraction_completeness": _chk_extraction,
    "curriculum_takeaway_extraction": _chk_curriculum_extraction,
}

# TRUE HYBRIDS: a deterministic half that catches what is countable, plus the LLM's
# judgement of what is not, combined as the MINIMUM. Both halves must be satisfied —
# an example within the share cap that was still unwarranted should not pass on a
# technicality, and vice versa.
HYBRID = {
    # Two halves that genuinely cannot see each other's defects: a `check` is exact and
    # a machine can settle it, and everything else the course owner wrote is prose that
    # only a reader can weigh. Combined as the minimum, so a document cannot pass by
    # satisfying the checkable half while ignoring the brief.
    "skill_adherence": _chk_skill_adherence,
    "worked_example_appropriateness": _chk_worked_example_share,
    "example_figure_realism": _chk_example_realism,
    # Word overlap catches a bullet that reuses the paragraph's vocabulary; it cannot
    # see one that paraphrases the paragraph in entirely different words ("Applications
    # call generic read/write operations" -> "System calls expose a uniform I/O
    # interface"). That half is the judge's, and both must be satisfied.
    "prose_bullet_mix": _chk_prose_bullet_mix,
}


# --------------------------------------------------------------------------- #
# generic LLM-judge for qualitative sets
# --------------------------------------------------------------------------- #
def _set_context(sset, session) -> str:
    """Extra prompt material a single set needs, beyond its own criterion text.

    THE BUG THIS FIXES. `skill_adherence` asks "is every approved skill governing this
    course honoured?" — and this function sent the judge the set's title, its rubric, the
    session's key takeaways and the document. The SKILLS THEMSELVES were never in the
    prompt. It was being asked whether a student followed the teacher's instructions
    without ever being shown the instructions, so it answered from the shape of the
    document and the wording of the question.

    It survived because the set's own test replaces this whole function with a stub that
    returns a fixed score: the plumbing was tested, the prompt never was. And because the
    deterministic half of the same set abstains for prose skills — which is every skill
    in a real store — that blind answer was, on its own, the entire verdict.
    """
    if sset.get("id") != "skill_adherence":
        return ""
    try:
        from src import skills as _skills
        course = getattr(session, "course", None) or _active_course_name()
        # Course AND session scope, and labelled — the same brief the writer was given
        # and the same labels the grade's per-skill report uses, so a reviewer comparing
        # the eval with the rubric is comparing two readings of one list.
        brief = _skills.block(course, getattr(session, "number", None), refs=True)
    except Exception:
        brief = ""
    if not brief.strip():
        return ""
    return (
        "\n\nTHE COURSE'S OWN BRIEF — this is what you are scoring the document "
        "against. It was authored by the course owner, approved before it took effect, "
        "and given to the writer. Nothing else in this suite measures it.\n"
        "Score ONLY against lines that are actually below. Where a line is a matter of "
        "degree, judge generously — a brief describes how a course teaches, it is not a "
        "checklist with a pass mark. Name the line and quote the text that breaks it in "
        "your justification; if you cannot quote it, the line was followed.\n"
        f"\n{brief}")


def _llm_score(doc, session, sset, *, enforce_time: bool = True) -> tuple[int, str]:
    h = config.harness()
    m = h["model"]
    model = m["judge"]
    web = ""
    if sset["id"] in ("market_coverage_completeness", "content_recency") \
            and m.get("enable_web_market_check") and m.get("provider", "openrouter").lower() == "openrouter":
        if not model.endswith(":online"):
            model += ":online"
        web = "\nUse a web search to verify current standards/versions and mainstream coverage."
    rubric = "\n".join(f"  {k} = {v}" for k, v in sset.get("rubric", {}).items())
    # Depth mode (40-min limit OFF): the doc is deliberately fuller. Length is not a
    # defect on this run — only genuine filler is.
    depth = "" if enforce_time else (
        "\nDEPTH MODE: the 40-minute recording limit is OFF for this doc, so it is "
        "deliberately fuller (explanatory prose, worked examples, extra slides). Do NOT "
        "penalise length, extra slides, or the absence of terse bullets — penalise only "
        "genuine filler, redundancy, or off-topic text.")
    system = ("You are a strict curriculum-doc reviewer scoring ONE quality dimension. "
              "Return ONLY JSON: {\"score\": <1-5>, \"justification\": \"<one sentence>\"}. "
              "Be discriminating; reserve 5 for genuinely nothing-to-improve.")
    user = f"""DIMENSION: {sset['title']}
WHAT TO CHECK: {sset.get('criterion', sset.get('description',''))}
SCORING RUBRIC:
{rubric}{web}{depth}{_set_context(sset, session)}

SESSION KEY TAKEAWAYS:
{json.dumps(session.key_takeaways, ensure_ascii=False)}

TR DOC (JSON):
{json.dumps(doc, ensure_ascii=False)}

Score this ONE dimension now."""
    raw = llm.complete(system=system, user=user, model=model,
                       max_tokens=m.get("judge_max_tokens", 8000), temperature=0.0)
    obj = llm.extract_json(raw)
    return int(obj.get("score", 0)), str(obj.get("justification", ""))[:300]


def _score_regen_event(event, sset, *, scope_mode: bool = False) -> tuple[int, str]:
    """LLM-score a recorded regeneration event.

    Two different questions are asked of the same event, which is why the flag exists:
      - adherence (default): did the redo ADDRESS the reason?
      - scope (scope_mode):  did it change ONLY what the reason concerned?
    A redo can pass one and fail the other — rewriting the whole section usually fixes
    the complaint and destroys four accepted slides doing it.
    """
    m = config.harness()["model"]
    rubric = "\n".join(f"  {k} = {v}" for k, v in sset.get("rubric", {}).items())
    scope = event.get("scope") or {}
    if scope_mode:
        system = ("You score whether a REGENERATION stayed inside the scope of the "
                  "reviewer's complaint. Return ONLY JSON {\"score\": <1-5>, "
                  "\"justification\": \"<one sentence>\"}. Reserve 5 for a reason fully "
                  "resolved with everything unrelated left untouched. Penalise a "
                  "whole-chunk rewrite prompted by a narrow note even when the result "
                  "reads well, and penalise a no-op that changed nothing.")
        ask = ("How the edit was applied (patch mode means untouched slides are "
               f"byte-identical by construction):\n{json.dumps(scope, indent=2)}\n\n"
               "Did AFTER resolve the reason WITHOUT changing anything the reason did "
               "not concern? Score now.")
    else:
        system = ("You score whether a REGENERATED chunk addressed the user's stated reason. "
                  "Return ONLY JSON {\"score\": <1-5>, \"justification\": \"<one sentence>\"}. "
                  "Reserve 5 for a reason fully addressed while keeping the rest intact.")
        ask = "Did AFTER address the reason? Score now."
    user = f"""REASON THE USER GAVE FOR REGENERATING:
{event.get('reason','')}

SCORING RUBRIC:
{rubric}

BEFORE (the version the user rejected):
{event.get('before','')}

AFTER (the regenerated version):
{event.get('after','')}

{ask}"""
    raw = llm.complete(system=system, user=user, model=m["judge"],
                       max_tokens=m.get("judge_max_tokens", 8000), temperature=0.0)
    obj = llm.extract_json(raw)
    return int(obj.get("score", 0)), str(obj.get("justification", ""))[:300]


# --------------------------------------------------------------------------- #
# Self-evolution (longitudinal): compare THIS session's failures against the most
# recent EARLIER session's saved eval result. A defect that appeared before and is
# gone now = the agent learned; a defect present in BOTH = it recurred (fail).
# --------------------------------------------------------------------------- #
def _prior_eval_result(session_no: int):
    """Most recent saved sets_result for a session < session_no, or None."""
    best = None
    for p in glob.glob(str(CASES_DIR / "sets_result_session_*.json")):
        m = re.search(r"session_(\d+)\.json$", p)
        if not m:
            continue
        n = int(m.group(1))
        if n < session_no and (best is None or n > best[0]):
            try:
                best = (n, json.loads(Path(p).read_text()))
            except Exception:
                pass
    return best  # (n, report) or None


def _fail_ids(report: dict) -> set:
    return {s["id"] for s in report.get("sets", [])
            if not s.get("skipped") and not s.get("passed") and s["id"] != "self_evolution_loop"}


def _score_self_evolution(session, current_fail_ids: set, thr: int) -> dict:
    """Longitudinal, deterministic. Uses the prior session's saved eval result +
    the current learned-rules store as evidence of cross-session learning."""
    sid = "self_evolution_loop"
    n_rules = len(learning.rules())
    prior = _prior_eval_result(session.number)
    if prior is None:
        return {"id": sid, "grader": "longitudinal", "skipped": True,
                "reason": (f"no earlier session's eval result saved yet — run evals on an "
                           f"earlier session to measure cross-session learning "
                           f"({n_rules} rule(s) currently learned)")}
    pn, prep = prior
    prior_fails = _fail_ids(prep)
    recurring = sorted(prior_fails & current_fail_ids)
    fixed = sorted(prior_fails - current_fail_ids)
    if recurring:
        score = 1
        detail = (f"defect(s) recurred despite prior signal (S{pn}→S{session.number}): "
                  f"{recurring}; fixed: {fixed or 'none'}; {n_rules} rule(s) learned")
    elif prior_fails:
        score = 5
        detail = (f"all {len(prior_fails)} prior defect(s) resolved, none recurred "
                  f"(S{pn}→S{session.number}): {fixed}; {n_rules} rule(s) learned")
    else:
        score = 5
        detail = (f"prior session S{pn} was clean — no defect to recur; "
                  f"{n_rules} rule(s) learned")
    return {"id": sid, "grader": "longitudinal", "score": score,
            "threshold": thr, "passed": score >= thr, "detail": detail}


def _learn_from_failures(session, scored: list) -> int:
    """Phase 2 self-evolution: turn eval-set FAILURES into durable rules (LLM runs
    only — distillation calls the model). Honors the self_evolution config."""
    cfg = config.harness().get("self_evolution", {}) or {}
    if not cfg.get("enabled", True) or not cfg.get("learn_from_eval_sets", True):
        return 0
    reasons = [f"{r['id']}: {r['detail']}" for r in scored
               if not r["passed"] and r["id"] != "self_evolution_loop"]
    if not reasons:
        return 0
    return learning.learn_from_issues(
        session.number, reasons, source="eval_set",
        course=getattr(session, "course", None) or _active_course_name() or None)


def run_on_doc(doc: dict, session, *, use_llm: bool = True, enforce_time: bool = True,
               learn: bool = True, course: str | None = None) -> dict:
    """`course` is the curriculum THIS DOCUMENT was written from.

    Passed by a caller that knows it — the run that produced the doc does — and stamped
    onto the session so every checker below reads one course rather than each falling
    back to the instance-wide selection. That fallback is a single global shared by
    everyone signed in, so without this a document could be graded against a brief its
    author never wrote, with nothing in the report to say so.
    """
    if course and not getattr(session, "course", None):
        try:
            from dataclasses import replace as _replace
            session = _replace(session, course=course)
        except Exception:
            pass
    results = []
    self_evo_thr = 4
    for sset in _load_sets():
        sid = sset["id"]
        thr = sset.get("pass_threshold", 4)
        if sid == "self_evolution_loop":
            self_evo_thr = thr        # scored last — it needs every other set's current result
            continue
        if sid in SKIP:
            results.append({"id": sid, "grader": "skip", "skipped": True,
                            "reason": SKIP[sid]})
            continue
        # 40-minute limit off → the recording-time set does not apply.
        if sid == "recording_time_budget" and not enforce_time:
            results.append({"id": sid, "grader": "skip", "skipped": True,
                            "reason": "40-minute limit is OFF for this run — not assessed"})
            continue
        # Depth mode (limit off): the doc is intentionally fuller, so the
        # deterministic per-line concision caps do not apply — skip rather than
        # false-fail (and avoid feeding a bogus rule into self-evolution).
        if sid == "conciseness" and not enforce_time:
            results.append({"id": sid, "grader": "skip", "skipped": True,
                            "reason": "depth mode (40-min limit OFF) — concision caps relaxed, not assessed"})
            continue
        # Two sets score a recorded Guided-mode regeneration: one asks whether the redo
        # ADDRESSED the reason, the other whether it stayed inside the reason's SCOPE.
        if sid in ("feedback_regeneration_adherence", "regeneration_scope_discipline"):
            scope_mode = sid == "regeneration_scope_discipline"
            evs = regen_log.events(session.number) or regen_log.events()
            if not evs:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": "no regeneration recorded — regenerate a chunk in Guided mode first"})
                continue
            if not use_llm:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": "LLM disabled (--no-llm)"})
                continue
            try:
                score, detail = _score_regen_event(evs[-1], sset, scope_mode=scope_mode)
            except Exception as e:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": f"llm error: {e}"})
                continue
            if scope_mode:
                mode = (evs[-1].get("scope") or {}).get("mode", "unknown")
                detail = f"[{mode}] {detail}"
            results.append({"id": sid, "grader": "llm_judge", "score": score,
                            "threshold": thr, "passed": score >= thr,
                            "detail": f"(scored {len(evs)} recorded regen event(s)) {detail}"})
            continue
        # True hybrids: the deterministic half always runs; the LLM half is combined as
        # the MINIMUM so neither can carry the set on its own.
        if sid in HYBRID:
            d_score, d_detail = HYBRID[sid](doc, session, sset)
            if d_score is NOT_APPLICABLE:
                results.append({"id": sid, "grader": "hybrid", "skipped": True,
                                "reason": d_detail})
                continue
            if use_llm:
                try:
                    l_score, l_detail = _llm_score(doc, session, sset,
                                                   enforce_time=enforce_time)
                    # A None deterministic half does not drag the minimum to nothing —
                    # it abstains, and the judge's verdict stands on its own.
                    score = l_score if d_score is None else min(d_score, l_score)
                    detail = (f"deterministic {'—' if d_score is None else d_score}/5 "
                              f"({d_detail}); judge {l_score}/5: {l_detail}")
                    grader = "hybrid" if d_score is not None else "llm_judge"
                except Exception as e:
                    if d_score is None:
                        results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                        "reason": f"{d_detail}; llm error: {e}"})
                        continue
                    score, detail, grader = d_score, f"{d_detail} (llm error: {e})", "deterministic"
            elif d_score is None:
                # Nothing left to run: the machine half abstained and the judge is off.
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": f"{d_detail}; judge half skipped (--no-llm)"})
                continue
            else:
                score, detail, grader = d_score, f"{d_detail} (judge half skipped: --no-llm)", "deterministic"
            results.append({"id": sid, "grader": grader, "score": score,
                            "threshold": thr, "passed": score >= thr, "detail": detail})
            continue
        if sid in DETERMINISTIC:
            score, detail = DETERMINISTIC[sid](doc, session, sset)
            grader = "deterministic"
            # A checker that abstains — either because the set does not apply to this
            # document, or because it has nothing it can assert and there is no judge
            # half to fall back on — is a SKIP. It must be one HERE rather than a None
            # reaching the comparison below, where it took the entire run down with a
            # TypeError on `None >= 4`.
            if score is None or score is NOT_APPLICABLE:
                results.append({"id": sid, "grader": grader, "skipped": True,
                                "reason": detail})
                continue
        elif use_llm:
            try:
                score, detail = _llm_score(doc, session, sset, enforce_time=enforce_time)
                grader = "llm_judge"
            except Exception as e:
                results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                                "reason": f"llm error: {e}"})
                continue
        else:
            results.append({"id": sid, "grader": "llm_judge", "skipped": True,
                            "reason": "LLM disabled (--no-llm)"})
            continue
        results.append({"id": sid, "grader": grader, "score": score,
                        "threshold": thr, "passed": score >= thr, "detail": detail})

    # Phase 2 — self-evolution from System B: distil this run's FAILURES into durable
    # rules (before scoring self_evolution_loop, so the rule count reflects them).
    learned = 0
    if learn and use_llm:
        try:
            scored_so_far = [r for r in results if not r.get("skipped")]
            learned = _learn_from_failures(session, scored_so_far)
        except Exception:
            learned = 0

    # self_evolution_loop — measured longitudinally against the prior session's result.
    current_fail_ids = {r["id"] for r in results
                        if not r.get("skipped") and not r["passed"]}
    results.append(_score_self_evolution(session, current_fail_ids, self_evo_thr))

    scored = [r for r in results if not r.get("skipped")]
    passed = [r for r in scored if r["passed"]]
    return {
        "session_no": session.number,
        "session_name": session.name,
        "overall_pass": len(passed) == len(scored) and len(scored) > 0,
        "scored": len(scored),
        "passed": len(passed),
        "skipped": len(results) - len(scored),
        "learned_rules_added": learned,
        "sets": results,
    }


def _load_or_make_doc(session_no: int, regenerate: bool):
    sessions = course_loader.load_sessions(None)
    _, cur, _ = course_loader.neighbours(session_no, sessions)
    out = config.harness()["output"]
    safe = out["docx_filename"].format(N=cur.number, SessionName=cur.name).replace("/", "-")
    doc_path = config.ROOT / out["dir"] / (safe.rsplit(".", 1)[0] + ".doc.json")
    if doc_path.exists() and not regenerate:
        print(f"Reusing saved doc: {doc_path.name}")
        return json.loads(doc_path.read_text()), cur
    print("Generating a fresh doc (no saved .doc.json found or --regenerate) …")
    res = pipeline.run(session_no, use_judge=False, do_sync=False)
    return res["doc"], cur


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", type=int, required=True)
    ap.add_argument("--no-llm", action="store_true", help="deterministic sets only (free)")
    ap.add_argument("--no-time-limit", action="store_true",
                    help="40-min limit off — skip the recording_time set")
    ap.add_argument("--regenerate", action="store_true", help="regenerate the doc even if saved")
    ap.add_argument("--no-learn", action="store_true",
                    help="do not turn eval-set failures into durable learned rules")
    args = ap.parse_args()

    doc, session = _load_or_make_doc(args.session, args.regenerate)
    report = run_on_doc(doc, session, use_llm=not args.no_llm,
                        enforce_time=not args.no_time_limit, learn=not args.no_learn)

    print(f"\n== EVAL SETS · Session {session.number}: {session.name} ==")
    for r in report["sets"]:
        if r.get("skipped"):
            print(f"  ⏭  {r['id']:34} skipped — {r['reason']}")
        else:
            mark = "✅" if r["passed"] else "❌"
            print(f"  {mark} {r['id']:34} {r['score']}/5 (>= {r['threshold']}) [{r['grader']}] — {r['detail'][:80]}")
    print(f"\n  RESULT: {report['passed']}/{report['scored']} passed"
          f" ({report['skipped']} skipped) → {'PASS' if report['overall_pass'] else 'FAIL'}")
    if report.get("learned_rules_added"):
        print(f"  🧠 self-evolution: learned {report['learned_rules_added']} new rule(s) "
              f"from this run's failures → applied to future generations")

    CASES_DIR.mkdir(parents=True, exist_ok=True)
    report["generated_at"] = datetime.now().isoformat(timespec="seconds")
    path = CASES_DIR / f"sets_result_session_{session.number}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  ↳ written to {path.relative_to(ROOT)}")
    sys.exit(0 if report["overall_pass"] else 1)
