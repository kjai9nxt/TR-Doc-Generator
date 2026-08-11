"""Build the context the generator needs: prior-session material + the target.

Primary source of 'the past' = the course's PowerPoint decks, ingested into the
persistent knowledge base (see pptx_ingest). For each generation we:
  1. incrementally sync the KB (only new/changed .pptx get processed),
  2. inject a compact SUMMARY of EVERY prior deck (nothing skipped) so the agent
     never re-teaches and can recap correctly,
  3. add RAG-retrieved slides most relevant to the target topic for detail.
Prior TR docs (.docx), if any, are folded in as a secondary signal.
"""
from __future__ import annotations
import glob
import re
from pathlib import Path

import docx as docxlib

from . import config, pptx_ingest
from .course_loader import Session


def _docx_paragraph_texts(path: Path) -> list[str]:
    d = docxlib.Document(str(path))
    return [(p.text.strip(), p.style.name) for p in d.paragraphs if p.text.strip()]


def summarize_past_doc(path: Path) -> str:
    """Compact one prior TR doc into title + sections + slide headings."""
    title = None
    lines: list[str] = []
    for text, style in _docx_paragraph_texts(path):
        if style == "Heading 1" and title is None:
            title = text
        elif "SECTION" in text:
            lines.append(f"  {re.sub(r'-+', '', text).strip()}")
        elif style == "Heading 3" and text.lower().startswith("slide"):
            lines.append(f"    - {text}")
    head = title or path.stem
    return head + ("\n" + "\n".join(lines) if lines else "")


def _session_no_from_name(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else 10**6


def past_docs_summary(before_session: int) -> str:
    """Summaries of all TR docs for sessions < before_session, in order."""
    pattern = config.harness()["context"]["past_docs_glob"]
    paths = sorted(
        (Path(p) for p in glob.glob(str(config.ROOT / pattern))),
        key=_session_no_from_name,
    )
    chunks = []
    for p in paths:
        if _session_no_from_name(p) < before_session:
            chunks.append(summarize_past_doc(p))
    return "\n\n".join(chunks)


def past_ppts_context(cur: Session) -> str:
    """Build the prior-material block from the knowledge base: a summary of every
    earlier deck + RAG-retrieved relevant slides. The KB is populated by the sync
    engine (Google Slides); if it is empty we fall back to any local .pptx files
    (offline/dev mode)."""
    prior = pptx_ingest.decks_before(cur.number)
    if not prior and not pptx_ingest.load_all_decks():
        pptx_ingest.ingest(verbose=True)   # offline fallback: local inputs/past_ppts/
        prior = pptx_ingest.decks_before(cur.number)

    parts = []
    if prior:
        summaries = "\n\n".join(d["summary"] for d in prior)
        covered = ", ".join(f"S{d['session_no']}" for d in prior)
        parts.append(f"SUMMARY OF EVERY PRIOR DECK (already taught — do NOT re-teach; "
                     f"sessions covered: {covered}):\n{summaries}")

        query = cur.name + " " + " ".join(cur.key_takeaways)
        top_k = config.harness()["context"].get("rag_top_k", 6)
        hits = pptx_ingest.retrieve(query, cur.number, top_k=top_k)
        if hits:
            rag = "\n".join(
                f"  [S{h['session_no']} · Slide {h['slide']}] {h['title']}: {h['excerpt']}"
                for h in hits)
            parts.append("MOST RELEVANT PRIOR SLIDES TO THIS TOPIC (for continuity/detail):\n" + rag)
    else:
        parts.append("(No prior decks in the knowledge base yet — treat earlier "
                      "sessions' scope as given by the course structure above.)")

    docs = past_docs_summary(cur.number)
    if docs.strip():
        parts.append("PRIOR TR DOCS (secondary reference):\n" + docs)
    return "\n\n".join(parts)


def recency_and_course_type_block() -> str:
    """Inject the recency baseline + the course-type teaching strategy the user
    chose at connect time (src/app_settings). Both course types must ultimately
    help the learner clear interview questions; a semester course additionally
    goes deep on theory."""
    from . import app_settings
    ref = app_settings.reference_date()
    ct = app_settings.course_type()
    if ct == "interview":
        type_line = (
            "COURSE TYPE: INTERVIEW-TARGETED. Prioritise the concepts, patterns, and "
            "Q&A that clear interviews. Keep theory to what is needed to answer well; "
            "lead with what gets asked and how to answer it crisply.")
    else:
        type_line = (
            "COURSE TYPE: SEMESTER. Go DEEP theoretically — formal definitions, "
            "derivations/why-it-works, internals, and edge cases — as a semester course "
            "demands. Depth is expected here.")
    return (
        "=== RECENCY & COURSE TYPE ===\n"
        f"Treat all information as current AS OF {ref}. Do NOT present deprecated, "
        "superseded, or outdated tools/versions/practices as current; prefer the latest "
        f"stable standards known as of {ref}, and note when something recently changed.\n"
        f"{type_line}\n"
        "IN BOTH CASES: the doc must ultimately help the learner CLEAR INTERVIEW "
        "QUESTIONS on this topic — frame each major concept so it also answers the "
        "questions an interviewer would ask.")


def build_guided_base(prev: Session | None, cur: Session, nxt: Session | None) -> str:
    """The shared context block (course + target + prev/next + course memory),
    WITHOUT a final 'produce the doc' instruction. One-shot generation appends the
    whole-doc instruction (build_user_prompt); guided generation appends a
    per-chunk instruction (opening_instruction / takeaway_instruction)."""
    kt = "\n".join(f"- {k}" for k in cur.key_takeaways)
    prev_block = "This is the FIRST session of the course — OMIT the recap."
    if prev:
        prev_kt = "\n".join(f"  - {k}" for k in prev.key_takeaways)
        prev_block = (
            f"Previous session (for RECAP): Session {prev.number} — {prev.name}\n"
            f"Its key takeaways:\n{prev_kt}"
        )
    next_block = (
        f"Next session (for the sign-off): {nxt.name}" if nxt
        else "This is the FINAL session — set upcoming_session to null."
    )
    past = past_ppts_context(cur)

    return f"""COURSE: Computer Networks
MODULE: {cur.module}
TOPIC: {cur.topic}

{recency_and_course_type_block()}

=== TARGET SESSION ===
Session {cur.number}: {cur.name}
Key takeaways ({cur.key_takeaways_count}) — cover ALL, add nothing beyond:
{kt}

Agenda must have at most {cur.key_takeaways_count} bullets.

=== {prev_block} ===

=== {next_block} ===

=== COURSE MEMORY — PRIOR SESSIONS (from ingested PowerPoint decks) ===
Build on this; do NOT re-teach it. Use it for an accurate recap and smooth transitions.
{past}
"""
# NOTE: the reviewer-enforced (learned) rules are deliberately NOT included here.
# They are injected at SYSTEM level by generator._learned() instead, for two reasons:
#   1. authority — as a soft list at the tail of this user prompt they lost every
#      conflict with the system prompt's "HARD RULES", so corrections were ignored;
#   2. freshness — guided mode freezes this base block once per run, so feedback
#      given mid-run never reached the remaining chunks.


def build_user_prompt(prev: Session | None, cur: Session, nxt: Session | None) -> str:
    """Assemble the full user message for one-shot whole-doc generation."""
    return (build_guided_base(prev, cur, nxt)
            + f"\nNow produce the TR doc JSON for Session {cur.number}: {cur.name}.\n")


def _page_budget_block(guided: bool) -> str:
    """The page ceiling, stated in every mode.

    It is separate from the time ceiling because the two measure different things: a
    comparison table costs a third of a page and almost no narration, a chatty speaker
    note costs a minute and one line. A doc can sit inside the 40-minute budget and
    still be twenty pages, which is what the reviewer was rejecting.
    """
    pg = config.harness()["constraints"]["pages"]
    share = (" Keep this chunk proportionate to its share of that budget."
             if guided else "")
    return (
        f"\nHARD LENGTH LIMIT: the rendered document must be at most {pg['max']} pages "
        f"(aim for ~{pg['target']}).{share} Spend that budget on COVERAGE — the "
        f"sub-concepts an exam tests — never on ritual. If it is running long, cut in "
        f"this order: analogies on slides that are not a first introduction, worked "
        f"examples the topic does not need, bullets restating a table or lead-in, prose "
        f"that should be bullets, filler. Never drop a sub-concept to fit.\n")


def time_mode_block(enforce_time: bool, *, guided: bool = False) -> str:
    """The generation tail carrying the LENGTH budget the doc is graded against.

    The page ceiling is stated in EVERY mode. The 40-minute time ceiling depends on
    the toggle, which `constraints.recording.always_enforced` normally pins on:
    ON  -> the hard time limit (the whole session must fit the budget).
    OFF -> DEPTH MODE: fuller bullets/tables and no time gate — but still bounded by
           the page ceiling, which no mode relaxes.

    Used by BOTH generation modes (one-shot whole doc and guided per-chunk) so the
    behaviour is identical in each.
    """
    pages = _page_budget_block(guided)
    if not enforce_time:
        return "\n" + config.depth_mode() + "\n" + pages
    if guided:
        return ("\nHARD TIME LIMIT: the WHOLE session must be recordable within 40 minutes "
                "(aim ~36), so keep this chunk proportionate to its share of the session. "
                "Be concise and use MORE slides rather than denser ones.\n") + pages
    return ("\nHARD TIME LIMIT: the entire session MUST be recordable within 40 minutes "
            "(aim ~36). Be concise and use MORE slides rather than denser ones. Exceeding "
            "40 minutes fails the run.\n") + pages


# --------------------------------------------------------------------------- #
# Guided (chunk-by-chunk) instructions. Each returns the tail appended to the
# shared base for ONE chunk, telling the model exactly which small JSON fragment
# to emit. Section indices / boilerplate are filled at assembly, not here.
# --------------------------------------------------------------------------- #
def opening_instruction(cur: Session, prev: Session | None) -> str:
    if prev is None:
        recap_rule = 'This is the FIRST session — set "recap" to null.'
    else:
        prev_items = "\n".join(f"  {i + 1}. {k}" for i, k in enumerate(prev.key_takeaways))
        recap_rule = (
            f'"recap" must carry ALL {len(prev.key_takeaways)} agenda items of Session '
            f'{prev.number} — {prev.name}, one bullet each, in order, in the same '
            f'"topic: subtopics" format shown here and copied VERBATIM:\n{prev_items}\n'
            f"Do NOT summarise them, do NOT drop any, do NOT shorten them.")
    agenda_items = "\n".join(f"  {i + 1}. {k}" for i, k in enumerate(cur.key_takeaways))
    return f"""GUIDED MODE — produce ONLY the OPENING of this doc as JSON, nothing else:
{{
  "recap": {{ "prev_session_no": <int>, "prev_session_name": "<str>",
             "bullets": ["<str>", ...] }} | null,
  "agenda": ["<str>", ...]     // exactly {cur.key_takeaways_count} items, numbered, verbatim
}}
{recap_rule}

The agenda is NOT yours to word. Emit these {cur.key_takeaways_count} lines EXACTLY as
written, numbered "1." .. "{cur.key_takeaways_count}.", in this order — not summarised,
not re-titled, not one word changed:
{agenda_items}
Word caps do not apply to agenda or recap lines: copy them exactly even if they run long.
Return ONLY this JSON object."""


def patch_instruction(kind: str, prev_fragment_json: str, reason: str) -> str:
    """The tail that asks for a SURGICAL PATCH instead of a re-drafted chunk.

    The reviewer's note is almost always about one slide or one field. Asking for the
    section back re-rolls everything they already approved, and telling the model to
    "keep the rest identical" does not work — reproducing a thousand words verbatim is
    exactly what a sampler is bad at. So the model names the change and
    src/patcher.py performs it: whatever the patch does not mention is never passed
    through the model, so it cannot drift.
    """
    if kind == "opening":
        schema = """{
  "set_fields": { "recap": { ... } }   // and/or "agenda": [ ... ] — only these two keys
  "note": "<one line: what changed>"
}
Include ONLY the field(s) the feedback is about. Remember the agenda lines and recap
items are copied verbatim from the curriculum — never reword them to satisfy a note."""
    else:
        schema = """{
  "section_name": "<new name>" | null,          // null / omit = leave the name alone
  "edit_slides": [                              // change specific FIELDS of specific slides
    {"n": <slide number>,
     "fields": {"heading": "<new>", "content": [ ...full replacement blocks... ],
                "analogy": null}}               // null DELETES that field
  ],
  "add_slides":    [{"after_n": <slide number or null>, "slide": { ...full slide... }}],
  "remove_slides": [<slide number>, ...],
  "note": "<one line: what changed and why>"
}"""
    return f"""REGENERATE — SURGICAL PATCH ONLY.

The reviewer rejected part of this chunk. Here is the chunk EXACTLY as it stands:
{prev_fragment_json}

THEIR REASON:
{reason}

Return a JSON PATCH — not the chunk. The patch is applied programmatically, so
**anything you do not name stays exactly as it is**:
{schema}

RULES FOR THE PATCH
- Address the reason and NOTHING else. Do not "improve" a slide the reason does not
  mention, do not re-word a heading that was not complained about, do not reorder or
  renumber slides.
- Touch the FEWEST fields that fully resolve the reason. If the note is about one
  slide's analogy, the patch is one `edit_slides` entry with one field.
- Only fix an untouched slide if the reason applies to it too (e.g. "remove the
  analogies from the example slides" names more than one) — then patch each of them,
  still field by field.
- Every field you DO supply must satisfy all the house rules (word caps, roles, the
  analogy placement rule, realistic figures, no second person, no navigation).
- `n` values refer to the slides as numbered above. Do not renumber anything; final
  numbering is assigned when the document is assembled.

Return ONLY the patch JSON object."""


def takeaway_instruction(cur: Session, idx: int) -> str:
    """idx is 0-based into cur.key_takeaways."""
    takeaway = cur.key_takeaways[idx]
    return f"""GUIDED MODE — produce ONLY the SECTION covering key takeaway #{idx + 1}, as JSON:
{{
  "section": {{
    "name": "<section title>",
    "slides": [ {{ "n": <int>, "title": "...", "role": "<slide role>",
                   "heading": "...", "subheading": "...",
                   "content": [ ...ordered blocks per the format spec... ],
                   "analogy": "<ONLY when role is concept_intro — else omit>",
                   "visual_guidance": "...", "speaker_notes": "..." }} ]
  }},
  "coverage": {{
    "takeaway": "{takeaway}",
    "sub_concepts": [ {{"name": "<exam-testable sub-concept>", "slide": <n>}},
                      {{"name": "<one left to a later session>",
                        "deferred_to": "<which session, and why>"}} ]
  }}
}}
This section must teach EXACTLY this key takeaway and nothing from the others:
  "{takeaway}"
The section "name" must be this takeaway line VERBATIM (it is also the agenda item).

COVERAGE: this line names a topic, not its whole scope. List the sub-concepts an exam
would test on it, give EACH one a slide, and record that mapping in "coverage" — a
commonly-tested sub-concept that is silently missing is the most serious failure here.
At least 2 sub-concepts. If one genuinely belongs to a later session, say so explicitly
in the section text AND record it as "deferred_to" instead of a slide.

ROLES AND ANALOGIES: every slide declares a "role". An analogy is REQUIRED on a
"concept_intro" slide and FORBIDDEN on every other role (mechanism, working_example,
comparison, advantages_limitations, reasoning, application, summary) — omit the field
there. At most half the slides in this section may be "concept_intro".

WORKED EXAMPLES: include one only if this takeaway is something the learner must be
able to EXECUTE (a procedure, calculation, translation, trace, numeric trade-off). For a
definitional or classificatory takeaway, do not manufacture one. Where one belongs, use
realistic figures (hex base addresses, power-of-two sizes, real ports/PIDs), never
placeholders.

Slide numbers ("n") continue consecutively AFTER the already-approved slides shown above,
and "coverage" refers to them by those numbers. Do not repeat anything already covered in
the approved chunks. Return ONLY this JSON object."""
