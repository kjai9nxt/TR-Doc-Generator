"""COURSE SKILLS — the instructions a course is written under.

WHY THIS EXISTS. A React course needs things an Operating Systems course does not: show
the snippet, explain it line by line, keep one worked-example pattern throughout. The
harness is one set of instructions for every course, and `learning.py`'s rules are
INFERRED from corrections after a document has been reviewed. Neither is a place to say
up front what this course requires.

THE ONE LINE THE WHOLE MODULE TURNS ON:

    the curriculum says WHAT to teach, the prerequisite decks say what is ALREADY
    KNOWN, and a skill says HOW to teach it.

A skill is therefore never content. It never becomes an agenda line, a key takeaway, a
bullet or a coverage-map entry — it shapes the document those are written into. That is
not advice: `leaks()` below checks it on the assembled document and the guardrails fail
the run when a skill has been copied onto a slide.

WHAT A SKILL IS MADE OF. One skill, one CATEGORY, and as many INSTRUCTIONS as the author
grouped under it:

  teaching_flow        the sequence concepts are taught in
  teaching_guidelines  how they are explained — depth, pedagogy, what to avoid
  examples_visuals     what the course shows — examples, diagrams, tables, charts
  reviewer             what review keeps sending back on THIS course

Four related lines under "Teaching Guidelines" are ONE skill with four instructions, not
four skills. Splitting them loses the author's grouping and their ordering — which for a
teaching flow IS the instruction — and turns one approval into four.

WHERE A SKILL APPLIES. Two scopes, and the precedence between them is fixed:

    HARD RULES  >  COURSE REVIEWER SKILLS  >  SESSION SKILLS  >  COURSE SKILLS

The numbered hard rules about document structure can never be overridden. Below them, a
correction a reviewer made about this course outranks a rule written for one of its
sessions, which outranks the course's standing brief. Narrower wins.

THERE IS NO "EVERY COURSE" SCOPE, and there was. A rule that applies to every course
belongs in harness/system_prompt.md and harness/style_guide.md, which are read on every
generation for every course and are reviewed and versioned like the code beside them.
Offering a second place to write the same house rule made the two disagree, and the one
you had not checked was the one in force.

A skill is AUTHORED and APPROVED. Three ways in, two of them authoring:

  A  a person writes it                                    (source="user")
  B  a person writes rough requirements and the agent
     groups them into skills, each quoting the
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
import re

# --------------------------------------------------------------------------- #
# the vocabulary
# --------------------------------------------------------------------------- #
# WHAT A SKILL GOVERNS. A closed set, in the order a writer needs it: the shape of the
# session first, then how it is explained, then what it shows, then the corrections this
# course keeps needing. Each heading says what the group is FOR, because a bare label
# ("style") reads to the model as a bucket and a sentence reads as a brief.
CATEGORIES = {
    "teaching_flow": (
        "TEACHING FLOW — the sequence this course teaches in",
        "Follow this sequence when you lay the session out. It is the shape of the "
        "teaching, not a list of headings to print: the reader should experience the "
        "order, never read it."),
    "teaching_guidelines": (
        "TEACHING GUIDELINES — how this course explains",
        "How every explanation in this document is written: its depth, its pedagogy, "
        "what it leans on and what it stays away from."),
    "examples_visuals": (
        "EXAMPLES & VISUALS — what this course shows",
        "What the examples and the visual guidance in this document look like, and "
        "where they belong."),
    "reviewer": (
        "REVIEWER CORRECTIONS — what review keeps sending back",
        "Corrections a reviewer of THIS course has had to make before. They outrank "
        "everything below them and they belong to this course alone — never carry them "
        "to another one."),
}

# The three labels skills carried before there were categories. Rows written under them
# are still live and still correct; they keep their own headings rather than being
# guessed into a category they were never written for.
LEGACY_KINDS = {
    "content": "WHAT THIS COURSE MUST CONTAIN",
    "structure": "HOW IT MUST BE STRUCTURED",
    "style": "HOW IT MUST BE WRITTEN",
}

KINDS = tuple(CATEGORIES) + tuple(LEGACY_KINDS)   # everything `kind` may hold
SCOPES = ("course", "session", "global")

# The assertions a skill may carry, and the fields each needs. A CLOSED vocabulary: an
# open one means arbitrary predicates from user input, failure messages nobody can
# maintain, and no way to tell a skill that is checkable from one that only looks it.
CHECKS = {
    "block_present": ("block",),        # e.g. every working_example slide has a code block
    "field_present": ("field",),        # e.g. every code block has a walkthrough
    "min_count": ("block", "min"),      # e.g. at least one code block in the document
    "forbidden_phrase": ("phrases",),   # e.g. never say "class component"
}


def normalize_category(value: str | None) -> str | None:
    """The category a skill belongs to, or None when it predates categories."""
    v = " ".join(str(value or "").split()).lower().replace("&", " ").replace("-", "_")
    v = re.sub(r"[^a-z_ ]", "", v).strip().replace(" ", "_")
    v = re.sub(r"_+", "_", v)
    aliases = {
        "flow": "teaching_flow", "teachingflow": "teaching_flow",
        "teaching": "teaching_guidelines", "guidelines": "teaching_guidelines",
        "teaching_guideline": "teaching_guidelines",
        "examples": "examples_visuals", "visuals": "examples_visuals",
        "examples_and_visuals": "examples_visuals",
        "example_visuals": "examples_visuals",
        "reviewer_skills": "reviewer", "review": "reviewer",
        "reviewer_feedback": "reviewer",
    }
    v = aliases.get(v, v)
    return v if v in CATEGORIES else None


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


def instructions_of(skill: dict) -> list[str]:
    """A skill's own points. Its `text` when it has none — see db.add_skill.

    Each one is returned AS WRITTEN, newlines and all. This used to `" ".join(x.split())`
    every point, which is the same flattening the store had just been fixed to stop:
    the layout survived being saved and was then destroyed on its way into the prompt,
    which is the only place it was actually for.
    """
    lines = [str(i) for i in (skill.get("instructions") or []) if str(i or "").strip()]
    if lines:
        return lines
    one = str(skill.get("text") or "").strip()
    return [one] if one else []


# --------------------------------------------------------------------------- #
# precedence
# --------------------------------------------------------------------------- #
# The tiers, strongest first, and what each is called when it is shown to the model.
# HARD RULES are not here: they live in the system prompt and no skill can reach them.
_TIERS = (
    ("reviewer", "COURSE REVIEWER SKILLS",
     "corrections review keeps making on this course — these outrank everything below"),
    ("session", "SESSION SKILLS",
     "written for THIS session only"),
    ("course", "COURSE SKILLS",
     "the course's standing brief"),
)


def _tier_of(skill: dict) -> str:
    """Which precedence tier a stored skill sits in."""
    if (skill.get("scope") or "course") == "session":
        return "session"
    if normalize_category(skill.get("category") or skill.get("kind")) == "reviewer":
        return "reviewer"
    return "course"


def resolve(course: str, session=None) -> dict[str, list[dict]]:
    """The approved skills governing this run, bucketed by precedence tier.

    Resolution is done ONCE, here, so the writer, the judge, the guardrails and the
    leak check are all looking at the same set. When they resolved it separately the
    document was written under one set of rules and graded against another, which reads
    to a reviewer as the agent ignoring its own brief.
    """
    try:
        from . import db
        rows = db.approved_skills(course, session=session)
    except Exception:
        return {t: [] for t, _, _ in _TIERS}
    out: dict[str, list[dict]] = {t: [] for t, _, _ in _TIERS}
    for r in rows:
        out[_tier_of(r)].append(r)
    return out


def applicable(course: str, session=None) -> list[dict]:
    """The approved skills governing this course and session, STRONGEST FIRST.

    Drafts and retired ones are excluded. The order is the precedence order, so a
    consumer that can only take the list as given still reads the reviewer's correction
    before the course rule it overrides.
    """
    tiers = resolve(course, session)
    return [s for t, _, _ in _TIERS for s in tiers[t]]


def _ref_map(tiers: dict) -> dict:
    """`{skill id: "S1"}` in precedence order — the same order `applicable` returns.

    A SHORT NAME FOR ONE SKILL, because the grade is now per skill and something has to
    name the one being ruled on. Quoting the text back does not work: a skill may be a
    paragraph with four numbered instructions under it, and a model asked to echo it
    paraphrases. The database id is stable but says nothing to a person reading the
    report. So the brief carries the label, the verdict comes back against it, and the
    report joins the two.
    """
    out: dict = {}
    for i, sk in enumerate([s for t, _, _ in _TIERS for s in tiers.get(t) or []], start=1):
        sid = sk.get("id")
        if sid is not None:
            out[sid] = f"S{i}"
    return out


def numbered(course: str, session=None) -> list[dict]:
    """`applicable()`, each skill carrying the `ref` the brief and the verdicts use.

    Same list, same order, same rows — the label is the only addition, and it is derived
    from the position, so the brief the judge reads and the report a reviewer reads
    cannot be numbering two different things.
    """
    return [{**sk, "ref": f"S{i}"}
            for i, sk in enumerate(applicable(course, session), start=1)]


# --------------------------------------------------------------------------- #
# the brief
# --------------------------------------------------------------------------- #
_CATEGORY_ORDER = tuple(CATEGORIES) + tuple(LEGACY_KINDS)


def _heading_for(cat: str) -> tuple[str, str]:
    if cat in CATEGORIES:
        return CATEGORIES[cat]
    return (LEGACY_KINDS.get(cat, cat.upper()), "")


def _indented(text: str, first: str, rest: str) -> list[str]:
    """A possibly multi-line skill body, laid out under a marker.

    `text` is kept as the author wrote it — see db.skill_body — so it may be a
    paragraph, a list, or a paragraph followed by its points. Every line after the first
    is indented to sit under the marker, which is what stops a three-line skill reading
    as three separate skills once it is in the brief.
    """
    lines = str(text or "").split("\n")
    out = [f"{first}{lines[0]}"]
    for ln in lines[1:]:
        out.append(f"{rest}{ln}" if ln else "")
    return out


def _render(skill: dict, out: list[str], ref: str | None = None) -> None:
    """One skill: its own body, then the points the author grouped under it.

    `ref` prints the skill's short label ahead of it — used for the judge's copy of the
    brief, which has to be able to return a verdict naming one skill. The writer's copy
    is rendered without it: a label is a grading handle, and a prompt that reads like a
    numbered form invites the model to answer it as one.

    LAID OUT AS WRITTEN. A skill is a fragment of the prompt, so an author's paragraph
    stays a paragraph and their list stays a list — flattening it to one line here undid,
    at the last step, exactly what the store had just been fixed to preserve.

    The grouped instructions are NUMBERED, because for a teaching flow the order is the
    instruction and a bullet list says nothing about order. A single-instruction skill is
    just its body — a list of one reads as a checklist item rather than a rule.
    """
    lines = instructions_of(skill)
    text = str(skill.get("text") or "")
    marker = f"- [{ref}] " if ref else "- "
    pad = " " * len(marker)
    if len(lines) <= 1:
        out += _indented(lines[0] if lines else text, marker, pad)
        return
    out += _indented(text, marker, pad)
    for i, line in enumerate(lines, start=1):
        out += _indented(line, f"    {i}. ", "       ")


def reminder(course: str, session=None) -> str:
    """The brief again, at the END of the user message. Empty when there is none.

    WHY IT IS SAID TWICE. The brief reaches the writer as a block of the SYSTEM prompt,
    which gives it authority — and, on a real run, 2,591 characters of it sitting inside
    71,114 characters of hard rules, format spec and style guide, followed by ten
    thousand tokens of prior-deck context, and then a per-chunk instruction that never
    mentions it. Nothing was lost in transit; every line arrived. It was simply the one
    input with no presence in the task the model was actually answering, and the course
    owner's instructions came back half-applied.

    So it is repeated where the model is looking: last, after the instruction, framed as
    something to check the output against rather than as background. The cost is a few
    hundred tokens a call, which is the cheapest thing in this pipeline and buys the one
    input that is different for every course.
    """
    body = block(course, session, compact=True)
    if not body.strip():
        return ""
    return (
        "\n\n---\n"
        "# THE COURSE BRIEF — APPLY EVERY LINE OF IT TO WHAT YOU WRITE NOW\n"
        "This is repeated from your instructions because it is the part most often "
        "skimmed. It is not background: it is how THIS course is taught, and it is the "
        "one thing that makes this document different from the same session written for "
        "another course.\n"
        "BEFORE YOU RETURN, take every line below and check what you wrote against it. "
        "A line you did not apply is an instruction the course owner wrote and did not "
        "get. If two lines pull in different directions, the higher tier wins.\n"
        "And none of it may appear IN the document as content — it shapes how you write, "
        "it is never what you write about.\n\n"
        + body)


def block(course: str, session=None, compact: bool = False, refs: bool = False) -> str:
    """The skills, composed as ONE BRIEF for the prompt. Empty when there are none.

    Composed, not listed. This used to emit a flat run of bullets, and four terse
    fragments — "Show code snippets. / Explain the code line by line." — read to the
    model as a checklist to tick rather than a description of how this course teaches.
    Grouping them under what each governs makes it a brief; grouping THOSE by whose
    authority they carry makes the precedence readable instead of implied.

    Labelled apart from the learned rules they travel with: a skill was WRITTEN for this
    course by a person, a learned rule was inferred from a correction. Same channel,
    different authority, and the model should be able to tell them apart.
    """
    tiers = resolve(course, session)
    if not any(tiers.values()):
        return ""
    # `refs` labels each skill S1, S2, … — for the JUDGE's copy, which has to be able to
    # name the one skill a verdict is about. Derived from the same precedence ordering
    # `numbered()` uses, so a verdict on S3 and the report's third row are the same rule.
    ref_of = _ref_map(tiers) if refs else {}
    # `compact` drops the preamble and keeps the instructions — for `reminder`, where
    # the framing has already been said and only the lines themselves are wanted.
    out = [] if compact else [f"# HOW '{course}' IS WRITTEN — the course brief",
           "Authored by the person who owns this course and approved before it took "
           "effect. This is what THIS course needs that others do not: it is the "
           "standing brief for every document produced for it, not a checklist to "
           "satisfy once.",
           "",
           "THIS BRIEF IS HOW TO TEACH, NEVER WHAT TO TEACH. The curriculum decides what "
           "the session covers and the prerequisite decks decide what the learner "
           "already knows; nothing below adds a topic, a takeaway or an agenda line. "
           "Apply it to the writing and it is invisible in the result.",
           "NEVER PRINT IT. Not as a bullet, an agenda item, a key takeaway, a "
           "coverage-map entry, a slide title or a speaker note. A teaching flow of "
           "'problem → concept → mechanism → example' means the session MOVES that way; "
           "it does not mean a slide listing those four words. If the curriculum "
           "independently calls for that content, write it because the curriculum asked, "
           "not because this brief did.",
           "",
           "PRECEDENCE, strongest first: HARD RULES → COURSE REVIEWER SKILLS → SESSION "
           "SKILLS → COURSE SKILLS. Where any of it conflicts with the default style "
           "guidance, THE BRIEF WINS; only the numbered HARD RULES about document "
           "STRUCTURE outrank it. Where two parts of the brief conflict, the one from "
           "the higher tier wins outright."]

    for tier, label, gloss in _TIERS:
        group = tiers.get(tier) or []
        if not group:
            continue
        head = label
        if tier == "session" and session not in (None, ""):
            head = f"{label} — SESSION {session} ONLY"
        out += ["", f"## {head} ({gloss})"]
        by_cat: dict[str, list[dict]] = {}
        for s in group:
            cat = normalize_category(s.get("category")) \
                or (s.get("kind") or "style").lower()
            by_cat.setdefault(cat, []).append(s)
        for cat in _CATEGORY_ORDER:
            batch = by_cat.pop(cat, [])
            if not batch:
                continue
            heading, gloss_c = _heading_for(cat)
            out.append("")
            out.append(f"### {heading}")
            if gloss_c:
                out.append(gloss_c)
            for s in batch:
                _render(s, out, ref_of.get(s.get("id")))
        for cat, batch in by_cat.items():           # a category added later, still shown
            out += ["", f"### {cat.upper()}"]
            for s in batch:
                _render(s, out, ref_of.get(s.get("id")))
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# the leak check
# --------------------------------------------------------------------------- #
# WHY THIS IS A GATE AND NOT A NOTE IN THE PROMPT. Told "start with the problem, then the
# concept, then the mechanism", a model reliably writes a slide whose bullets are
# "Problem / Concept / Mechanism". It is the single most common way an instruction turns
# into content, it survives every re-wording of the prompt, and a reviewer reading the
# finished document cannot tell it from curriculum they forgot writing. So it is checked
# on the assembled document, where the leak is visible, rather than hoped for.
_STOP = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "with", "without",
    "for", "of", "to", "in", "on", "at", "by", "from", "as", "is", "are", "be", "it",
    "its", "this", "that", "these", "those", "should", "must", "always", "never",
    "every", "each", "any", "all", "use", "using", "used", "make", "do", "not", "no",
    "when", "where", "how", "what", "why", "one", "into", "before", "after", "first",
}
# What a teaching flow is written with. A flow is a sequence and authors write it as one.
_ARROWS = re.compile(r"\s*(?:->|=>|→|»|›|\||;|,|\bthen\b|\bfollowed by\b)\s*", re.I)
# An arrow is unambiguous; a comma is not. "Explain intuition first, use simple language,
# connect to the last session" is three guidelines that happen to be comma-separated, and
# splitting it into flow steps would go looking for a sequence nobody wrote. So a comma
# only makes a sequence when the author ALSO said this was the teaching flow.
_ARROWED = re.compile(r"->|=>|→|»|›")


def _tokens(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9']+", str(text or "").lower())
            if w not in _STOP and len(w) > 2]


def _visible_strings(doc: dict) -> list[tuple[str, str]]:
    """(where, text) for everything in the document a READER sees.

    Speaker notes included: they are read aloud, so an instruction that lands there has
    leaked just as surely as one on a slide.
    """
    out: list[tuple[str, str]] = []

    def add(where, value):
        if isinstance(value, str) and value.strip():
            out.append((where, value))
        elif isinstance(value, list):
            for i, v in enumerate(value, start=1):
                add(f"{where}[{i}]", v)

    for key in ("recap", "agenda", "key_takeaways", "upcoming_session", "closing"):
        add(key, doc.get(key))
    for sec in doc.get("sections") or []:
        for s in sec.get("slides") or []:
            tag = f"slide {s.get('n', '?')}"
            for f in ("title", "heading", "subheading", "analogy", "visual_guidance",
                      "speaker_notes"):
                add(f"{tag} {f}", s.get(f))
            for b in s.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text":
                    add(f"{tag} text", b.get("text"))
                elif b.get("type") == "bullets":
                    add(f"{tag} bullets", b.get("items"))
    return out


def _bullet_lists(doc: dict) -> list[tuple[str, list[str]]]:
    out = []
    for sec in doc.get("sections") or []:
        for s in sec.get("slides") or []:
            for b in s.get("content") or []:
                if isinstance(b, dict) and b.get("type") == "bullets":
                    items = [str(i) for i in (b.get("items") or []) if str(i).strip()]
                    if items:
                        out.append((f"slide {s.get('n', '?')}", items))
    if doc.get("agenda"):
        out.append(("agenda", [str(a) for a in doc["agenda"]]))
    if doc.get("key_takeaways"):
        out.append(("key takeaways", [str(a) for a in doc["key_takeaways"]]))
    return out


# How much of a line has to be the instruction before it counts as the instruction being
# copied out. Set from real output rather than taste: at 0.6 a legitimate bullet about
# the same subject as a skill was flagged, and below 4 content words almost anything
# matches something.
_LEAK_OVERLAP = 0.75
# …and how much of the INSTRUCTION the line has to account for. Without this second
# test a four-word bullet is a leak whenever a long instruction happens to contain its
# four words, which is how a gate starts failing correct documents: "Compare different
# approaches" is a real slide bullet, not a copy of "Use tables to compare different
# approaches, methods or options side by side." A leak is a RESTATEMENT — it has to
# look like the instruction from both ends.
_LEAK_COVERAGE = 0.5
_LEAK_MIN_WORDS = 4
_FLOW_MIN_STEPS = 3


def _copied(line: list[str], instruction: set[str]) -> bool:
    """Is this line the instruction, written out?

    TOKENS IN, not strings: the caller tokenises once and reuses. Strict about which
    shape each side is, because the permissive version — accept either, tokenise what is
    not already tokens — silently tokenised the *repr* of a list when the two were mixed
    up, matched nothing, and turned the gate off without failing anything.
    """
    if len(line) < _LEAK_MIN_WORDS or not instruction:
        return False
    shared = {w for w in line if w in instruction}
    return (sum(1 for w in line if w in instruction) / len(line) >= _LEAK_OVERLAP
            and len(shared) / len(instruction) >= _LEAK_COVERAGE)


def _flow_steps(instruction: str, *, is_flow: bool) -> list[str]:
    """The steps of a teaching flow, if the instruction is written as a sequence.

    `is_flow` says the author filed this under teaching_flow. Without that, only an
    explicit arrow counts — see _ARROWED.
    """
    if not (is_flow or _ARROWED.search(instruction)):
        return []
    parts = [p.strip(" .:-") for p in _ARROWS.split(instruction) if p.strip(" .:-")]
    return [p for p in parts if _tokens(p)] if len(parts) >= _FLOW_MIN_STEPS else []


def _same_step(item: set[str], step: set[str]) -> bool:
    """Is this list item the NAME of that flow step? Token SETS, for the reason above.

    A different test from _copied and it has to be: a flow step is two or three words
    ("build intuition"), and the leak prints one or two of them ("Intuition"). No
    line-level overlap ratio can see that — what gives it away is three of them in a row
    in the same list, which is what the caller counts.
    """
    if not item or not step:
        return False
    return len(item & step) / min(len(item), len(step)) >= 0.6


def leaks(doc: dict, skills: list[dict] | None) -> list[dict]:
    """Where the brief has been copied into the document. [] when it has not.

    Two shapes, because instructions leak in two ways:

      · A LINE IS THE INSTRUCTION — a bullet or a takeaway that restates a skill nearly
        word for word.
      · A LIST IS THE FLOW — a teaching flow written as 'problem → concept → mechanism'
        reappearing as three bullets naming its steps. Each step is two words, so no
        line-level check can see it; the sequence is what gives it away.

    Returns dicts, not strings, so a caller can report, count or repair them.
    """
    if not skills:
        return []
    found: list[dict] = []
    seen: set[tuple] = set()
    # Tokenised ONCE. This runs on every draft of every document, and the naive form
    # re-tokenised each of ~200 visible strings against each of ~50 instructions.
    visible = [(w, t, _tokens(t)) for w, t in _visible_strings(doc)]
    # SETS here, lists above: _same_step intersects, _copied counts a line's own tokens
    # and needs their multiplicity. Passing a list where a set was expected silently
    # re-tokenised the repr of the list and matched nothing at all.
    lists = [(w, items, [set(_tokens(i)) for i in items])
             for w, items in _bullet_lists(doc)]
    for sk in skills:
        sid = sk.get("id")
        is_flow = normalize_category(sk.get("category")) == "teaching_flow"
        for ins in instructions_of(sk):
            ins_tokens = set(_tokens(ins))
            for where, line, line_tokens in visible:
                if _copied(line_tokens, ins_tokens) and (sid, where) not in seen:
                    seen.add((sid, where))
                    found.append({
                        "skill_id": sid, "where": where, "text": line[:200],
                        "instruction": ins,
                        "why": "restates an instruction from the course brief"})
            steps = _flow_steps(ins, is_flow=is_flow)
            if not steps:
                continue
            step_tokens = [set(_tokens(st)) for st in steps]
            for where, items, item_tokens in lists:
                # DISTINCT STEPS in DISTINCT ITEMS. Counting either one alone lets a
                # single vague bullet match three steps, or three bullets all match the
                # same one, and neither of those is the flow being printed out.
                pairs = {(i, j) for i, item in enumerate(item_tokens)
                         for j, st in enumerate(step_tokens) if _same_step(item, st)}
                if (len({j for _, j in pairs}) >= _FLOW_MIN_STEPS
                        and len({i for i, _ in pairs}) >= _FLOW_MIN_STEPS
                        and (sid, where, "flow") not in seen):
                    seen.add((sid, where, "flow"))
                    found.append({
                        "skill_id": sid, "where": where,
                        "text": "; ".join(items[:6])[:200], "instruction": ins,
                        "why": "prints the steps of a teaching flow as a list"})
    return found


def leak_failures(doc: dict, skills: list[dict] | None) -> list[str]:
    """`leaks`, as guardrail failure messages. Each one says WHAT to do about it."""
    out = []
    for lk in leaks(doc, skills):
        out.append(
            f"{lk['where']}: “{lk['text']}” {lk['why']} — “{lk['instruction']}”. The "
            f"brief says HOW to teach this session, and it must never appear as its "
            f"content. Rewrite this to say what the LEARNER needs to know here, or "
            f"delete it if the curriculum does not call for it.")
    return out


# --------------------------------------------------------------------------- #
# authoring
# --------------------------------------------------------------------------- #
# WHAT AN ARTICULATION MUST NOT LOSE.
#
# The job of `articulate` is to fix the author's English, not to summarise them. It was
# asked for "one or two full sentences" and told not to echo the author's phrasing, and
# between those two instructions a model reliably compressed a paragraph carrying three
# examples into one clean sentence carrying none — and the author was then shown that
# sentence to approve, with no sign that anything had gone. An author who writes an
# example has written a REQUIREMENT; the example is the instruction, not decoration.
#
# So it is checked. Two things count as losing something:
#
#   · A SPECIFIC IS GONE. A number, a quoted phrase, a backticked or code-shaped token,
#     a name. These are the parts a rewrite has no business touching — "keep snippets
#     under 12 lines" is a different rule from "keep snippets short".
#   · IT IS MUCH SHORTER. Tightening is fine; a 60-word note coming back as 12 words is
#     not a tightening, it is a summary.
#
# On either, the author's own words are kept — see `articulate`.
_SPECIFIC = re.compile(
    r"`[^`]+`"                      # `useEffect`
    r"|\"[^\"]{2,}\"|“[^”]{2,}”"     # "quoted phrases"
    r"|\b\d+(?:\.\d+)?\b"           # 12, 1.5
    r"|\b\w+[._]\w+\b"              # os.path, max_pages
    r"|\b[a-z]+[A-Z]\w*\b"           # useEffect, keyTakeaway
)
# Below this share of the author's own length, a rewrite has stopped being a rewrite.
# Only applied to notes long enough for the distinction to mean anything — compressing
# a six-word note is not the failure this is looking for.
#
# 0.75, not the 0.6 it started at. Fixing someone's grammar does not cost a quarter of
# their words, and at 0.6 a 68-word note could come back at 44 with an entire worked
# example deleted and pass without comment.
_MIN_KEEP_RATIO = 0.75
_MIN_WORDS_TO_JUDGE_LENGTH = 22
# A sentence of the author's is "still there" when this share of its content words is.
# Generous, because the whole job is rewording: 'start with a navbar' becoming 'begin
# with a navbar' has to pass. Deleting the sentence scores zero and does not.
_SENTENCE_KEPT = 0.5
_MIN_WORDS_TO_JUDGE_SENTENCE = 5
# …and the ceiling on the other side, once the model was allowed to sharpen and
# restructure. Whichever of the two is larger applies, so a four-word note gets room to
# be said properly and a long one is not held to a percentage.
_MAX_GROWTH_RATIO = 3
_MAX_GROWTH_WORDS = 25


def _specifics(text: str) -> set[str]:
    return {m.group(0).strip("`\"“”'").lower()
            for m in _SPECIFIC.finditer(str(text or ""))}


def _stem(word: str) -> str:
    """Crude, and deliberately so. `example`/`examples`, `explain`/`explaining` are the
    same word for the purpose of "is this sentence still here", and a real stemmer is a
    dependency and a source of surprises for a check that only needs to be roughly
    right."""
    return word[:5] if len(word) > 5 else word


# How alike two words have to be to count as the same one. THE AUTHOR'S SPELLING IS THE
# WHOLE PROBLEM HERE: they wrote `exmaple`, `sesion`, `swich`, and the rewrite says
# `example`, `session`, `switch` — because correcting them is the job. Comparing the
# words as typed against the words as corrected reported the sentence as DELETED when it
# was sitting there in full, which is the check punishing the exact fix it was asked for.
_FUZZY = 0.8


def _covered(words: list[str], kept: set[str]) -> int:
    """How many of `words` survive in `kept`, allowing for the typos being fixed."""
    from difflib import SequenceMatcher
    stems = {_stem(k) for k in kept}
    hits = 0
    for w in words:
        if w in kept or _stem(w) in stems:
            hits += 1
            continue
        # Only against words of a similar length: `code` and `core` are one edit apart
        # and nothing else about them is alike.
        if any(SequenceMatcher(None, w, k).ratio() >= _FUZZY
               for k in kept if abs(len(k) - len(w)) <= 2):
            hits += 1
    return hits


_SENTENCE = re.compile(r"[^.!?\n]+")

# A LINE THAT IS CODE RATHER THAN A SENTENCE. The same rule the UI renders by: prose does
# not start with `<`, `{` or `}`, does not end with `{`, `}` or `;`, does not begin with
# `.cards`/`#id`/`@media`, and — once db.skill_body keeps indentation only where the
# author put it — is not indented.
_CODE_LINE = re.compile(r"^\s+\S|^[<{}]|[{};]\s*$|^[.#@][\w-]")


def _code_blocks(text: str) -> list[list[str]]:
    """Runs of two or more code-ish lines: the snippets the author pasted.

    Two lines minimum, because one line ending in a semicolon is far more likely to be a
    sentence than a program.
    """
    out, run = [], []
    for line in str(text or "").split("\n"):
        if line.strip() and _CODE_LINE.search(line):
            run.append(line.strip())
        elif not line.strip() and run:
            continue                      # a blank line inside a snippet stays inside it
        else:
            if len(run) > 1:
                out.append(run)
            run = []
    if len(run) > 1:
        out.append(run)
    return out


def _mangled_code(src: str, out: str) -> list[str]:
    """Lines of the author's snippets that did not come back as they were written.

    WHY THIS IS ITS OWN CHECK. Every other one is about words, and a paraphrase of code
    keeps the words: '.cards with display: flex and gap: 20px' contains `.cards`,
    `display`, `flex`, `gap` and `20px`, so the specifics check passes, the sentence
    check passes, and the length is fine. What is gone is the SNIPPET — the thing the
    author actually pasted, and the only part of the note a learner would have seen on
    screen. Prose describing code is not code.
    """
    flat = {" ".join(l.split()) for l in str(out or "").split("\n")}
    missing = []
    for block in _code_blocks(src):
        for line in block:
            if " ".join(line.split()) not in flat:
                missing.append(line)
    return missing


def _dropped_sentences(src: str, out: str) -> list[str]:
    """The author's sentences that have no counterpart in the rewrite.

    THE CHECK THAT ACTUALLY IMPLEMENTS "KEEP EVERYTHING". The others are proxies: a
    specific is a thing a rewrite must not touch, and the length ratio catches wholesale
    summarising. Neither sees a single sentence being quietly dropped out of the middle
    of a long note — and the sentence most likely to go is the EXAMPLE, because it reads
    to a model as illustration rather than as instruction. A 68-word note came back at 44
    with the worked example deleted, and every check passed.

    Sentences shorter than a few content words are not judged: there is not enough of
    them left after stopwords to tell a rewrite from a deletion.
    """
    kept = set(_tokens(out))
    lost = []
    for sentence in _SENTENCE.findall(str(src or "")):
        words = _tokens(sentence)
        if len(words) < _MIN_WORDS_TO_JUDGE_SENTENCE:
            continue
        if _covered(words, kept) / len(words) < _SENTENCE_KEPT:
            lost.append(" ".join(sentence.split()))
    return lost


_LIST_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")


def _shape(text: str) -> tuple[int, int]:
    """(list lines, blank-line-separated blocks) — the layout, as a pair of counts."""
    lines = str(text or "").split("\n")
    bullets = sum(1 for ln in lines if _LIST_LINE.match(ln))
    blocks = len([b for b in re.split(r"\n\s*\n", str(text or "").strip()) if b.strip()])
    return bullets, blocks


def lossy(src: str, out: str) -> str:
    """Why `out` is not a faithful rewrite of `src`, or "" when it is.

    Three ways to be unfaithful, and the author is shown the result to APPROVE, so all
    three have to be caught before they are shown rather than after:
      · a SPECIFIC is gone — a number, a name, a quoted phrase;
      · it is much SHORTER — a summary wearing a rewrite's clothes;
      · the SHAPE is gone — the author's list came back as prose.
    The third is not cosmetic. This text is a fragment of the prompt a writer works
    from, and a list of four rules reads as four rules; run together in a paragraph it
    reads as one sentence with some commas in it, which is not what was approved.
    """
    src, out = str(src or ""), str(out or "")
    low = out.lower()
    missing = sorted(x for x in _specifics(src) if x and x not in low)
    if missing:
        return ("it drops what the author actually specified: "
                + ", ".join(f"\u201c{m}\u201d" for m in missing[:6]))
    mangled = _mangled_code(src, out)
    if mangled:
        return ("it turns the author's CODE into prose about the code. These lines were "
                "pasted as a snippet and have to come back exactly as they were written, "
                "on their own lines: "
                + " | ".join(mangled[:5]))
    gone = _dropped_sentences(src, out)
    if gone:
        return ("it deletes what the author wrote. These sentences of theirs have no "
                "counterpart in your version: "
                + "; ".join(f"\u201c{g}\u201d" for g in gone[:4]))
    n_src, n_out = len(src.split()), len(out.split())
    if n_src >= _MIN_WORDS_TO_JUDGE_LENGTH and n_out < _MIN_KEEP_RATIO * n_src:
        return (f"it is a summary, not a rewrite: {n_src} words became {n_out}. "
                f"Every separate thing the author asked for has to survive.")
    # …AND THE OTHER DIRECTION. Once the model was allowed to sharpen a vague note and
    # to give content the structure it deserves, "make the analogies good" came back as
    #   four confident rules about mapping, domains and testing — none of which the
    # author had written. Growing is normal and expected; growing FOURFOLD out of a
    # four-word note is the model writing the brief instead of the author. The allowance
    # is generous on purpose: a long note may still be rearranged freely, and a short one
    # gets a flat +25 words of room to be said properly.
    if n_out > max(_MAX_GROWTH_RATIO * n_src, n_src + _MAX_GROWTH_WORDS):
        return (f"it is an expansion, not a rewrite: {n_src} words became {n_out}, and "
                f"the extra is requirements the author never gave. Sharpen what they "
                f"wrote; do not write the rest of the brief for them.")
    src_bullets, src_blocks = _shape(src)
    out_bullets, out_blocks = _shape(out)
    if src_bullets >= 2 and out_bullets < 2:
        return (f"the author wrote {src_bullets} points as a LIST and you ran them "
                f"together into prose. Give the list back as a list, one point per "
                f"line, each starting with '- '.")
    if src_blocks >= 2 and out_blocks < 2:
        return (f"the author wrote {src_blocks} separate blocks, and you ran them into "
                f"one. Keep the blank line between them.")
    return ""


def _instructions_from(parsed: dict, _db) -> list[str]:
    """The `instructions` array, as the store holds them: one string per rule.

    Each entry may be an object with its own `title` — 'Explain in Simple Language
    First' — and the `text` saying what it requires. They are joined with a newline,
    which is the shape everything downstream already renders as a labelled step: the
    title carries the weight, the sentence sits under it.
    """
    out = []
    for it in parsed.get("instructions") or []:
        if isinstance(it, dict):
            title = " ".join(str(it.get("title") or "").split())
            lines = it.get("lines")
            body = _db.skill_body("\n".join(str(x) for x in lines)
                                  if isinstance(lines, list) else it.get("text"))
            one = f"{title}\n{body}".strip() if title else body
        else:
            one = _db.skill_body(it)
        if one:
            out.append(one)
    return out


def _assembled(text: str, instructions: list[str]) -> str:
    """The whole skill as one document, for checking against what the author wrote.

    `lossy` counts list lines and blank-line-separated blocks, and a skill whose rules
    live in a separate array has neither until they are put back together. Without this
    a perfectly structured answer — a name and ten instructions — looked to the check
    like one unbroken paragraph, and was rejected for flattening the very list it had
    just built.
    """
    if not instructions:
        return text
    return text + "\n\n" + "\n\n".join(f"- {i}" for i in instructions)


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
                "written under. You fix their English, sharpen what is vague, and give "
                "the content the structure it deserves. You merge what they said twice. "
                "You add NO REQUIREMENT they did not ask for and you DROP NOTHING they "
                "did — every requirement and every example they wrote survives, however "
                "long that makes it. Reply with JSON only."),
        user=prompt,
        model=m.get("judge", m["generator"]), max_tokens=2000, temperature=0.0,
        label="skills")
    return llm.extract_json(raw)


_CATEGORY_BRIEF = (
    "  teaching_flow        the sequence concepts are taught in — how a session opens, "
    "where examples land, how it closes\n"
    "  teaching_guidelines  how the content is explained — depth, pedagogy, what to "
    "emphasise, what to avoid, how new material joins old\n"
    "  examples_visuals     what the course shows — kinds of example, diagrams, tables, "
    "charts, and when NOT to use one\n"
    "  reviewer             a correction from review: a mistake to stop making, a check "
    "this course keeps failing\n")


def from_requirements(raw: str, model=None) -> list[dict]:
    """Turn free-text requirements into DRAFT skills, grouped by category. Path B.

    THE UNIT IS THE SKILL, NOT THE INSTRUCTION. This used to split every sentence into
    its own atomic skill, and an author who wrote four lines under "Teaching Guidelines"
    got four skills to approve, four entries in the brief, and no record that they were
    one instruction with four parts. Ordering — which for a teaching flow IS the
    instruction — was lost outright. Now the related lines stay together as one skill's
    `instructions`, in the order they were written.

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
        "the SKILLS that course is written under — instructions about HOW it is "
        "taught.\n\n"
        "Return JSON: {\"skills\": [{\"category\": \"teaching_flow|teaching_guidelines|"
        "examples_visuals|reviewer\", \"text\": \"<what this skill is, in one "
        "sentence>\", \"instructions\": [{\"title\": \"...\", \"lines\": [\"...\", "
        "\"...\"]}, ...], \"kind\": \"style|content|structure\", \"source_quotes\": "
        "[\"<exact words from the input>\", ...], \"check\": {...}|null}]}\n\n"
        "EACH INSTRUCTION IS AN OBJECT, and `lines` is its layout — ONE ARRAY ELEMENT "
        "PER LINE, an empty string \"\" for a blank line. `title` is the rule's own short "
        "name and may be left out. This shape exists so that a snippet the author pasted "
        "can come back as a snippet: a multi-line block of code cannot be expressed as "
        "one JSON string, and asking for one is what turned an author's HTML into a "
        "sentence describing their HTML.\n\n"
        "THE CATEGORIES:\n" + _CATEGORY_BRIEF + "\n"
        "THREE JOBS, and the draft is no use unless you do all three.\n\n"
        "1. GROUP. ONE SKILL PER CATEGORY. Everything the author said about how the "
        "session should be sequenced is ONE teaching_flow skill whose `instructions` are "
        "their lines IN THE ORDER THEY WROTE THEM; everything they said about explaining "
        "is ONE teaching_guidelines skill; and so on. Do NOT emit one skill per "
        "sentence. The author's grouping and ordering are part of what they said — a "
        "flow whose steps have been scattered across four skills is no longer a flow. "
        "`text` names what the skill is ('the sequence this session is taught in'); the "
        "`instructions` carry the substance.\n\n"
        "2. MERGE RESTATEMENTS — AND ONLY RESTATEMENTS. Inside a category, the author "
        "repeats themselves: the same requirement said twice in different words is ONE "
        "instruction. 'code snippets should be small' and 'small code snippets to be "
        "used' are the same rule — write it once with BOTH phrases in source_quotes.\n"
        "   Two notes are the same rule only when they constrain THE SAME THING IN THE "
        "SAME WAY. Being about the same subject is NOT enough: 'keep snippets small' and "
        "'show the syntax' are both about code and are DIFFERENT requirements — one "
        "limits length, the other demands something be present. Obeying one does not "
        "obey the other. When in doubt, keep them as separate instructions: a duplicate "
        "is a nuisance, a swallowed requirement is a rule the author asked for and never "
        "got.\n\n"
        "3. ARTICULATE, WITHOUT LOSING ANYTHING. They wrote rough notes with typos; you "
        "are writing the instructions a professional writer will work from. State what "
        "must happen, and where, and what it looks like when done — imperative, no "
        "hedging, standing alone without the author's note beside it. 'Show code "
        "snippets' is a restatement and is USELESS. 'Introduce every concept that has a "
        "code form with the snippet itself before any prose about it; the code is the "
        "primary teaching object, not an illustration of the paragraph above it.' is an "
        "instruction.\n"
        "   Each instruction is ONE POINT; the `instructions` list IS the structure, so "
        "do not put bullet characters or numbering at the start of a line. Where a point "
        "needs a paragraph and then its own sub-points, or a paragraph and then a code "
        "snippet, put each line in its own `lines` element.\n"
        "   CODE THE AUTHOR PASTED IS QUOTED MATERIAL. Every snippet comes back EXACTLY "
        "as they wrote it — line for line, indent for indent, one `lines` element per "
        "line of code. NEVER describe a snippet in words instead of reproducing it: "
        "\u201ca container div with class 'cards' holding three child divs\u201d is not "
        "the author's HTML, it is a sentence about their HTML, and the snippet is the "
        "thing a learner would have seen. This is checked, and a described snippet is "
        "sent back to you.\n"
        "   THERE IS NO LENGTH LIMIT, and being shorter than the author is not a virtue. "
        "EVERY SENTENCE THEY WROTE ENDS UP IN ONE OF THE SKILLS — this is checked across "
        "all of them together, and an answer that drops any of it is sent back. Every "
        "EXAMPLE they gave is carried into the instruction it belongs to: an author who "
        "gives an example has stated a requirement about which example to use, and it is "
        "the first thing you will be tempted to cut because it reads as illustration. "
        "Every number, name, quoted phrase and piece of code they wrote appears in your "
        "version — 'under 12 lines' may not become 'short'. Nothing they said is dropped "
        "for being long-winded: fix the wording, keep the substance.\n\n"
        "THESE ARE INSTRUCTIONS ABOUT HOW TO TEACH, NEVER ABOUT WHAT TO TEACH. If the "
        "author names a topic their course covers, that is CURRICULUM and does not "
        "belong here — skip it. A skill shapes how any topic is taught.\n\n"
        "MAKE THEM CLEARER, AND GIVE THEM STRUCTURE. Where a note is ambiguous about "
        "what the writer must DO, say it precisely; where it is missing the condition "
        "its own words imply, supply that condition. Where several parallel requirements "
        "are running together in one of their sentences, split them into separate "
        "instructions — a brief of four rules has to read as four rules. A human "
        "approves every one of these before it takes effect.\n\n"
        "THE ONE THING YOU MUST NOT DO IS INVENT. Articulating means making the author's "
        "intent explicit and actionable. It does NOT mean adding requirements they did "
        "not express. Every skill must trace to something in the input, and every string "
        "in `source_quotes` must be a LITERAL substring of it — copy the author's words "
        "exactly, typos and all. A skill you cannot quote for is dropped.\n\n"
        f"- `check` is optional and must be one of: {', '.join(sorted(CHECKS))}. Add one "
        "only where the requirement is mechanically checkable; otherwise null.\n\n"
        "NEVER RESTATE WHERE A SKILL APPLIES. Which course and which session are recorded "
        "separately; write what must happen, not where.\n\n"
        f"AUTHOR'S NOTES:\n{raw}")

    # SAME TWO TRIES AS `articulate`, for the same reason: the commonest way this fails
    # is not inventing but SUMMARISING, and a model that dropped an author's example
    # keeps it once the example is named back at it. Checked over ALL the drafts
    # together, because this path splits one note across several skills and a specific
    # only has to survive into one of them.
    nudge = ""
    for _attempt in range(2):
        out, dropped = _draft_once(model, prompt + nudge, raw)
        if not dropped:
            return out
        nudge = ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED: it drops what the author "
                 "actually specified — " + ", ".join(f"\u201c{d}\u201d" for d in dropped[:6])
                 + ". Do it again, and carry every requirement, every example, every "
                 "number and every name they wrote into one of the skills. Fix their "
                 "English; do not shorten them.")
    # Twice, and it still dropped something. The drafts are returned anyway: each one is
    # traceable to the author's words and is shown beside them for approval, so a partial
    # draft the author can see and edit beats making them retype the whole note.
    return out


def _draft_once(model, prompt: str, raw: str) -> tuple[list[dict], list[str]]:
    """One drafting call: (drafts, the author's specifics that no draft carried)."""
    try:
        data = model(prompt)
        parsed = json.loads(data) if isinstance(data, str) else data
        proposed = parsed.get("skills") or []
    except Exception as e:
        raise ModelUnavailable(str(e) or e.__class__.__name__) from e

    out: list[dict] = []
    by_cat: dict[str, dict] = {}
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
        # The same reader path A uses, so an instruction may be a plain string, or an
        # object carrying its own `title` and `lines` — which is the only shape that can
        # hold a pasted snippet.
        from . import db as _db
        lines = _instructions_from(p, _db)
        kind = str(p.get("kind") or "style").lower()
        cat = normalize_category(p.get("category"))
        chk = p.get("check")
        ok, _why = validate_check(chk)
        draft = {"text": text, "kind": kind if kind in KINDS else "style",
                 "category": cat, "instructions": lines,
                 "source_quote": quotes[0], "source_quotes": quotes,
                 "check": chk if (ok and chk) else None}
        # ONE SKILL PER CATEGORY, enforced here and not only asked for. A model that
        # returns three teaching_guidelines skills has done what the old prompt taught
        # every model to do, and the author would get back exactly the scattering this
        # rewrite exists to stop. Merging keeps the order they arrived in.
        #
        # An UNCATEGORISED draft is never merged: with no category there is nothing to
        # say two of them belong together, and guessing would fuse unrelated rules.
        if cat and cat in by_cat:
            prev = by_cat[cat]
            prev["instructions"] += [ln for ln in (lines or [text])
                                     if ln not in prev["instructions"]]
            prev["source_quotes"] += [q for q in quotes
                                      if q not in prev["source_quotes"]]
            prev["check"] = prev["check"] or draft["check"]
            continue
        if cat:
            draft["instructions"] = lines or [text]
            by_cat[cat] = draft
        out.append(draft)
    # A merged skill's `text` named only the first of its parts. Once it carries several
    # instructions it needs a sentence that names the whole group, or the brief shows a
    # heading that describes a quarter of what is under it.
    for cat, d in by_cat.items():
        if len(d["instructions"]) > 1:
            d["text"] = _group_text(cat, d["text"])
    # Everything the drafts say, checked against everything the author specified.
    said = "\n".join(d["text"] + "\n" + "\n".join(d["instructions"]) for d in out)
    dropped = sorted(x for x in _specifics(raw) if x and x not in said.lower())
    # …and the author's own SENTENCES, across all the drafts together — one note becomes
    # several skills here, so a sentence only has to survive into one of them. Without
    # this the split hides a deletion: four tidy skills look complete, and the example
    # that was in the fifth sentence is simply gone.
    dropped += _dropped_sentences(raw, said)
    # …and the SNIPPETS. A paraphrase of code keeps every word of it, so nothing above
    # sees the loss: what is gone is the snippet itself.
    dropped += _mangled_code(raw, said)
    return out, dropped


_GROUP_TEXT = {
    "teaching_flow": "The sequence this course teaches in.",
    "teaching_guidelines": "How this course explains its content.",
    "examples_visuals": "The examples and visuals this course uses.",
    "reviewer": "Corrections review keeps making on this course.",
}


def _group_text(category: str, fallback: str) -> str:
    return _GROUP_TEXT.get(category) or fallback


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

    HOW FAR IT MAY GO. It fixes the grammar, sharpens what is vague, and gives the
    content the structure it deserves — three parallel requirements running together in
    one of the author's sentences come back as three lines, because a brief of four
    rules has to read as four rules. What it may NOT do is add a requirement nobody
    asked for. That line is where it is because a human approves every skill before it
    takes effect and can see their own words beside the rewrite: a sharper version they
    can check is worth more than a timid one that needed no thought, and a smuggled
    requirement is the one thing that review cannot easily catch.

    IT MAY NOT SHORTEN THEM. The first version of this prompt asked for "one or two
    full sentences" and told the model not to echo the author's phrasing, and between
    those two instructions a paragraph carrying three worked examples came back as one
    clean sentence carrying none. The author was then shown that sentence to approve,
    with nothing to say anything had been dropped — which is the worst possible shape for
    this bug, because approving it is how the loss becomes permanent. The prompt now says
    to edit the English and keep the substance, and `lossy()` checks that it did: one
    retry naming exactly what went missing, and after that the author's own words.

    Returns None when the model is unavailable, gave nothing usable, or could not do it
    without dropping something — the caller then stores the author's own words, because
    an unpolished instruction is worth far more than a polished one that lost half of
    what it was for.
    """
    # THE AUTHOR'S LAYOUT, KEPT. This was `" ".join(text.split())` — the note was
    # flattened into one line before the model ever saw it, so "keep their shape" was an
    # instruction about a shape that had already been destroyed, the check for it could
    # never fire (there were no list lines left to miss), and `source_quote` recorded a
    # run-on paragraph as "your own words". One line, three bugs.
    from . import db as _db
    text = _db.skill_body(text)
    if not text:
        return None
    if model is None:
        model = _default_model
    prompt = (
        "A course author has written ONE SKILL their course must be written under — an "
        "instruction about HOW the course is taught. Turn it into the instruction a "
        "professional writer will work from.\n\n"
        "Return JSON:\n"
        "  {\"name\": \"...\",\n"
        "   \"instructions\": [{\"title\": \"...\", \"text\": \"...\"}, ...],\n"
        "   \"lines\": [\"...\", \"...\"],\n"
        "   \"category\": \"teaching_flow|teaching_guidelines|examples_visuals|reviewer\",\n"
        "   \"kind\": \"style|content|structure\"}\n\n"
        "WHICH OF `instructions` AND `lines` YOU FILL IN IS THE WHOLE DECISION:\n\n"
        "  · SEVERAL RULES → `instructions`, one entry each, and a short `name` for the "
        "whole skill. This is the common case and the one that is got wrong: a course "
        "brief is usually a RUN OF NAMED RULES — a short title, then a sentence saying "
        "what it means, then the next one. Ten of those are ONE skill with TEN "
        "instructions, not one long document. `title` is the rule's own name (leave it "
        "out if the author gave none) and `text` is what it requires. `name` is what the "
        "whole set is about, in three to eight words — NEVER the first rule's title, "
        "because the first rule is one of ten and naming the set after it hides the "
        "other nine.\n"
        "  · ONE INSTRUCTION → `lines`, and leave `instructions` empty. `lines` is the "
        "layout, one array element per line, an empty string \"\" for a blank line "
        "between blocks. Use it when the author wrote a single instruction, or a "
        "paragraph and its own sub-points that only make sense together.\n\n"
        "Answering in an array either way is deliberate: asking for newlines inside one "
        "long JSON string does not work and the answer comes back as prose every "
        "time.\n\n"
        "YOU ARE EDITING THEIR ENGLISH, NOT SUMMARISING THEM. They typed it in a hurry, "
        "with typos, as a note to themselves. Give it back as what a writer who has "
        "never spoken to them will follow: correct, unambiguous, imperative, no hedging, "
        "standing on its own without their note beside it. Fix the typos and the grammar. "
        "Say the same things, properly.\n\n"
        "DELETE NOTHING. This is the rule that matters most, it is the one most easily "
        "broken, and it is checked sentence by sentence — an answer that drops any of "
        "the author's sentences is rejected and sent back to you.\n"
        "   EVERY SENTENCE THEY WROTE HAS A COUNTERPART IN YOURS. You may re-order it, "
        "re-word it, and break it into a list; you may not decide that some of it was "
        "not worth keeping. RESTRUCTURING IS MOVING TEXT, NOT CHOOSING WHICH OF IT TO "
        "KEEP: if you turn a paragraph into bullets, every sentence of that paragraph "
        "ends up in one of the bullets. Keeping the tidy bullets and quietly dropping "
        "the paragraph around them is the exact failure this is about.\n"
        "   THE EXAMPLE IS THE FIRST THING YOU WILL BE TEMPTED TO DROP, because it reads "
        "as illustration. It is not. An author who writes an example has written a "
        "REQUIREMENT — that example is to be used — and cutting it removes the most "
        "concrete instruction in the note. 'For example, when teaching flexbox start "
        "with a navbar that collapses on mobile' is a rule about which example to use, "
        "and it survives in full.\n"
        "   CODE THE AUTHOR PASTED IS QUOTED MATERIAL. Reproduce every snippet exactly "
        "as they wrote it, line for line and indent for indent. Do not reformat it, "
        "shorten it, rename anything in it, or 'improve' it — their snippet IS the "
        "example the skill is about, and a tidied version of it is a different "
        "example.\n"
        "   So does every number, name, quoted phrase and piece of code: \u201ckeep "
        "snippets under 12 lines\u201d is a different rule from \u201ckeep snippets "
        "short\u201d, and you may not trade one for the other.\n"
        "   THERE IS NO LENGTH LIMIT. If their note carries five requirements and three "
        "examples, your version carries five requirements and three examples, and it "
        "will be as long as that takes. Coming back shorter than what they wrote is the "
        "failure this is warning you about; longer is fine.\n\n"
        "KEEP THEIR WORDS WHERE THEY ARE ALREADY RIGHT, and this is enforced too. You "
        "are not being asked to re-word for the sake of it. Where a phrase of theirs "
        "already says the thing clearly, keep that phrase — 'student' does not need to "
        "become 'learner', and 'on their own' does not need to become 'unaided'. Change "
        "what is WRONG or UNCLEAR and leave the rest alone. A version that replaces "
        "every word of theirs with a synonym is indistinguishable, to anyone checking "
        "it, from one that threw their sentence away, and it will be rejected as such.\n\n"
        "KEEP THEIR SHAPE — this one is checked, and a flattened answer is rejected. "
        "The text becomes part of the prompt a writer works from, so it is a piece of "
        "WRITING, not a label. Give it back laid out the way they laid it out, one "
        "array element per line:\n"
        "  - a paragraph stays a paragraph;\n"
        "  - A LIST STAYS A LIST — one point per line, each line starting with '- '. "
        "Running four points together into one paragraph of prose is the single most "
        "common way this is got wrong, and it will be sent back;\n"
        "  - a paragraph followed by its points keeps both, in that order, with a blank "
        "line between them.\n"
        "So a note that arrives as\n"
        "    do X.\n\n    - a\n    - b\n\n    also do Y.\n"
        "comes back as {\"lines\": [\"Do X.\", \"\", \"- A\", \"- B\", \"\", \"Also do Y.\"]} — same "
        "blocks, same list, better English.\n\n"
        "ADD THE STRUCTURE THE CONTENT DESERVES. Keeping their shape does NOT mean "
        "refusing to give it one. An author writing quickly runs things together, and "
        "these shapes always want breaking out:\n"
        "  - A RUN OF NAMED RULES — 'Explain in Simple Language First' and a sentence, "
        "then 'Connect Theory to Implementation' and a sentence, and so on — is a LIST. "
        "Every one of them becomes an `instructions` entry. Left as loose blocks it is a "
        "wall of prose with headings in it, and the tenth rule is as easy to miss as if "
        "it had not been written.\n"
        "  - SEVERAL PARALLEL REQUIREMENTS in one sentence or paragraph become a list, "
        "one per line, each starting with '- '. A brief of four rules has to read as "
        "four rules.\n"
        "  - A SEQUENCE OF THREE OR MORE STEPS — 'first X, then Y, then Z' — becomes a "
        "NUMBERED list, one step per line ('1. ', '2. ', …), because the order IS the "
        "instruction and a comma does not carry it. This one is missed constantly, so "
        "here it is worked through: the note 'explain the concept first then the syntax "
        "then show the code and then explain what the result means' is FOUR STEPS, and "
        "comes back as {\"lines\": [\"1. Explain the concept.\", \"2. Explain the "
        "syntax.\", \"3. Show the code.\", \"4. Explain what the result means.\"]} — never "
        "as one sentence with commas in it. Two steps is not a sequence; leave those as "
        "a sentence.\n"
        "If they already made a list, keep it as a list. Do NOT bullet something that is "
        "one thing: a single instruction stays a single sentence.\n"
        "EVERY LINE OF A LIST YOU CREATE MUST BE SOMETHING THEY ACTUALLY SAID. You are "
        "breaking their sentence apart, not filling a list out to a respectable length.\n\n"
        "MAKE IT CLEARER, NOT JUST TIDIER. Where a sentence is ambiguous about what the "
        "writer must actually DO, say it precisely. Where a term they used has two "
        "readings, pin it to the one they plainly meant. Where an instruction is missing "
        "the condition it obviously depends on — 'when', 'before', 'unless' — supply the "
        "one their own words imply. A human approves this before it takes effect, so a "
        "sharper version they can check beats a vaguer one that needed no thought.\n\n"
        "DO NOT INVENT — the one line you may not cross, and it is checked. Sharpening "
        "what they asked for is the job. Adding a REQUIREMENT they did not ask for is "
        "not: a new constraint, a new number, a new thing the document must contain, a "
        "preference of your own. If they said to explain the code, do not also decide "
        "how long the explanation runs or what it must mention. Every demand in your "
        "version must be traceable to a demand in theirs — better worded, better "
        "structured, sharper, but THEIRS.\n"
        "   A VAGUE NOTE STAYS ONE INSTRUCTION. When their whole note is short and "
        "unspecific — 'make the analogies good' — your job is ONE sharper sentence "
        "saying what they meant, not a list of four rules about analogies that they "
        "never wrote. A four-word note does not become a paragraph. If you cannot "
        "sharpen it without inventing, return their words tidied and nothing more.\n\n"
        "A RULE ABOUT HOW TO TEACH, NEVER ABOUT WHAT TO TEACH. The curriculum decides "
        "the topics; this decides how any topic is handled.\n\n"
        "`category` is what the rule governs:\n" + _CATEGORY_BRIEF + "\n"
        "`kind` is the older, coarser label, kept for compatibility: content (what the "
        "document must contain), structure (how it is shaped), style (how it is "
        "written).\n\n"
        "NEVER RESTATE WHERE IT APPLIES. Which course, which session, and whether it is "
        "a house rule are recorded separately and shown beside your sentence; an author "
        "who typed \"session 11 only\" has told you the SCOPE, not the instruction. Write "
        "what must happen, not where it happens. If their note says nothing but a scope, "
        "return their words essentially unchanged rather than inventing an instruction "
        "for them.\n\n"
        f"THE AUTHOR'S SKILL:\n{text}")

    # TWO TRIES, and the second one is told what it did wrong. A model that has dropped
    # an example almost always keeps it when the example is named back at it; asking
    # blindly again just re-rolls the same summary.
    nudge = ""
    out = ""
    for _attempt in range(2):
        try:
            data = model(prompt + nudge)
            parsed = json.loads(data) if isinstance(data, str) else data
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        ins = _instructions_from(parsed, _db)
        # `lines` is the layout of a single instruction; `text` is accepted as a
        # fallback for a model answering in the older shape. When there are several
        # instructions the skill's own text is its NAME — what the whole set is about —
        # and the rules live in the list beside it, exactly as the "from my notes" path
        # has always produced them.
        raw_lines = parsed.get("lines")
        body = _db.skill_body("\n".join(str(x) for x in raw_lines)
                              if isinstance(raw_lines, list) else parsed.get("text"))
        name = _db.skill_body(parsed.get("name"))
        out = (name or body) if len(ins) > 1 else (body or name)
        if len(ins) == 1 and not body:
            out, ins = ins[0], []          # one rule is the skill, not a list of one
        if not out:
            return None
        why = lossy(text, _assembled(out, ins))
        if not why:
            kind = str(parsed.get("kind") or "style").lower()
            return {"text": out, "instructions": ins,
                    "kind": kind if kind in KINDS else "style",
                    "category": normalize_category(parsed.get("category")),
                    "source_quote": text, "source_quotes": [text]}
        nudge = ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED. You wrote:\n"
                 f"{out}\n\n"
                 f"That is not acceptable because {why} Write it again, carrying "
                 "EVERYTHING the author wrote — every requirement, every example, every "
                 "number and name — and laid out as they laid it out, one array element "
                 "per line in `lines`. Fix their English and sharpen what is vague; do "
                 "not shorten them, do not flatten them, and do not add rules they never "
                 "wrote.")
    return None


def store_drafts(course: str, drafts: list[dict], *, created_by: str | None = None,
                 scope: str = "course", session_ref=None) -> int:
    """Store path-B drafts. They need approving like any other skill."""
    from . import db
    n = 0
    for d in drafts or []:
        if db.add_skill(course, d.get("text", ""), kind=d.get("kind") or "style",
                        source="requirements", created_by=created_by,
                        check=d.get("check"), source_quote=d.get("source_quote"),
                        source_quotes=d.get("source_quotes"),
                        category=d.get("category"),
                        instructions=d.get("instructions"),
                        scope=scope, session_ref=session_ref):
            n += 1
    return n
