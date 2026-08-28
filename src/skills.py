"""COURSE SKILLS — the instructions a course is written under.

WHY THIS EXISTS. A React course needs things an Operating Systems course does not: show
the snippet, explain it line by line, keep one worked-example pattern throughout. The
harness is one set of instructions for every course, and `learning.py`'s rules are
INFERRED from corrections after a document has been reviewed. Neither is a place to say
up front what this course requires.

A skill is AUTHORED and APPROVED. Three ways in, two of them authoring:

  A  a person writes it                                    (source="user")
  B  a person writes rough requirements and the agent
     splits them into atomic skills, each quoting the
     words it came from                                    (source="requirements")
  C  imported from a course that already has it            (source="imported:<course>")

Nothing reaches the writer until a person approves it, and an EDIT sends a skill back to
draft — an approval is of the words that were approved.

WHY THE AGENT DOES NOT DERIVE SKILLS FROM PREREQUISITE DECKS. Those slides say what the
learner already KNOWS, not how this course should be WRITTEN. Asked to derive style from
them, a model produces fluent, plausible rules nobody asked for, they read well enough to
be approved, and they are then baked into the course permanently. Prerequisite decks feed
the assumed-knowledge context instead (see pptx_ingest.taught_digest), which is complete
and automatic rather than a sampled summary.
"""
from __future__ import annotations

import json

KINDS = ("style", "content", "structure")

# The assertions a skill may carry, and the fields each needs. A CLOSED vocabulary: an
# open one means arbitrary predicates from user input, failure messages nobody can
# maintain, and no way to tell a skill that is checkable from one that only looks it.
CHECKS = {
    "block_present": ("block",),        # e.g. every working_example slide has a code block
    "field_present": ("field",),        # e.g. every code block has a walkthrough
    "min_count": ("block", "min"),      # e.g. at least one code block in the document
    "forbidden_phrase": ("phrases",),   # e.g. never say "class component"
}


def validate_check(check) -> tuple[bool, str]:
    """(ok, why). An empty check is fine — most skills are prose the judge weighs."""
    if check in (None, {}):
        return True, ""
    if not isinstance(check, dict):
        return False, "a check must be an object"
    kind = check.get("assert")
    if kind not in CHECKS:
        return False, (f"unknown assertion {kind!r}. A skill's check must be one of: "
                       f"{', '.join(sorted(CHECKS))}. Anything else is prose — leave the "
                       f"check off and let the judge weigh it.")
    missing = [f for f in CHECKS[kind] if check.get(f) in (None, "", [])]
    if missing:
        return False, f"{kind} needs {', '.join(missing)}"
    return True, ""


def applicable(course: str) -> list[dict]:
    """The approved skills governing this course. Drafts and retired ones are excluded."""
    try:
        from . import db
        return db.approved_skills(course)
    except Exception:
        return []


# What each kind of skill governs, in the order a writer needs them: what the document
# is made of, then how it is shaped, then how it is written.
_KIND_HEADINGS = (
    ("content",   "WHAT THIS COURSE MUST CONTAIN"),
    ("structure", "HOW IT MUST BE STRUCTURED"),
    ("style",     "HOW IT MUST BE WRITTEN"),
)


def block(course: str) -> str:
    """The skills, composed as ONE BRIEF for the prompt. Empty when the course has none.

    Composed, not listed. This used to emit a flat run of bullets, and four terse
    fragments — "Show code snippets. / Explain the code line by line." — read to the
    model as a checklist to tick rather than a description of how this course teaches.
    Grouping them under what each kind governs makes it a brief, and puts requirements
    about CONTENT in front of requirements about WORDING, which is the order a writer
    needs them in.

    Labelled apart from the learned rules they travel with: a skill was WRITTEN for this
    course by a person, a learned rule was inferred from a correction. Same channel,
    different authority, and the model should be able to tell them apart.
    """
    rs = applicable(course)
    if not rs:
        return ""
    out = [f"# HOW '{course}' IS WRITTEN — the course brief",
           "Authored by the person who owns this course and approved before it took "
           "effect. This is what THIS course needs that others do not: it is the "
           "standing brief for every document produced for it, not a checklist to "
           "satisfy once.",
           "PRECEDENCE: where any of it conflicts with the default style guidance, THE "
           "BRIEF WINS. Only the numbered HARD RULES about document STRUCTURE outrank "
           "it."]
    by_kind: dict[str, list[dict]] = {}
    for r in rs:
        by_kind.setdefault((r.get("kind") or "style").lower(), []).append(r)
    for kind, heading in _KIND_HEADINGS:
        group = by_kind.pop(kind, [])
        if not group:
            continue
        out.append("")
        out.append(f"## {heading}")
        for r in group:
            out.append(f"- {r['text']}")
    for kind, group in by_kind.items():          # any kind added later, still shown
        out.append("")
        out.append(f"## {kind.upper()}")
        for r in group:
            out.append(f"- {r['text']}")
    return "\n".join(out) + "\n"


class ModelUnavailable(RuntimeError):
    """The drafting call itself failed — no answer came back, or it was not JSON.

    Kept DISTINCT from "the model answered and everything it proposed was untraceable".
    They are the same empty list but completely different problems: one is the service,
    one is what the person wrote. Collapsing them cost a release — every attempt at path B
    was reported to the author as "nothing could be drawn from your text" when in fact the
    call had never been made.
    """


def _default_model(prompt: str) -> dict:
    """The production drafting call. Small, cheap, deterministic.

    Separated from `from_requirements` so the seam that makes drafting testable is a
    one-argument callable, and so this — the part that has to agree with `llm.complete`'s
    signature — sits in one place where it can be read next to the other call sites.
    """
    from . import llm, config
    m = config.harness()["model"]
    raw = llm.complete(
        system=("You turn a course author's rough notes into the brief their course is "
                "written under. You merge what they said twice, you articulate what they "
                "meant, and you add NOTHING they did not ask for. Reply with JSON only."),
        user=prompt,
        model=m.get("judge", m["generator"]), max_tokens=2000, temperature=0.0,
        label="skills")
    return llm.extract_json(raw)


def from_requirements(raw: str, model=None) -> list[dict]:
    """Split free-text requirements into atomic draft skills. Path B.

    The agent FORMALISES; it does not invent. Every draft must quote the words it came
    from, and one that cannot is DROPPED — without the quote the approval step is a
    rubber stamp, because the reviewer has no way to tell a rule they asked for from one
    the model thought of.

    Returns [] only when the model answered and NOTHING it proposed survived that rule.
    Raises ModelUnavailable when the call or the parse failed — see the class.

    `model` is injected so this is testable without a network call; production uses
    `_default_model`.
    """
    raw = (raw or "").strip()
    if not raw:
        return []
    if model is None:
        model = _default_model
    prompt = (
        "A course author has written what their course needs, in a hurry. Turn it into "
        "the SKILLS that course is written under.\n\n"
        "Return JSON: {\"skills\": [{\"text\": \"...\", \"kind\": \"style|content|"
        "structure\", \"source_quotes\": [\"<exact words from the input>\", ...], "
        "\"check\": {...}|null}]}\n\n"
        "TWO JOBS, and the draft is no use unless you do both.\n\n"
        "1. MERGE RESTATEMENTS — AND ONLY RESTATEMENTS. The author repeats themselves: "
        "the same requirement said twice in different words is ONE skill. 'code snippets "
        "should be small' and 'small code snippets to be used' are the same rule — emit "
        "it once with BOTH phrases in source_quotes.\n"
        "   Two notes are the same rule only when they constrain THE SAME THING IN THE "
        "SAME WAY. Being about the same subject is NOT enough: 'keep snippets small' and "
        "'show the syntax' are both about code and are DIFFERENT requirements — one "
        "limits length, the other demands something be present. Obeying one does not "
        "obey the other. When in doubt, keep them separate: a duplicate is a nuisance, a "
        "swallowed requirement is a rule the author asked for and never got.\n\n"
        "2. ARTICULATE. Do not echo the author's phrasing back at them. They wrote rough "
        "notes with typos; you are writing the instruction a professional writer will "
        "work from. State what must happen, and where, and what it looks like when done "
        "— one or two full sentences, imperative, no hedging, standing alone without the "
        "author's note beside it. 'Show code snippets' is a restatement and is USELESS. "
        "'Introduce every concept that has a code form with the snippet itself before "
        "any prose about it; the code is the primary teaching object, not an "
        "illustration of the paragraph above it.' is a skill.\n\n"
        "THE ONE THING YOU MUST NOT DO IS INVENT. Articulating means making the author's "
        "intent explicit and actionable. It does NOT mean adding requirements they did "
        "not express. Every skill must trace to something in the input, and every string "
        "in `source_quotes` must be a LITERAL substring of it — copy the author's words "
        "exactly, typos and all. A skill you cannot quote for is dropped.\n\n"
        f"- `check` is optional and must be one of: {', '.join(sorted(CHECKS))}. Add one "
        "only where the requirement is mechanically checkable; otherwise null.\n\n"
        f"AUTHOR'S NOTES:\n{raw}")
    try:
        data = model(prompt)
        parsed = json.loads(data) if isinstance(data, str) else data
        proposed = parsed.get("skills") or []
    except Exception as e:
        raise ModelUnavailable(str(e) or e.__class__.__name__) from e

    out = []
    low = " ".join(raw.split()).lower()
    for p in proposed:
        if not isinstance(p, dict):
            continue
        text = " ".join(str(p.get("text") or "").split())
        # THE TRACEABILITY RULE. A skill must quote the author. Articulating their intent
        # is the job; adding requirements they never expressed is not, and without a
        # verifiable quote the approval step is a rubber stamp — the reviewer has no way
        # to tell a rule they asked for from one the model thought of.
        raw_quotes = p.get("source_quotes")
        if not isinstance(raw_quotes, list):
            raw_quotes = [p.get("source_quote")]
        quotes, seen = [], set()
        for q in raw_quotes:
            q = " ".join(str(q or "").split())
            if q and q.lower() in low and q.lower() not in seen:
                seen.add(q.lower())
                quotes.append(q)
        if not text or not quotes:
            continue
        kind = str(p.get("kind") or "style").lower()
        chk = p.get("check")
        ok, _why = validate_check(chk)
        out.append({"text": text, "kind": kind if kind in KINDS else "style",
                    "source_quote": quotes[0], "source_quotes": quotes,
                    "check": chk if (ok and chk) else None})
    return out


def articulate(text: str, model=None) -> dict | None:
    """Turn ONE line an author wrote into the instruction a writer works from. Path A.

    WHY PATH A NEEDED THIS TOO. "From my requirements" already did it: the author's rough
    notes go to the model, which articulates each one into a standing instruction and
    quotes the words it came from. "Write one" did not — whatever was typed went into the
    store verbatim and from there, verbatim, into the system prompt of every generation
    for that course. The live store shows exactly what that produces:

        "Explain the code, the student should be able to wrtite the code on their own
         after that for the concpet for any given problem reltated to it"

    That is a note to oneself, typos and all, being handed to the model as policy. Beside
    it, from the other path, sits "Provide code syntax examples wherever a concept
    requires them to be understood; syntax must be shown when needed to teach the
    material." Same author, same intent, ten seconds apart — the difference is entirely
    whether an articulation step ran. Two doors into one store should not produce two
    grades of instruction.

    ONE IN, ONE OUT. Unlike from_requirements this never splits: the author said they
    were adding a skill, and they get that skill, articulated. The words they typed are
    kept as source_quote and shown beside it, so what they approve is a rewrite they can
    check against their own sentence.

    Returns None when the model is unavailable or gave nothing usable — the caller then
    stores the author's own words, because losing an instruction is far worse than
    storing an unpolished one.
    """
    text = " ".join((text or "").split())
    if not text:
        return None
    if model is None:
        model = _default_model
    prompt = (
        "A course author has written ONE rule their course must be written under. Turn "
        "it into the instruction a professional writer will work from.\n\n"
        "Return JSON: {\"text\": \"...\", \"kind\": \"style|content|structure\"}\n\n"
        "ARTICULATE. They typed it in a hurry, with typos, as a note to themselves. You "
        "are writing what a writer who has never spoken to them will follow: state what "
        "must happen, where it applies, and what it looks like when it is done — one or "
        "two full sentences, imperative, no hedging, standing on its own without their "
        "note beside it. Fix the typos. Do not echo their phrasing back at them.\n\n"
        "DO NOT INVENT. Making their intent explicit is the job; adding requirements "
        "they did not express is not. If they said to explain the code, do not also "
        "decide how long the explanation runs, where it sits, or what it must mention. "
        "Every demand in your sentence must be one they made. When their note is already "
        "a clear instruction, return it essentially unchanged rather than embroidering "
        "it — a faithful copy beats a richer rule they did not ask for.\n\n"
        "`kind` is what the rule governs: content (what the document must contain), "
        "structure (how it is shaped), style (how it is written).\n\n"
        f"THE AUTHOR'S RULE:\n{text}")
    try:
        data = model(prompt)
        parsed = json.loads(data) if isinstance(data, str) else data
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    out = " ".join(str(parsed.get("text") or "").split())
    if not out:
        return None
    kind = str(parsed.get("kind") or "style").lower()
    return {"text": out, "kind": kind if kind in KINDS else "style",
            "source_quote": text, "source_quotes": [text]}


def store_drafts(course: str, drafts: list[dict], *, created_by: str | None = None) -> int:
    """Store path-B drafts. They need approving like any other skill."""
    from . import db
    n = 0
    for d in drafts or []:
        if db.add_skill(course, d.get("text", ""), kind=d.get("kind") or "style",
                        source="requirements", created_by=created_by,
                        check=d.get("check"), source_quote=d.get("source_quote"),
                        source_quotes=d.get("source_quotes")):
            n += 1
    return n
