"""Self-evolution: a persistent, per-course store of LEARNED RULES.

Feedback the human gives (a regeneration reason) and hard defects the judge flags
(blocking issues) are distilled into short, durable rules and saved to
`knowledge_base/learned_rules.json`, so the same mistake is not repeated across
sessions — the agent visibly improves as it is used.

The loop has four parts, and all four are needed for it to actually close:
  1. DISTIL  — a hurried, deictic note ("remove analogy from this") becomes a
     standalone instruction. Raw notes cannot transfer to another session.
  2. DEDUPE  — the same request phrased three ways becomes ONE rule with a hit
     count, instead of three that dilute the block and evict older rules.
  3. INJECT  — the rules go into the SYSTEM prompt (generator._learned), rebuilt on
     every call, and declare precedence over the style guide. As a soft list at the
     tail of the user prompt they lost every conflict with the harness's HARD RULES,
     so corrections were silently discarded.
  4. VERIFY  — the judge is given the rules and asked to put any violation in
     blocking_issues, which fails the gate, triggers a revision, and re-learns the
     rule (bumping its hit count). Without this nothing ever checked compliance.

Deliberately simple and TRANSPARENT: rules are plain text you can read, edit, or
delete by hand. No fine-tuning, no hidden state. `python3 -m src.learning` re-distils
an existing store in place (keeping a .bak and each rule's original wording).
"""
from __future__ import annotations
import json
import re
from pathlib import Path

from . import config

STORE = config.KB_DIR / "learned_rules.json"
_MAX_RULES = 40            # keep the injected block small; oldest trimmed first
_MAX_RULE_LEN = 200


def _load() -> dict:
    if STORE.exists():
        try:
            return json.loads(STORE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"rules": []}


def _save(data: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # Mirror to the DB straight away. On a free/ephemeral host this file lives on a
    # disk that is wiped when the instance spins down, and the only other backup runs
    # at the end of a sync — so without this a rule learned from feedback could be
    # gone before the next document was generated, which would make the whole loop
    # look like it had "not learned anything". Best effort: never break a generation.
    try:
        from . import db
        db.kb_put(STORE.name)
    except Exception:
        pass


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


# Words that carry no discriminating meaning when comparing two rules.
_STOP = {
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "in", "on", "for", "and",
    "or", "not", "no", "do", "dont", "don't", "must", "should", "always", "never",
    "this", "that", "these", "those", "it", "its", "you", "your", "we", "i", "as",
    "with", "from", "at", "by", "each", "every", "any", "all", "also", "while",
    "when", "where", "which", "here", "there", "add", "added", "make", "keep",
}


def _keywords(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", _norm(text)) if len(w) > 2 and w not in _STOP}


def _similar(a: str, b: str, threshold: float = 0.6) -> bool:
    """Near-restatement check on content words (Jaccard).

    This is only the CHEAP guard, for rules that are almost the same sentence. It
    cannot see that "Relate the analogy to the topic" and "Correlate every analogy
    with the concept being taught" are the same instruction — they share one content
    word, so any threshold loose enough to merge them would also merge unrelated
    rules. Genuine paraphrase dedup is done by the model in distill_feedback(), which
    gets the existing rules and is asked to fold the note into one of them; this
    function is the fallback for when that call is unavailable.
    """
    ka, kb = _keywords(a), _keywords(b)
    if not ka or not kb:
        return _norm(a) == _norm(b)
    return len(ka & kb) / len(ka | kb) >= threshold


def _merge_plausible(note: str, rule: str) -> bool:
    """Veto on a claimed duplicate that shares NO subject matter with the rule.

    The distil model is willing to fold a note into a rule that merely sounds like
    generic writing advice — it once merged "recap should be extracted as-is like the
    agenda" into a rule about analogies. A wrong merge is worse than a duplicate: the
    reviewer's instruction is silently dropped, which is the very failure this module
    exists to prevent. Two instructions about the same thing essentially always share
    at least one content word, so require that much.
    """
    return bool(_keywords(note) & _keywords(rule))


def reinforce(index: int, session_no=None) -> None:
    """Bump an existing rule's hit count — the reviewer has asked for it again."""
    data = _load()
    if 0 <= index < len(data["rules"]):
        r = data["rules"][index]
        r["hits"] = r.get("hits", 1) + 1
        r["last_session_no"] = session_no
        _save(data)


def rules() -> list[dict]:
    """EVERY stored rule, regardless of scope (for the UI / admin listing)."""
    return _load().get("rules", [])


# --------------------------------------------------------------------------- #
# SCOPE — why rules are two-tier rather than simply per-course.
#
# A reviewer's corrections split into two kinds, and they behave differently:
#   • HOUSE STYLE ("scope": "global") — how a TR doc should be written at all:
#     copy the agenda verbatim, tie analogies back to the concept, drop the analogy
#     on an example slide. These are true for every course.
#   • SUBJECT MATTER ("scope": "course") — about one curriculum's content:
#     "expand Rollback and Starvation". Meaningless in another course, and actively
#     misleading if injected there.
#
# Scoping EVERYTHING per course would have thrown away 6 of the 7 rules learned so
# far the moment a new course started, so the reviewer would have to re-teach the
# same house style from scratch — the exact "it keeps repeating the same mistake"
# problem this module exists to solve. Scoping NOTHING leaks one course's topics into
# another. So: global rules always apply; course rules apply only to their course.
# --------------------------------------------------------------------------- #
GLOBAL, COURSE = "global", "course"


def _active_course() -> str:
    try:
        from . import app_settings
        return app_settings.course_name()
    except Exception:
        return ""


def _scope_of(r: dict) -> str:
    """Rules written before scoping existed are treated as house style (they were
    injected everywhere already, so this changes nothing for them until migrated)."""
    return COURSE if r.get("scope") == COURSE else GLOBAL


def applicable_rules(course: str | None = None) -> list[dict]:
    """The rules that apply to `course`: every global rule + that course's own.

    Honours self_evolution.scope_rules — set it false in the harness to go back to
    injecting every rule everywhere.
    """
    rs = rules()
    if not _self_evo_cfg().get("scope_rules", True):
        return rs
    course = _active_course() if course is None else course
    return [r for r in rs
            if _scope_of(r) == GLOBAL or (r.get("course") or "") == course]


def _cap() -> int:
    try:
        return int(_self_evo_cfg().get("max_rules", _MAX_RULES) or _MAX_RULES)
    except Exception:
        return _MAX_RULES


def _trim(rs: list[dict]) -> list[dict]:
    """Enforce the cap, dropping the LEAST-REINFORCED rules first.

    Trimming purely by age (the old behaviour) meant a rule the human had insisted
    on repeatedly could be pushed out by a one-off grader nitpick. Rules that keep
    coming back have a higher `hits` count and survive.
    """
    cap = _cap()
    if len(rs) <= cap:
        return rs
    indexed = list(enumerate(rs))
    indexed.sort(key=lambda p: (p[1].get("hits", 1), p[0]), reverse=True)
    keep = {i for i, _ in indexed[:cap]}
    return [r for i, r in enumerate(rs) if i in keep]


def add_rule(text: str, *, source: str, session_no=None, raw: str | None = None,
             scope: str = GLOBAL, course: str | None = None) -> bool:
    """Add a durable rule. Returns True if NEWLY added.

    A rule that merely restates one already stored is not appended again — it
    reinforces the existing rule (`hits`), which both keeps the injected block
    short and marks it as something the human keeps having to ask for.
    """
    text = (text or "").strip()
    if not text:
        return False
    if len(text) > _MAX_RULE_LEN:
        text = text[:_MAX_RULE_LEN].rstrip() + "…"
    course = _active_course() if course is None else course
    data = _load()
    for r in data["rules"]:
        # Only collapse against a rule that would actually apply alongside this one —
        # two courses may legitimately need similar-sounding subject-matter rules.
        if _scope_of(r) == COURSE and (r.get("course") or "") != course:
            continue
        if _similar(r.get("text", ""), text):
            r["hits"] = r.get("hits", 1) + 1
            r["last_session_no"] = session_no
            _save(data)
            return False
    entry = {"text": text, "source": source, "session_no": session_no, "hits": 1,
             "scope": COURSE if scope == COURSE else GLOBAL, "course": course}
    if raw and _norm(raw) != _norm(text):
        entry["raw"] = raw[:_MAX_RULE_LEN]      # what the human actually typed
    data["rules"].append(entry)
    data["rules"] = _trim(data["rules"])
    _save(data)
    return True


def record_feedback(session_no, reason: str, *, source: str = "feedback") -> bool:
    """A human reason for rejecting/regenerating content -> a durable preference.

    The raw reason is NOT usable as a cross-session rule: it is typed in a hurry
    ("Simce no analogy is needed for an rexample remove the field of analogy from
    this") and it is DEICTIC — "this", "here", "that slide" refer to a chunk that
    does not exist in the next session. Injected verbatim it was noise the model
    could not act on, which is why the same feedback had to be given again and
    again. So distil it into one general imperative rule first, and keep the raw
    text alongside it for auditing.
    """
    if not (reason or "").strip():
        return False
    cfg = _self_evo_cfg()
    if not cfg.get("enabled", True):
        return False
    if not cfg.get("distill", True):
        return add_rule(reason, source=source, session_no=session_no)
    # Compare only against rules that CO-APPLY with this one (global + this course),
    # but keep the mapping back to positions in the full store so `reinforce` targets
    # the right rule.
    course = _active_course()
    visible = [(i, r.get("text", "")) for i, r in enumerate(rules())
               if _scope_of(r) == GLOBAL or (r.get("course") or "") == course]
    text, dup_index, scope = distill_feedback(reason, [t for _, t in visible])
    if dup_index is not None:
        # Same instruction as one already stored, just phrased differently. Reinforce
        # it rather than adding a third wording of the same thing.
        reinforce(visible[dup_index][0], session_no)
        return False
    return add_rule(text, source=source, session_no=session_no, raw=reason,
                    scope=scope, course=course)


def record_issues(session_no, issues: list[str], *, source: str = "judge") -> int:
    """Persist hard defects (judge blocking issues) as rules VERBATIM (no distil)."""
    n = 0
    for i in issues or []:
        if add_rule(str(i), source=source, session_no=session_no):
            n += 1
    return n


def _self_evo_cfg() -> dict:
    try:
        return config.harness().get("self_evolution", {}) or {}
    except Exception:
        return {}


def distill_rule(issue: str) -> str:
    """Rewrite one concrete grader/judge failure into a short, GENERAL, reusable
    DO/DON'T rule for future generations. Best-effort: on any LLM error it returns
    the raw issue text so learning still happens (just less polished)."""
    from . import llm
    m = config.harness()["model"]
    try:
        out = llm.complete(
            system=(
                "You convert a single QA failure from a teaching-document generator into "
                "ONE short, GENERAL, imperative rule (a DO or DON'T) that would prevent the "
                "same CLASS of mistake next time. Output one line only, no preamble, <=160 "
                "chars, no session-specific nouns/numbers, no quotes."),
            user=f"QA failure:\n{issue}\n\nReusable rule:",
            model=m.get("judge", m["generator"]), max_tokens=120, temperature=0.0,
            label="distill")
        line = (out or "").strip().splitlines()[0].strip().lstrip("-•*").strip().strip('"')
        return line or issue
    except Exception:
        return issue


def distill_feedback(reason: str, existing: list[str] | None = None
                     ) -> tuple[str, int | None, str]:
    """Turn one human regeneration reason into a general, reusable instruction.

    Returns (rule_text, duplicate_index, scope). If the note restates something
    already in `existing`, duplicate_index is that rule's position and rule_text is
    that rule. `scope` is "global" for house style or "course" when the rule is about
    this curriculum's subject matter (see the SCOPE note above).

    Different job from distill_rule(): the input is not a QA failure report but a
    hurried human note, so the prompt has to cope with typos and — critically —
    strip the deictics ("this", "here", "that slide") that make the note meaningless
    outside the chunk it was written about. It also does the dedup, because the
    reviewer types the same instruction differently each time and no lexical measure
    catches that (see _similar). Best-effort: on any LLM error we fall back to the
    raw reason plus the cheap lexical check, so feedback is still recorded.
    """
    from . import llm
    m = config.harness()["model"]
    existing = existing or []
    listing = "\n".join(f"{i}. {t}" for i, t in enumerate(existing)) or "(none yet)"
    try:
        out = llm.complete(
            system=(
                "You maintain a list of standing instructions for a writer of teaching "
                "documents, from a reviewer's rough notes.\n"
                "Given EXISTING RULES and a new NOTE, do ONE of:\n"
                "(a) If the note asks for the SAME THING an existing rule already covers — "
                "even if worded completely differently — output exactly: SAME: <number>\n"
                "(b) Otherwise output ONE new standing instruction: short, imperative, "
                "general, <=160 chars.\n"
                "Be CONSERVATIVE about (a): it must be the same request about the same part "
                "of the document, not merely similar-sounding writing advice. If the note "
                "is about a different element (agenda vs analogy vs recap vs slide length), "
                "it is NOT the same — use (b). When in doubt, use (b).\n"
                "The note may contain typos and shorthand — infer the intent. For (b) you "
                "MUST remove every reference to a specific slide/section/document ('this', "
                "'here', 'that slide') and restate the point so it stands alone with no "
                "context.\n"
                "For (b), also CLASSIFY the instruction on a second line:\n"
                "  SCOPE: global   — it is about HOW to write any teaching document "
                "(formatting, voice, length, analogies, structure, depth). Applies to every "
                "course. This is the common case.\n"
                "  SCOPE: course   — it is about the SUBJECT MATTER of this particular "
                "curriculum, naming a specific topic/algorithm/protocol (e.g. 'expand the "
                "Rollback and Starvation section'). Would be meaningless in another course.\n"
                "Output either 'SAME: <number>' on one line, or the instruction on line 1 "
                "and 'SCOPE: <global|course>' on line 2. No preamble, no quotes."),
            user=f"EXISTING RULES:\n{listing}\n\nNOTE:\n{reason}\n\nOutput:",
            model=m.get("judge", m["generator"]), max_tokens=160, temperature=0.0,
            label="distill_feedback")
        lines = [l.strip() for l in (out or "").strip().splitlines() if l.strip()]
        line = (lines[0] if lines else "").lstrip("-•*").strip().strip('"')
        scope = COURSE if re.search(r"SCOPE:\s*course", out or "", re.I) else GLOBAL
        mm = re.match(r"^SAME:\s*(\d+)\s*$", line, flags=re.I)
        if mm:
            idx = int(mm.group(1))
            if 0 <= idx < len(existing) and _merge_plausible(reason, existing[idx]):
                return existing[idx], idx, _scope_of({})
            # Named a rule that isn't there, or one with nothing in common with the
            # note. Don't drop the feedback — distil it on its own instead (one more
            # cheap call, and only on this rare path) so it is still a usable rule
            # rather than the raw, deictic note.
            if existing:
                return distill_feedback(reason, [])
            return reason, None, scope
        if line:
            for i, t in enumerate(existing):          # belt-and-braces lexical check
                if _similar(t, line):
                    return t, i, scope
            return line, None, scope
        return reason, None, scope
    except Exception:
        for i, t in enumerate(existing):
            if _similar(t, reason):
                return t, i, GLOBAL
        return reason, None, GLOBAL


def learn_from_issues(session_no, issues: list[str], *, source: str = "judge") -> int:
    """Self-evolution entry point: distil the defects that SURVIVED the revision loop
    into durable, cross-session rules. Honors harness `self_evolution` config
    (enabled / learn_from_judge / distill). Returns the number of NEW rules added."""
    cfg = _self_evo_cfg()
    if not cfg.get("enabled", True) or not cfg.get("learn_from_judge", True):
        return 0
    do_distill = cfg.get("distill", True)
    n = 0
    for raw in issues or []:
        text = distill_rule(str(raw)) if do_distill else str(raw)
        if add_rule(text, source=source, session_no=session_no):
            n += 1
    return n


def classify_scope(text: str) -> str:
    """Is this rule house style (applies to every course) or about one curriculum's
    subject matter? Used to migrate rules stored before scoping existed. Falls back
    to global, which is the pre-scoping behaviour and the common case."""
    from . import llm
    m = config.harness()["model"]
    try:
        out = llm.complete(
            system=("Classify one standing instruction for a writer of teaching documents. "
                    "Answer with ONE word.\n"
                    "'global'  — about HOW to write any teaching document (formatting, "
                    "voice, length, analogies, structure, depth, process). Applies to every "
                    "course.\n"
                    "'course'  — about the SUBJECT MATTER of one particular curriculum, "
                    "naming a specific topic/algorithm/protocol. Meaningless elsewhere.\n"
                    "Output only 'global' or 'course'."),
            user=f"Instruction:\n{text}\n\nAnswer:",
            model=m.get("judge", m["generator"]), max_tokens=8, temperature=0.0,
            label="classify_scope")
        return COURSE if "course" in (out or "").strip().lower() else GLOBAL
    except Exception:
        return GLOBAL


def scope_existing(course: str | None = None) -> dict:
    """Assign scope + course to rules stored before two-tier scoping existed.

    Run once after the upgrade: until a rule is classified it is treated as global,
    so a subject-matter rule from an earlier course would keep being injected into a
    different course's documents.
    """
    course = _active_course() if course is None else course
    data = _load()
    changed = 0
    counts = {GLOBAL: 0, COURSE: 0}
    for r in data.get("rules", []):
        if not r.get("scope"):
            r["scope"] = classify_scope(r.get("text", ""))
            r.setdefault("course", course)
            changed += 1
        counts[_scope_of(r)] += 1
    _save(data)
    return {"classified": changed, "global": counts[GLOBAL], "course": counts[COURSE]}


def distil_existing() -> dict:
    """One-off maintenance: re-distil rules that were stored VERBATIM by the old
    record_feedback, and collapse the duplicates that exact-text dedup let through.

    Worth running once after the upgrade: those rules are now injected at system
    level with precedence over the style guide, and raw notes full of typos and
    "remove analogy from this" are not something the model can act on. The original
    wording is preserved in each rule's `raw` field. Run with:

        python3 -m src.learning
    """
    data = _load()
    old = data.get("rules", [])
    kept: list[dict] = []
    merged = 0
    for r in old:
        text, src = r.get("text", ""), r.get("source")
        if src not in ("regeneration", "feedback") or r.get("raw"):
            kept.append(r)                       # already distilled, or automated
            continue
        new_text, dup, _scope = distill_feedback(text, [k.get("text", "") for k in kept])
        if dup is not None:
            kept[dup]["hits"] = kept[dup].get("hits", 1) + 1
            merged += 1
            continue
        entry = dict(r)
        entry["text"] = new_text
        entry["raw"] = text[:_MAX_RULE_LEN]
        entry.setdefault("hits", 1)
        kept.append(entry)
    data["rules"] = _trim(kept)
    _save(data)
    return {"before": len(old), "after": len(data["rules"]), "merged": merged}


def learned_rules_block() -> str:
    """The block injected into generation prompts. Empty string if no rules.

    Wording matters here. As a soft "LEARNED PREFERENCES" list at the tail of the
    user message, these lost every argument against the system prompt's "HARD RULES
    (a violation fails the run)" — so whenever a reviewer asked for something the
    default style guidance discouraged, the model quietly kept doing the thing it had
    just been corrected on. The block is now injected at SYSTEM level (see
    generator._system) and states its own precedence explicitly.

    Rules the reviewer has raised more than once are marked, so the model can see
    which ones it keeps getting wrong.
    """
    # Only the rules that apply to the ACTIVE course — a global (house-style) rule
    # always does; a subject-matter rule only within its own course.
    rs = applicable_rules()
    if not rs:
        return ""
    human = [r for r in rs if r.get("source") in ("regeneration", "feedback")]
    auto = [r for r in rs if r.get("source") not in ("regeneration", "feedback")]

    def fmt(r):
        again = f"  [RAISED {r['hits']}× — you keep getting this wrong]" if r.get("hits", 1) > 1 else ""
        return f"- {r['text']}{again}"

    out = ["# REVIEWER-ENFORCED RULES (highest priority)",
           "These come from corrections a human reviewer made to earlier documents in this "
           "same course. They are REQUIREMENTS, not suggestions.",
           "PRECEDENCE: if one of these conflicts with the style guidance or field guidance "
           "(length caps, phrasing preferences, what to include), THE REVIEWER RULE WINS — "
           "follow it and ignore the default. Only the numbered HARD RULES about document "
           "STRUCTURE (cover every key takeaway, agenda count, valid JSON schema) outrank "
           "them. Never silently drop one of these because a default said otherwise."]
    if human:
        out.append("\n## From the reviewer's own feedback")
        out += [fmt(r) for r in human]
    if auto:
        out.append("\n## From automated QA defects on earlier runs")
        out += [fmt(r) for r in auto]
    return "\n".join(out) + "\n"


if __name__ == "__main__":       # python3 -m src.learning  -> re-distil + scope the store
    import shutil
    if STORE.exists():
        shutil.copy2(STORE, STORE.with_suffix(".json.bak"))
        print(f"backup: {STORE.with_suffix('.json.bak')}")
    print("distil:", distil_existing())
    print("scope :", scope_existing())
    print(f"\nactive course: {_active_course()!r}")
    for r in rules():
        tag = "house" if _scope_of(r) == GLOBAL else f"course:{r.get('course')}"
        print(f"  [{tag}] hits={r.get('hits', 1)} ({r.get('source')}) {r['text']}")
    print(f"\ninjected for the active course: {len(applicable_rules())} of {len(rules())}")
