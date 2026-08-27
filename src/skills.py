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


def block(course: str) -> str:
    """The skills, rendered for the prompt. Empty when the course has none.

    Labelled apart from the learned rules they travel with: a skill was WRITTEN for this
    course by a person, a learned rule was inferred from a correction. Same channel,
    different authority, and the model should be able to tell them apart.
    """
    rs = applicable(course)
    if not rs:
        return ""
    out = ["# COURSE SKILLS (authored for this course, highest priority)",
           "These were written for THIS COURSE by the person who owns it and approved "
           "before they took effect. They are REQUIREMENTS, not preferences, and they "
           "describe what this course needs that others do not.",
           "PRECEDENCE: where one conflicts with the default style guidance, THE SKILL "
           "WINS. Only the numbered HARD RULES about document STRUCTURE outrank them."]
    for r in rs:
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
        system=("You formalise a course author's rough requirements into atomic, "
                "checkable skills. You add NOTHING they did not ask for. Reply with "
                "JSON only."),
        user=prompt,
        model=m.get("judge", m["generator"]), max_tokens=1500, temperature=0.0,
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
        "Split the following course requirements into ATOMIC skills — one instruction "
        "each, in the author's own intent, no additions.\n\n"
        "Return JSON: {\"skills\": [{\"text\": \"...\", \"kind\": \"style|content|"
        "structure\", \"source_quote\": \"<the exact words from the input this came "
        "from>\", \"check\": {...}|null}]}\n\n"
        "RULES:\n"
        "- Invent NOTHING. Every skill must restate something the input actually asks "
        "for, and `source_quote` must be a literal substring of the input.\n"
        "- Split compound requirements; merge nothing.\n"
        f"- `check` is optional and must be one of: {', '.join(sorted(CHECKS))}. Add one "
        "only where the requirement is mechanically checkable; otherwise null.\n\n"
        f"REQUIREMENTS:\n{raw}")
    try:
        data = model(prompt)
        parsed = json.loads(data) if isinstance(data, str) else data
        proposed = parsed.get("skills") or []
    except Exception as e:
        raise ModelUnavailable(str(e) or e.__class__.__name__) from e

    out = []
    low = raw.lower()
    for p in proposed:
        if not isinstance(p, dict):
            continue
        text = " ".join(str(p.get("text") or "").split())
        quote = " ".join(str(p.get("source_quote") or "").split())
        # THE TRACEABILITY RULE. A skill whose quote is not in the input is one the model
        # wrote itself, and it must not be put in front of a reviewer as something they
        # asked for.
        if not text or not quote or quote.lower() not in low:
            continue
        kind = str(p.get("kind") or "style").lower()
        chk = p.get("check")
        ok, _why = validate_check(chk)
        out.append({"text": text, "kind": kind if kind in KINDS else "style",
                    "source_quote": quote, "check": chk if (ok and chk) else None})
    return out


def store_drafts(course: str, drafts: list[dict], *, created_by: str | None = None) -> int:
    """Store path-B drafts. They need approving like any other skill."""
    from . import db
    n = 0
    for d in drafts or []:
        if db.add_skill(course, d.get("text", ""), kind=d.get("kind") or "style",
                        source="requirements", created_by=created_by,
                        check=d.get("check"), source_quote=d.get("source_quote")):
            n += 1
    return n
