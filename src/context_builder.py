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
    """What earlier sessions ALREADY TAUGHT, from the ingested decks.

    Two layers, deliberately:
      1. the TAUGHT INDEX — every prior session's distinct topics, de-duplicated
         (pptx_ingest.taught_index). This is the "do not teach this again" list, and
         it replaced dumping each deck's raw slide-title summary: 38,000 characters in
         which "Data Representation" appeared eight times, which cost ~10k tokens a
         run and told the model very little.
      2. RAG-retrieved prior slides most relevant to THIS session's topic, with their
         actual body text — the detail layer, so the model can see how far a prior
         session went and pick up from there rather than starting over.

    The KB is populated by the sync engine (Google Slides); if it is empty we fall
    back to any local .pptx files (offline/dev mode).
    """
    prior = pptx_ingest.decks_before(cur.number)
    if not prior and not pptx_ingest.load_all_decks():
        pptx_ingest.ingest(verbose=True)   # offline fallback: local inputs/past_ppts/
        prior = pptx_ingest.decks_before(cur.number)

    parts = []
    if prior:
        covered = ", ".join(f"S{d['session_no']}" for d in prior)
        parts.append(
            f"ALREADY TAUGHT IN THIS COURSE — sessions {covered}, extracted from their "
            f"actual decks. THIS IS BINDING: a learner reaching the target session has "
            f"ALREADY been taught everything below.\n"
            f"  · Do NOT re-teach any of it. No slide may explain, define or walk "
            f"through a topic listed here.\n"
            f"  · BUILD ON it instead: use these as established ground the new material "
            f"stands on, and assume the terms are known.\n"
            f"  · If the target session's takeaway genuinely revisits one of these, go "
            f"BEYOND what was taught — the new angle, the deeper mechanism, the case the "
            f"earlier session did not cover — never repeat the introduction.\n"
            f"  · A one-line reminder inside the Recap is the only place repetition is "
            f"allowed.\n"
            f"{pptx_ingest.taught_digest(cur.number)}")

        query = cur.name + " " + " ".join(cur.key_takeaways)
        top_k = config.harness()["context"].get("rag_top_k", 6)
        hits = pptx_ingest.retrieve(query, cur.number, top_k=top_k)
        if hits:
            rag = "\n".join(
                f"  [S{h['session_no']} · Slide {h['slide']}] {h['title']}: {h['excerpt']}"
                for h in hits)
            parts.append(
                "HOW FAR PRIOR SESSIONS ALREADY WENT ON THIS TOPIC (their actual slide "
                "content — this is the material you must NOT restate, and the level you "
                "must start ABOVE):\n" + rag)
    else:
        parts.append("(No prior decks in the knowledge base yet — treat earlier "
                      "sessions' scope as given by the course structure above.)")

    docs = past_docs_summary(cur.number)
    if docs.strip():
        parts.append("PRIOR TR DOCS (secondary reference):\n" + docs)
    return "\n\n".join(parts)


def prior_coverage_block(cur: Session, takeaway: str, *, top_k: int | None = None) -> str:
    """What prior decks already said about ONE takeaway — injected into that chunk.

    The session-level block above is retrieved once, against the whole session, and is
    frozen into the cached base context. So the chunk actually writing takeaway 4 sees
    prior material chosen for the session as a whole, not for takeaway 4 — and the
    overlap that matters is always topic-specific. This retrieves against the takeaway
    itself and goes into that chunk's own instruction.
    """
    if top_k is None:
        top_k = config.harness()["context"].get("rag_top_k_per_takeaway", 5)
    try:
        hits = pptx_ingest.retrieve(f"{cur.name} {takeaway}", cur.number, top_k=top_k)
    except Exception:
        return ""
    if not hits:
        return ""
    lines = "\n".join(
        f"  [Session {h['session_no']} · Slide {h['slide']}] {h['title']}: {h['excerpt']}"
        for h in hits)
    return (f"\nALREADY TAUGHT ON THIS EXACT TOPIC — the closest prior-session slides to "
            f"this takeaway, straight out of their decks. The learner has seen ALL of "
            f"this:\n{lines}\n"
            f"Do NOT re-explain any of it. Start above this level: assume every term and "
            f"mechanism shown here is known, and spend this section on what these slides "
            f"do NOT already cover. If a sub-concept here is genuinely part of this "
            f"takeaway, treat it as one line of assumed background and go deeper, never "
            f"as a slide re-introducing it.\n")


def course_type_block() -> str:
    """Inject the course-type teaching strategy the user chose at connect time
    (src/app_settings). Both course types must ultimately help the learner clear
    interview questions; a semester course additionally goes deep on theory.

    There is deliberately NO "current as of <date>" baseline here. A fixed date
    forces "newest version wins", which is wrong for coding sessions where the
    version or tool the industry actually uses is often not the newest one. What
    remains is the version-agnostic rule: never pass off something deprecated as
    the present state of the art."""
    from . import app_settings
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
        "=== COURSE TYPE ===\n"
        "Do NOT present a deprecated or superseded tool/version/practice as the current "
        "state of the art; if you mention a legacy item, label it as legacy. Teach the "
        "version or tool the industry actually uses for this topic — that is often NOT "
        "the newest release, and choosing the widely-used one is correct as long as you "
        "say which one you are teaching.\n"
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

{course_type_block()}

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


def slide_ceiling(enforce_time: bool) -> int:
    """The document-wide slide ceiling guardrails will actually enforce.

    Mirrors guardrails.check(rich=not enforce_time): depth mode reads `max_rich`,
    which since 1.29 is the same number — the page ceiling binds, not the slide count.
    """
    con = config.harness()["constraints"]["slides"]
    return int(con.get("max_rich", con["max"]) if not enforce_time else con["max"])


def content_budget(enforce_time: bool, n_slides: int | None = None) -> dict:
    """How much a slide may carry, derived from whichever limit actually binds.

    Under the PER-SLIDE pacing model the recording ceiling is a limit on the NUMBER of
    slides (1.5 min each, so 26 slides is 39 of the 40 minutes) and says nothing about how
    much text a slide holds. The binding content limit is therefore the PAGE ceiling, and
    the budget is derived from it: the pages left after front/back matter, divided across
    the slides, converted to words with the measured words-per-page figure so the number
    is something a writer can act on.

    Under the WORD-COUNT model the recording ceiling itself bounds the text, so the budget
    is derived by inverting graders/time_grader instead — every slide added costs 15s of
    transition and takes words away from the others.

    Either way the point is the same: a slide ceiling stated without a matching content
    budget just moves the failure from the slide gate to the length gate.
    """
    rec = config.harness()["constraints"]["recording"]
    pages = config.harness()["constraints"]["pages"]
    n = max(int(n_slides or slide_ceiling(enforce_time)), 1)

    if rec.get("pacing") == "per_slide" and rec.get("minutes_per_slide"):
        wpp = float(pages.get("words_per_page", 122))
        matter = float(pages.get("front_back_matter_pages", 1.5))

        def words_for(page_ceiling):
            return max(0, int((max(page_ceiling - matter, 0) * wpp)))

        total_max = words_for(pages["max"])
        total_target = words_for(pages.get("target", pages["max"]))
        return {"bound_by": "pages", "slides": n,
                "pages_max": pages["max"], "pages_target": pages.get("target"),
                "pages_per_slide": round(
                    (pages.get("target", pages["max"]) - matter) / n, 2),
                "total_max": total_max, "total_target": total_target,
                "per_slide_max": total_max // n,
                "per_slide_target": total_target // n}

    wpm = rec["speaking_words_per_minute"]
    factor = rec.get("elaboration_factor", 2.9)
    overhead_min = n * rec["seconds_per_slide_overhead"] / 60.0
    # Recap/agenda/takeaway lines are spoken but not elaborated much; time_grader counts
    # them in the same total, so reserve a slice rather than promising it to the slides.
    frame_reserve = 150

    def minutes_words(minutes):
        return max(0, int((max(minutes - overhead_min, 0) * wpm / factor) - frame_reserve))

    total_max = minutes_words(rec["max_minutes"])
    total_target = minutes_words(rec.get("target_minutes", rec["max_minutes"]))
    return {"bound_by": "recording_time", "slides": n,
            "total_max": total_max, "total_target": total_target,
            "per_slide_max": total_max // n,
            "per_slide_target": total_target // n}


def _slide_budget_block(enforce_time: bool, *, guided: bool) -> str:
    """STATE the slide ceiling in the prompt.

    It was enforced and never said: `constraints.slides.max` is a hard guardrail, but
    no prompt file mentioned a slide count — while HARD RULE 1 said "use MORE slides
    rather than denser slides". So the only slide-count instruction the model ever got
    pushed the count UP, against a gate that caps it. A one-shot draft happened to land
    at 14 because the time and page budgets bound it indirectly; a GUIDED run, where
    each section is drafted with no view of the whole, had nothing holding it at all and
    came back with 23 slides for 5 takeaways — which then failed the slide, time and
    page gates together, at finalize, with no revision pass to repair it.
    """
    con = config.harness()["constraints"]["slides"]
    mx = slide_ceiling(enforce_time)
    scope = ("The number below is for the WHOLE document, across every section — this "
             "chunk gets the share stated in its own instruction."
             if guided else
             "Count every slide in every section.")
    block = (
        f"\nHARD SLIDE CEILING: the document must have between {con['min']} and {mx} "
        f"slides IN TOTAL. {scope} More slides than that fails the run, so 'use more "
        f"slides rather than denser slides' applies only up to this ceiling: once it is "
        f"reached, the way to fit more sub-concepts is to put two closely-related ones "
        f"on one slide, not to add another slide.\n")
    if not enforce_time:
        return block
    cb = content_budget(enforce_time, mx)
    if cb["bound_by"] != "pages":
        # word_count pacing: the recording ceiling itself bounds the text, so more slides
        # means less on each — say the number, or "you may use N slides" reads as
        # permission to write N slides' worth of prose and the time gate fails instead.
        return block + (
            f"\nCONTENT BUDGET — MORE SLIDES MEANS LESS ON EACH, NOT MORE IN TOTAL. The "
            f"document has about {cb['total_target']} words of spoken content to spend "
            f"(content + analogy + speaker_notes across every slide; hard ceiling "
            f"{cb['total_max']}). At the {mx}-slide ceiling that is roughly "
            f"{cb['per_slide_target']} words per slide.\n")
    # per_slide pacing: the recording ceiling limits the NUMBER of slides, and the PAGE
    # ceiling limits the text. So the slides may be written at full teaching depth — the
    # budget below is close to the density these documents already have, and the extra
    # slides genuinely buy more material rather than the same material spread thinner.
    return block + (
        f"\nCONTENT BUDGET — WHAT EACH SLIDE MAY CARRY. Aim for about "
        f"{cb['per_slide_target']} words per slide of spoken content (the `content` "
        f"blocks + `analogy` + `speaker_notes`; hard ceiling ~{cb['per_slide_max']}), "
        f"which is roughly {cb['pages_per_slide']} of a rendered page. Across the whole "
        f"document that is ~{cb['total_target']} words and ~{cb['pages_target']} pages "
        f"(hard ceiling {cb['pages_max']}). Teach each slide at FULL depth to that budget "
        f"— this is not a thin skeleton. The slide ceiling and this word budget are "
        f"independent limits, so using every slide allowed does NOT require shortening "
        f"them: spend the room on the sub-concepts an exam tests, never on ritual.\n")


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
    pages = _page_budget_block(guided) + _slide_budget_block(enforce_time, guided=guided)
    if not enforce_time:
        return "\n" + config.depth_mode() + "\n" + pages
    rec = config.harness()["constraints"]["recording"]
    mx, mps = rec["max_minutes"], rec.get("minutes_per_slide")
    # Say HOW the recording time is measured, not just the ceiling. Under the per-slide
    # pacing model it is the slide COUNT that consumes the budget (a slide takes ~1.5
    # minutes to record whatever is on it), so "be concise to save time" is the wrong
    # instinct — trimming a slide's text buys no recording time at all, and dropping a
    # sub-concept to save time buys nothing while costing coverage.
    if rec.get("pacing") == "per_slide" and mps:
        allowed = int(mx // mps)
        how = (f"\nHARD TIME LIMIT: {mx} minutes for the whole session. Recording pace is "
               f"about {mps} minutes PER SLIDE regardless of how much is on it, so the "
               f"budget is spent by the slide COUNT — {allowed} slides is "
               f"{round(allowed * mps, 1)} minutes. Shortening a slide's text therefore "
               f"buys no recording time; only using fewer slides does. Do NOT drop or thin "
               f"a sub-concept in the name of the time budget.\n")
    else:
        how = (f"\nHARD TIME LIMIT: the entire session MUST be recordable within {mx} "
               f"minutes (aim ~{rec['target_minutes']}). Exceeding {mx} minutes fails the "
               f"run.\n")
    if guided:
        how += ("This chunk is one section of that session — keep it proportionate to its "
                "share, per the budgets below.\n")
    return how + pages


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


def chunk_slide_allowance(cur: Session, *, slides_used: int, sections_left: int,
                          enforce_time: bool = True) -> int:
    """How many slides THIS guided section may use.

    Guided mode drafts one section per LLM call, so no single call can see the
    document-wide slide ceiling being spent — the model was asked for "the sub-concepts
    an exam would test, EACH with a slide" and nothing else, which is an open-ended
    instruction. Five takeaways answered it with 23 slides against a ceiling of 14.

    So the ceiling is divided here, from the slides the earlier sections ACTUALLY used
    rather than from a fixed per-section quota: a section that ran one slide long
    squeezes the ones after it instead of silently pushing the total over. The remainder
    goes to the earlier sections (a 14-slide budget over 5 takeaways is 3,3,3,3,2, not
    2,2,2,2,6). The floor is min_sub_concepts_per_takeaway, since a section that cannot
    fit its required sub-concepts would fail the coverage gate instead.
    """
    budget = slide_ceiling(enforce_time)
    floor = int(config.harness()["constraints"]["coverage"]
                .get("min_sub_concepts_per_takeaway", 2))
    # A session with more takeaways than budget/floor cannot give every section that
    # floor and still fit the ceiling (8 takeaways x 2 > 14). This course tops out at 6,
    # but issuing a budget whose parts cannot sum to the whole is exactly the
    # prompt-says-X-gate-says-not-X trap the deterministic gates exist to avoid, so drop
    # to 1: sub-concepts are allowed to share a slide, and the instruction says so.
    if floor * max(1, cur.key_takeaways_count) > budget:
        floor = 1
    left = max(1, sections_left)
    base, extra = divmod(max(budget - max(slides_used, 0), 0), left)
    return max(floor, base + (1 if extra else 0))


def takeaway_instruction(cur: Session, idx: int, *, slides_used: int = 0,
                         sections_left: int | None = None,
                         enforce_time: bool = True) -> str:
    """idx is 0-based into cur.key_takeaways.

    slides_used / sections_left describe the slide budget already spent by the OTHER
    sections and how many sections (including this one) still have to fit in what is
    left — see chunk_slide_allowance. They default to a whole-budget-for-one-section
    view only so an old call site still works; server.py always passes real numbers.
    """
    takeaway = cur.key_takeaways[idx]
    if sections_left is None:
        sections_left = max(1, cur.key_takeaways_count - idx)
    allowance = chunk_slide_allowance(cur, slides_used=slides_used,
                                     sections_left=sections_left,
                                     enforce_time=enforce_time)
    budget = slide_ceiling(enforce_time)
    # A slide allowance on its own is only half a budget: the section can obey it and
    # still write twice as much per slide as the recording ceiling allows, which is how a
    # doc lands inside the slide gate and outside the time gate.
    words = ""
    if enforce_time:
        cb = content_budget(enforce_time)
        tail = ("Teach these slides at FULL depth to that budget — the slide allowance and "
                "the word budget are independent limits, so using every slide does not "
                "mean writing thinner ones."
                if cb["bound_by"] == "pages" else
                "Using every slide allowed means writing SHORTER slides, not more "
                "material, because the recording budget bounds the total text.")
        words = (f"WORD BUDGET FOR THIS SECTION: about "
                 f"{cb['per_slide_target'] * allowance} words of spoken content across "
                 f"those slides (content + analogy + speaker_notes, ~"
                 f"{cb['per_slide_target']} per slide, ceiling ~{cb['per_slide_max']}). "
                 f"The whole document has ~{cb['total_target']} to spend. {tail}\n")
    budget_block = (
        f"\nSLIDE BUDGET FOR THIS SECTION: at most {allowance} slide(s).\n"
        + words
        + f"The whole document is capped at {budget} slides; the other sections have "
        f"already used {slides_used}, and {sections_left} section(s) — including this "
        f"one — share the {max(budget - slides_used, 0)} that remain. Going over is not "
        f"a stylistic preference: the assembled document is hard-gated on the total, and "
        f"a section that overspends is one another section has to pay for.\n"
        f"If this takeaway has more exam-testable sub-concepts than {allowance} slides, "
        f"do NOT drop one and do NOT add a slide — group closely-related sub-concepts "
        f"onto one slide (a shared bullet list, or a comparison table covering several at "
        f"once) and map each of them to that slide in \"coverage\". Cut ritual first: an "
        f"analogy is only allowed on a concept_intro slide, and a worked example only "
        f"where the learner must EXECUTE something.\n")
    return budget_block + prior_coverage_block(cur, takeaway) + f"""GUIDED MODE — produce ONLY the SECTION covering key takeaway #{idx + 1}, as JSON:
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

COVERAGE — 100% OF THIS TAKEAWAY, NOTHING LEFT OVER. The curriculum line itself names
what must be taught: everything after the colon, and every item separated by a
semicolon or comma inside it, is a sub-topic this session OWES the learner. Read the
line above and list them out; each one must end up on a slide in THIS section. A
sub-topic named in the line and not taught here is a broken promise to the learner and
fails the run.
Then go further: the line names a topic, not its whole scope. Add the sub-concepts an
exam would test on it, map EACH one to the slide that teaches it in "coverage", and
stay inside the slide budget above — two sub-concepts may share a slide, but neither
may go unmapped. A commonly-tested sub-concept silently missing is the most serious
failure here. At least 2 sub-concepts.
DEFERRAL IS A LAST RESORT, not a way to make room: "deferred_to" is only for a
sub-concept that genuinely belongs to a LATER session's takeaway. Never defer anything
the curriculum line above names, never defer to fit the slide budget (group
sub-concepts onto one slide instead), and never defer most of a takeaway. If you defer
one, say so explicitly in the section text AND record it as "deferred_to".

SCOPE — NOTHING BUT THIS TAKEAWAY. It also runs the other way: every slide you write
must teach a sub-concept you list in "coverage". A slide nothing in the map points at
is off-agenda and will be rejected — cut it, or map it. The only slides allowed to
stand unmapped are the section's "overview", a "comparison" table and a "summary",
which serve several sub-concepts at once. An adjacent topic, however interesting,
spends this session's budget on something the learner was not promised.

BROAD -> SPECIFIC (structural — the first slide of this section is checked):
Open on the LANDSCAPE, then go narrow. Slide 1 of this section must have role
"overview" (or "concept_intro" if this takeaway is a single concept rather than a
family of things): say what this topic is and name ALL the types / kinds / parts /
stages it has, together, in one place. Only then take them one at a time — what each
is, where it came from or why it was introduced, how it works, what it costs — and
finish with the cross-cutting view (comparison, trade-offs, real use). Never open on
one type, one formula or one step.

PROSE AND BULLETS — MIX THEM. A section made only of bullet lists reads as choppy and
loses the reasoning that connects the points.
- A short `text` paragraph (<= 55 words, 2-3 sentences) frames, defines or connects.
  At least 60% of the slides in this section must carry one, and no slide should open
  straight into a list with nothing saying what the list is.
- `bullets` are for a REAL list: 3 or more parallel, substantial items (types, steps,
  causes, guarantees, trade-offs), each <= 12 words. Never emit a one- or two-item
  bullet list — that is a sentence somebody bulleted; write the sentence.
- Two or three short related points go in the paragraph; long, independent ones go in
  bullets. Use a `table` for any 2+ way comparison.

TECHNICAL CORRECTNESS: check every specific before writing it — standard/RFC numbers,
port numbers, field names and bit-widths, thresholds, acronym expansions, complexities,
version numbers. If you are not certain of one, teach the concept WITHOUT it ("a
well-known port", "a fixed-size header") rather than reaching for a plausible value:
this document is recorded and taught, so an invented figure outlives the session. Keep
every value consistent with the approved chunks above.

ROLES AND ANALOGIES: every slide declares a "role". An analogy is REQUIRED on a
"concept_intro" slide and FORBIDDEN on every other role (overview, mechanism,
working_example, comparison, advantages_limitations, reasoning, application, summary) —
omit the field there. At most half the slides in this section may be "concept_intro".

WORKED EXAMPLES: include one only if this takeaway is something the learner must be
able to EXECUTE (a procedure, calculation, translation, trace, numeric trade-off). For a
definitional or classificatory takeaway, do not manufacture one. Where one belongs, use
realistic figures (hex base addresses, power-of-two sizes, real ports/PIDs), never
placeholders.

Slide numbers ("n") continue consecutively AFTER the already-approved slides shown above,
and "coverage" refers to them by those numbers. Do not repeat anything already covered in
the approved chunks. Return ONLY this JSON object."""
