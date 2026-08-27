"""PREREQUISITE COURSES — what the learner already knows, before session 1.

WHY THIS EXISTS. "Already taught" meant EARLIER SESSIONS OF THIS COURSE. A React course
whose learners have done a JavaScript course had no way to say so, so the writer had no
basis for deciding whether to define `const` — and it guessed, differently each session.
The page budget is fixed at 26, so every re-taught concept costs a page from something
new.

PREREQUISITE IS NOT PRIOR SESSION, and the rule differs in a way that matters:

  · a prior session's topic must NOT be re-taught. The learner met it in this course,
    under this course's numbering, and repeating it is exactly what the repetition
    guardrail exists to catch (pptx_ingest.taught_titles feeds it).
  · a prerequisite's topic may be REFERENCED freely. It is assumed ground and the
    document is expected to build on it by name.

Two blocks, two rules. Conflating them would either forbid a React doc from saying
"closure" because a JS deck mentioned one, or permit re-teaching last week's session.

A prerequisite is a COURSE THIS AGENT ALREADY HOLDS. Its decks are here, so nothing is
uploaded twice and a course library compounds: add Operating Systems and Computer
Networks as prerequisites of Distributed Systems and the assumed-knowledge index is
already complete.

WHAT THIS DELIBERATELY DOES NOT DO: derive SKILLS. Prerequisite decks say what the
learner KNOWS, not how this course should be WRITTEN. A model asked to derive style from
them produces fluent, plausible rules nobody asked for, which read well enough to be
approved and are then baked into the course permanently. Style comes from the course
owner (src/skills.py); these decks feed knowledge, completely and automatically.
"""
from __future__ import annotations


def courses(course: str) -> list[str]:
    """The prerequisite names, in order — both kinds."""
    try:
        from . import db
        return [p["prereq"] for p in db.prereqs(course) if p.get("prereq")]
    except Exception:
        return []


def _index(course: str) -> list[dict]:
    """[{prereq, kind, sessions:[{session_no, deck_title, topics}]}] per prerequisite.

    The only difference between the two kinds is WHERE THE DECKS ARE. An internal
    prerequisite is a course in this agent, so its decks are its own; an external one was
    taught elsewhere and its decks belong to the course that declared it. Everything after
    this point treats them identically.
    """
    from . import db, pptx_ingest
    out = []
    for row in db.prereqs(course):
        name, kind = row.get("prereq"), row.get("kind") or "course"
        if not name:
            continue
        try:
            # Everything the prerequisite ever taught — there is no "before session N"
            # here, because all of it precedes session 1 of this course.
            if kind == "external":
                sessions = pptx_ingest.taught_index(course, 10_000, prereq=name)
            else:
                sessions = pptx_ingest.taught_index(name, 10_000)
        except Exception:
            sessions = []
        if sessions:
            out.append({"prereq": name, "kind": kind, "sessions": sessions})
    return out


def assumed_topics(course: str) -> list[str]:
    """Every distinct topic the learner has already been taught, de-duplicated."""
    seen, out = set(), []
    for entry in _index(course):
        for s in entry["sessions"]:
            for t in s.get("topics") or []:
                if t.lower() not in seen:
                    seen.add(t.lower())
                    out.append(t)
    return out


def block(course: str, max_per_course: int = 60) -> str:
    """The assumed-knowledge block for the generation prompt. Empty when there are none.

    Says REFERENCE, not FORBID — the opposite instruction from the prior-session block it
    sits beside, and the reason the two are separate.
    """
    idx = _index(course)
    if not idx:
        return ""
    lines = [
        "ASSUMED KNOWLEDGE — taught in this course's PREREQUISITES, before session 1. "
        "The learner already knows all of it.",
        "  · Do NOT re-teach it. No slide should define or introduce something listed "
        "here as if the learner were meeting it for the first time.",
        "  · DO refer to it freely, by name, as established ground this session builds "
        "on — that is the difference between this and the prior-session list above, "
        "where repetition is forbidden outright. Using a term from here without "
        "explaining it is correct.",
        "  · If this session genuinely deepens one of these, say so and go past what the "
        "prerequisite covered.",
    ]
    for entry in idx:
        topics: list[str] = []
        for s in entry["sessions"]:
            topics += list(s.get("topics") or [])
        shown = topics[:max_per_course]
        more = len(topics) - len(shown)
        tag = " (taught elsewhere)" if entry.get("kind") == "external" else ""
        lines.append(f"  {entry['prereq']}{tag}: " + "; ".join(shown)
                     + (f" (+{more} more)" if more > 0 else ""))
    return "\n".join(lines) + "\n"


def coverage_report(course: str) -> dict:
    """What this course assumes that no prerequisite covers, and what it may re-teach.

    The one VISIBLE product of attaching prerequisites — the other effects are real but
    invisible (a prompt block, a repetition lookup, a judge input). Factual: it compares
    the curriculum's own takeaway text against the prerequisite topic index and reports
    matches. It does not judge, and it does not write rules.
    """
    from . import db
    known = assumed_topics(course)
    by_topic = {}
    for entry in _index(course):
        for s in entry["sessions"]:
            for t in s.get("topics") or []:
                by_topic.setdefault(t.lower(), entry["prereq"])

    overlaps = []
    for row in db.curriculum(course):
        for kt in row.get("key_takeaways") or []:
            low = str(kt).lower()
            for t in known:
                if t.lower() in low:
                    overlaps.append({"session_no": row["session_no"], "takeaway": kt,
                                     "topic": t, "prereq": by_topic.get(t.lower())})
    return {
        "course": course,
        "prereqs": courses(course),
        "topics_indexed": len(known),
        "overlaps": overlaps,
        "note": ("An overlap is a takeaway naming something a prerequisite already "
                 "taught. Often right — a session that deepens it — but worth seeing, "
                 "because the alternative is spending a page re-teaching it."),
    }
