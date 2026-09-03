"""COURSE MEMORY — the two things this course remembers that nothing else records.

There is already a lot of per-course memory here, and it is deliberately NOT in this
module: extracted decks and the already-taught index (`pptx_ingest`), assumed knowledge
from earlier courses (`prereqs`), the rules distilled from reviewer corrections
(`learning`), the instructions a course is written under (`skills`), and what kind of
course it is (`profiles`). Anything derivable from those belongs to them.

This module holds the two gaps those six leave, and nothing else:

  1. WHAT AN APPROVED TR TAUGHT, BEFORE ITS DECK EXISTS.
     `pptx_ingest.taught_index` is fed by extracted decks alone, and a deck arrives
     weeks after the document does — it has to be recorded first. In that window the
     course has no memory of the session: it is absent from the digest the writer and
     the judge read AND from `taught_titles`, which is what the repetition guardrail
     compares a new slide title against. A batch of TRs written ahead of recording can
     therefore re-teach itself with every gate green. Entries here fill exactly that
     window and are ignored the moment a real deck exists.

  2. THE EXAMPLES THE COURSE HAS ALREADY SPENT.
     `guardrails.check` catches one example reused across slides of one document.
     Nothing has ever looked across documents, so the same worked example can be built
     in session 4 and built again in session 11.

WHAT IS DELIBERATELY NOT STORED. No free-form notes, no "teaching decisions" (those are
skills), no reviewer feedback (that is `learning`, which distils, dedupes, counts hits,
keeps the raw wording and retires a rule once a gate supersedes it), no "learner
difficulties" (nothing in this system observes a learner, so the field could only ever
be filled by inference), and no catch-all continuity field. Every value here is
EXTRACTED from a document a human approved, which is what makes it auditable — and it
is why there is no cap and no eviction policy: the stores are bounded by the number of
sessions in the course, and part 1 empties itself as the course gets recorded.

Course Memory is internal context. It reaches the writer as prior-session material and
must never appear in a document; `skills.leaks` already fails the run when internal text
lands on a slide, in the agenda or in the key takeaways.
"""
from __future__ import annotations

from . import db
from . import pptx_ingest

# The SAME title cleaning and deck-furniture filter the deck index uses. Imported
# rather than re-implemented on purpose: an entry written here is read back through
# `taught_index` alongside real deck entries, and two different notions of "is this a
# topic or is it furniture" would make a provisional session look different from the
# recorded one — which is the single thing this must not do.
_clean_title = pptx_ingest._clean_title
_BOILERPLATE = pptx_ingest._BOILERPLATE

_MAX_SUMMARY = 180          # an example line is a reminder, not a re-teaching
_MAX_FIGURES = 6


def _slides(doc: dict) -> list[dict]:
    return [s for sec in (doc.get("sections") or []) for s in (sec.get("slides") or [])]


# --------------------------------------------------------------------------- #
# part 1 — topics an approved document taught
# --------------------------------------------------------------------------- #
def topics_of(doc: dict) -> list[str]:
    """The distinct topics a rendered TR doc teaches, shaped like a deck's topic list.

    Mirrors `taught_index`: slide titles, cleaned, furniture dropped, de-duplicated
    case-insensitively, and the document's own title excluded — a section divider
    repeating the session name is not a topic, exactly as a deck's title is not.
    """
    own = _clean_title(doc.get("session_title")).lower()
    seen, topics = set(), []
    for s in _slides(doc):
        t = _clean_title(s.get("title"))
        if not t or _BOILERPLATE.match(t):
            continue
        key = t.lower()
        if key == own or key in seen:
            continue
        seen.add(key)
        topics.append(t)
    return topics


# --------------------------------------------------------------------------- #
# part 2 — examples an approved document spent
# --------------------------------------------------------------------------- #
def examples_of(doc: dict) -> list[dict]:
    """The worked examples in a rendered TR doc: what each taught and its figures.

    Only `working_example` slides. That is the role the harness defines for a slide the
    learner must be able to EXECUTE, it is the role the realism gate already measures,
    and it is the only place a reused example actually costs the reviewer something —
    a definition restated is a repetition problem, which other gates own.
    """
    from guardrails import guardrails
    out = []
    for s in _slides(doc):
        if (s.get("role") or "") != "working_example":
            continue
        concept = _clean_title(s.get("heading")) or _clean_title(s.get("title"))
        blob = guardrails._slide_text_blob(s)
        # The figures ARE the example's identity: "translate a logical address" is a
        # topic, "translate 0x2F1A with a 4 KB page" is an example, and only the second
        # can be reused by accident.
        figs, seen = [], set()
        for f in guardrails._NUMERIC.findall(blob):
            if f not in seen:
                seen.add(f)
                figs.append(f)
            if len(figs) >= _MAX_FIGURES:
                break
        summary = " ".join(str(x) for x in guardrails._text_blocks(s)).strip()
        if not summary:
            summary = _clean_title(s.get("subheading"))
        if len(summary) > _MAX_SUMMARY:
            summary = summary[:_MAX_SUMMARY].rsplit(" ", 1)[0] + "…"
        if concept or summary:
            out.append({"concept": concept, "summary": summary, "figures": figs})
    return out


# --------------------------------------------------------------------------- #
# write — one call, at the moment a document is approved
# --------------------------------------------------------------------------- #
def record(course: str, session_no: int, doc: dict, *,
           run_id: str | None = None) -> dict:
    """Remember what an approved TR taught and which examples it spent.

    Called from finalize. Best effort by contract: the document is already written,
    reviewed and rendered by this point, so nothing here may raise into that path.
    Returns a small summary for the run log.
    """
    if not (course or "").strip() or session_no is None or not isinstance(doc, dict):
        return {"topics": 0, "examples": 0}
    try:
        topics = topics_of(doc)
    except Exception:
        topics = []
    try:
        examples = examples_of(doc)
    except Exception:
        examples = []
    try:
        db.put_provisional_taught(course, session_no, topics, run_id=run_id,
                                  session_name=str(doc.get("session_title") or ""))
    except Exception:
        pass
    try:
        written = db.put_examples_used(course, session_no, examples, run_id=run_id)
    except Exception:
        written = 0
    return {"topics": len(topics), "examples": written}


# --------------------------------------------------------------------------- #
# read — the examples block, for the generation prompt
# --------------------------------------------------------------------------- #
def examples_block(course: str | None, before_session: int | None) -> str:
    """The examples this course has already spent, for the writer. '' when there are none.

    Phrased as a budget rather than a ban. Re-deriving a worked example the course has
    already built is waste; teaching the same CONCEPT again more deeply is required
    whenever a takeaway names it, and the two must not be confused — which is why the
    figures are shown. They are what makes an example the same example.
    """
    if not (course or "").strip():
        return ""
    try:
        rows = db.examples_used(course, before_session)
    except Exception:
        return ""
    if not rows:
        return ""
    lines = []
    for r in rows:
        figs = ", ".join(r["figures"][:_MAX_FIGURES])
        tail = f"  [figures: {figs}]" if figs else ""
        label = r["concept"] or (r["summary"][:60] + "…")
        lines.append(f"  Session {r['session_no']} — {label}{tail}")
    return (
        "\nWORKED EXAMPLES THIS COURSE HAS ALREADY SPENT. Each was built in full in an "
        "earlier session, with the figures shown. Do NOT build the same example again: "
        "pick a different scenario, different figures, or a case the earlier one did not "
        "cover. Teaching the same CONCEPT again at greater depth is a different thing "
        "and is required wherever a takeaway names it — what must not repeat is the "
        "worked example itself.\n" + "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# housekeeping
# --------------------------------------------------------------------------- #
def prune(course: str, *, recorded_sessions=None, curriculum_sessions=None) -> dict:
    """Forget what the course no longer needs remembered.

    Two reasons an entry goes:
      · SUPERSEDED — the session's deck has been ingested, so the real thing is now in
        the taught index and the placeholder would be a second, weaker opinion of the
        same session.
      · ORPHANED — the session is no longer in the curriculum at all. Same rule
        `sync.prune_orphan_decks` applies to decks: the curriculum is the source of
        truth, so course memory follows it.
    """
    course = (course or "").strip()
    if not course:
        return {"superseded": 0, "orphaned": 0}
    try:
        held = {e["session_no"] for e in db.provisional_taught(course)}
    except Exception:
        return {"superseded": 0, "orphaned": 0}
    superseded = held & {int(n) for n in (recorded_sessions or [])}
    orphaned = set()
    if curriculum_sessions is not None:
        known = {int(n) for n in curriculum_sessions}
        # Guarded exactly as prune_orphan_decks is: with no curriculum rows this does
        # nothing, so a process that has not loaded a course cannot wipe the store.
        if known:
            orphaned = (held - known) | set()
            try:
                db.drop_examples_used(
                    course, {e["session_no"] for e in db.examples_used(course)} - known)
            except Exception:
                pass
    drop = superseded | orphaned
    if drop:
        db.drop_provisional_taught(course, drop)
    return {"superseded": len(superseded), "orphaned": len(orphaned)}
