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

WHERE A SKILL APPLIES. Three scopes, and the precedence between them is fixed:

    HARD RULES  >  COURSE REVIEWER SKILLS  >  SESSION SKILLS  >  COURSE SKILLS  >  GLOBAL

The numbered hard rules about document structure can never be overridden. Below them, a
correction a reviewer made about this course outranks a rule written for one of its
sessions, which outranks the course's standing brief, which outranks the house rules
every course gets. Narrower and more recently-learned wins.

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
    """A skill's own lines. Its `text` when it has none — see db.add_skill."""
    lines = [" ".join(str(i).split()) for i in (skill.get("instructions") or [])
             if str(i or "").strip()]
    if lines:
        return lines
    one = " ".join(str(skill.get("text") or "").split())
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
    ("global", "GLOBAL SKILLS",
     "house rules every course gets; anything above overrides them"),
)


def _tier_of(skill: dict) -> str:
    """Which precedence tier a stored skill sits in."""
    try:
        from . import db
        global_course = db.GLOBAL_COURSE
    except Exception:
        global_course = "*"
    if (skill.get("course") or "").strip() == global_course:
        return "global"
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


# --------------------------------------------------------------------------- #
# the brief
# --------------------------------------------------------------------------- #
_CATEGORY_ORDER = tuple(CATEGORIES) + tuple(LEGACY_KINDS)


def _heading_for(cat: str) -> tuple[str, str]:
    if cat in CATEGORIES:
        return CATEGORIES[cat]
    return (LEGACY_KINDS.get(cat, cat.upper()), "")


def _render(skill: dict, out: list[str]) -> None:
    """One skill: its own sentence, then the lines the author grouped under it.

    Numbered, because for a teaching flow the ORDER is the instruction and a bullet list
    says nothing about order. A single-instruction skill is just the sentence — a list of
    one reads as a checklist item rather than a rule.
    """
    lines = instructions_of(skill)
    text = " ".join(str(skill.get("text") or "").split())
    if len(lines) <= 1:
        out.append(f"- {lines[0] if lines else text}")
        return
    out.append(f"- {text}")
    for i, line in enumerate(lines, start=1):
        out.append(f"    {i}. {line}")


def block(course: str, session=None) -> str:
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
    out = [f"# HOW '{course}' IS WRITTEN — the course brief",
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
           "SKILLS → COURSE SKILLS → GLOBAL SKILLS. Where any of it conflicts with the "
           "default style guidance, THE BRIEF WINS; only the numbered HARD RULES about "
           "document STRUCTURE outrank it. Where two parts of the brief conflict, the "
           "one from the higher tier wins outright."]

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
                _render(s, out)
        for cat, batch in by_cat.items():           # a category added later, still shown
            out += ["", f"### {cat.upper()}"]
            for s in batch:
                _render(s, out)
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
_MIN_KEEP_RATIO = 0.6
_MIN_WORDS_TO_JUDGE_LENGTH = 22


def _specifics(text: str) -> set[str]:
    return {m.group(0).strip("`\"“”'").lower()
            for m in _SPECIFIC.finditer(str(text or ""))}


def lossy(src: str, out: str) -> str:
    """Why `out` fails to carry everything `src` said, or "" when it carries it all."""
    src, out = str(src or ""), str(out or "")
    low = out.lower()
    missing = sorted(x for x in _specifics(src) if x and x not in low)
    if missing:
        return ("it drops what the author actually specified: "
                + ", ".join(f"\u201c{m}\u201d" for m in missing[:6]))
    n_src, n_out = len(src.split()), len(out.split())
    if n_src >= _MIN_WORDS_TO_JUDGE_LENGTH and n_out < _MIN_KEEP_RATIO * n_src:
        return (f"it is a summary, not a rewrite: {n_src} words became {n_out}. "
                f"Every separate thing the author asked for has to survive.")
    return ""


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
                "meant, you add NOTHING they did not ask for and you DROP NOTHING they "
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
        "sentence>\", \"instructions\": [\"...\", \"...\"], \"kind\": \"style|content|"
        "structure\", \"source_quotes\": [\"<exact words from the input>\", ...], "
        "\"check\": {...}|null}]}\n\n"
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
        "   THERE IS NO LENGTH LIMIT, and being shorter than the author is not a virtue. "
        "Every separate thing they asked for becomes an instruction; every EXAMPLE they "
        "gave is carried into the instruction it belongs to, because an author who gives "
        "an example has stated a requirement. Every number, name, quoted phrase and "
        "piece of code they wrote appears in your version — 'under 12 lines' may not "
        "become 'short'. Nothing they said is dropped for being long-winded: fix the "
        "wording, keep the substance.\n\n"
        "THESE ARE INSTRUCTIONS ABOUT HOW TO TEACH, NEVER ABOUT WHAT TO TEACH. If the "
        "author names a topic their course covers, that is CURRICULUM and does not "
        "belong here — skip it. A skill shapes how any topic is taught.\n\n"
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
        lines = [" ".join(str(i).split()) for i in (p.get("instructions") or [])
                 if str(i or "").strip()]
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
    said = " ".join(d["text"] + " " + " ".join(d["instructions"]) for d in out).lower()
    dropped = sorted(x for x in _specifics(raw) if x and x not in said)
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
    text = " ".join((text or "").split())
    if not text:
        return None
    if model is None:
        model = _default_model
    prompt = (
        "A course author has written ONE SKILL their course must be written under — an "
        "instruction about HOW the course is taught. Turn it into the instruction a "
        "professional writer will work from.\n\n"
        "Return JSON: {\"text\": \"...\", \"category\": \"teaching_flow|"
        "teaching_guidelines|examples_visuals|reviewer\", \"kind\": \"style|content|"
        "structure\"}\n\n"
        "YOU ARE EDITING THEIR ENGLISH, NOT SUMMARISING THEM. They typed it in a hurry, "
        "with typos, as a note to themselves. Give it back as what a writer who has "
        "never spoken to them will follow: correct, unambiguous, imperative, no hedging, "
        "standing on its own without their note beside it. Fix the typos and the grammar. "
        "Say the same things, properly.\n\n"
        "LOSE NOTHING. This is the rule that matters most, and the one most easily "
        "broken. EVERY separate thing they asked for survives into your version. So does "
        "EVERY EXAMPLE they gave — an author who writes an example has written a "
        "requirement, and the example is the instruction, not decoration for it. So does "
        "every number, name, quoted phrase and piece of code: \u201ckeep snippets under 12 "
        "lines\u201d is a different rule from \u201ckeep snippets short\u201d, and you may not "
        "trade one for the other. There is NO LENGTH LIMIT. If their note carries five "
        "requirements and three examples, your version carries five requirements and "
        "three examples, and it will be as long as that takes. Coming back shorter than "
        "what they wrote is the failure this is warning you about; longer is fine.\n\n"
        "KEEP THEIR WORDS WHERE THEY ARE ALREADY RIGHT. You are not being asked to "
        "re-word for the sake of it. Where a phrase of theirs already says the thing "
        "clearly, keep that phrase. Change what is wrong or unclear, and leave the rest "
        "alone.\n\n"
        "DO NOT INVENT. Making their intent explicit is the job; adding requirements "
        "they did not express is not. If they said to explain the code, do not also "
        "decide how long the explanation runs, where it sits, or what it must mention. "
        "Every demand in your sentence must be one they made. When their note is already "
        "a clear instruction, return it essentially unchanged rather than embroidering "
        "it — a faithful copy beats a richer rule they did not ask for.\n\n"
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
    for _attempt in range(2):
        try:
            data = model(prompt + nudge)
            parsed = json.loads(data) if isinstance(data, str) else data
        except Exception:
            return None
        if not isinstance(parsed, dict):
            return None
        out = " ".join(str(parsed.get("text") or "").split())
        if not out:
            return None
        why = lossy(text, out)
        if not why:
            kind = str(parsed.get("kind") or "style").lower()
            return {"text": out, "kind": kind if kind in KINDS else "style",
                    "category": normalize_category(parsed.get("category")),
                    "source_quote": text, "source_quotes": [text]}
        nudge = ("\n\nYOUR PREVIOUS ANSWER WAS REJECTED. You wrote:\n"
                 f"{out}\n\n"
                 f"That is not acceptable because {why} Write it again, carrying "
                 "EVERYTHING the author wrote — every requirement, every example, every "
                 "number and name. Fix their English; do not shorten them. Your answer "
                 "should be at least as long as their note.")
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
