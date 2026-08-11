# TR Doc — Style Guide

Tone, density, and voice rules. Calibrated against the golden reference.

## Voice
- Instructional, warm, confident — but **impersonal**. **No second person anywhere:**
  no "you", "your", "yours", "yourself" in `title`, `heading`, `subheading`,
  `content`, `analogy`, or even `speaker_notes`. Slide text is read, not spoken to
  someone. Write "TCP guarantees order", not "you get ordered delivery"; write
  "Emphasise the two-phase commit", not "you should emphasise…".
- **Every slide stands alone.** No navigational or cross-reference phrases in
  slide-visible text: no "last session", "previous/next session", "as we saw
  earlier", "as we discussed", "introduced earlier", "in the previous/next slide",
  "in this slide", "now let us", "let's move on", "recall that", "moving on".
  Those belong in `speaker_notes`, which may keep a lighter continuity voice.
  The only places a forward/back reference is allowed are the structural ones:
  Recap, Agenda, Upcoming Session, closing.
- Short declarative sentences. One idea per sentence.
- Indian English spelling and everyday Indian-context analogies (WhatsApp, trains,
  hospital ER, school canteen — as in the golden doc).

## Length (HARD — two ceilings, and what to cut)
- The rendered document must be **≤ 16 pages** (aim ~14) *and* recordable in
  **≤ 40 minutes** (aim ~36). They are different limits: a table costs page space but
  almost no narration, a chatty speaker note costs a minute but one line.
- **The budget belongs to coverage.** When the doc is too long, cut in this order:
  1. every analogy that is not on a first-introduction slide,
  2. worked examples on topics that do not need one,
  3. bullets that restate their lead-in or the table beside them,
  4. prose that should be bullets, and filler.
  **Never cut a sub-concept to make room.** A shorter doc that drops something an exam
  tests has failed at the thing it exists to do.

## Conciseness (HARD — this is what appears on the slide)
A TR doc is a SKELETON, not an essay. Every line must be tight enough to read at a
glance. Enforce these caps:
- **Bullets: ≤ 12 words each.** One idea per bullet. No sub-clauses, no "and also".
  If a bullet needs a comma-plus-conjunction, split it into two bullets.
- **`content` text blocks: ≤ 35 words TOTAL, 1-2 sentences.** One definition or
  framing sentence, then let the bullets and table carry the detail. A 60-90 word
  paragraph as slide content is a defect — this cap holds in depth mode too.
  Prefer bullets/tables over `text`; reach for a sentence only to frame what follows.
- **No redundancy on a slide.** Do not restate the lead-in sentence in the bullets
  under it, and do not restate a table's contents as bullets on the same slide —
  pick the table or the bullets, never both for the same information.
- **EXEMPT from every word cap:** agenda items, recap bullets, and key-takeaway
  lines. Those are copied verbatim from the curriculum; copy them exactly even when
  they run long, and never trim one to fit a cap.
- **`heading` / `subheading`: 3-4 words, HARD MAXIMUM 4** — a short slide label, never a
  sentence, no period. 5 words is a failure. Prefer 3. ("Why SCTP Exists",
  "IntServ vs DiffServ", "Scheduling and Shaping" — not "Two Problems TCP Could Not Solve".)
  This cap holds in depth mode too.
- **`title`: a phrase, not a sentence** (≤ 8 words, no period).
- **Table cells: ≤ 8 words.** Keywords, not prose.
- Cut filler words ("basically", "in order to", "it is important to note that").
  Write "TCP guarantees order" — not "It is important to understand that TCP is a
  protocol which basically guarantees that data arrives in order."

## Density (this drives the 40-minute budget)
- A slide should be *speakable in 2-5 minutes*. If a slide's content would take
  longer, split it into two slides — never shrink the font / cram.
- `content` blocks: prefer bullets and tables over paragraphs. A bullet = one beat.
- `speaker_notes`: **2 sentences, hard maximum** — one core teaching cue plus one
  exam/interview hook. Nothing else. Do NOT restate the slide body, do NOT write
  "close by…", do NOT write "tie back to the analogy". This is the primary signal for
  the time estimator, so a bloated note inflates the whole session's estimate.
- Analogy: **only on a `concept_intro` slide** — see the Analogy placement section
  below. 1-2 sentences. Concrete, everyday, not abstract — and it must
  **correlate, not just illustrate**: end with an explicit tie-back that names the
  concept, `"<everyday scene> — just as / exactly as <how the concept works>."`
  An analogy that leaves the mapping implicit is incomplete. It must also match the
  concept structurally (a cycle needs a circular-dependency scene, not a queue).
- Visual Guidance: one line — the diagram/layout to build (positions, labels, arrows).
  Not spoken aloud.

## Technical accuracy (non-negotiable)
- Use exact standard values: RFC numbers, field bit-widths, port numbers,
  thresholds (e.g. "ITU-T G.114: < 150 ms one-way delay"), header field names.
- Never invent an acronym expansion. If unsure, state the widely-accepted one.
- Comparisons must be symmetric and fair (same rows for each column).

## Pedagogical ordering (per session)
1. Hook / problem the session solves (why should the learner care).
2. Core concept introduced only after the gap is felt.
3. Mechanism / how it works.
4. Comparison or contrast (table).
5. Real-world usage / where it shows up.
Each section should feel like it hands off to the next.

## Analogy placement (where an analogy is allowed at all)
- Every slide declares a `role`. An analogy is **required** on `concept_intro` and
  **forbidden** on `mechanism`, `working_example`, `comparison`,
  `advantages_limitations`, `reasoning`, `application` and `summary`.
- The reason is simple: an analogy earns its lines by making an unfamiliar idea
  graspable the **first** time it is met. On an advantages slide, a reasoning slide or a
  comparison table the concept is already on the table — a second analogy there is
  decoration that costs page space. Omit the field entirely on those slides.
- No more than **half** the slides may be `concept_intro`. In a real session most
  slides build on a concept already introduced; labelling them otherwise to keep
  writing analogies is a failure.

## Worked examples (only where one earns its slide)
- Add a worked example only where the learner could follow every word and still not be
  able to **DO** the thing: a procedure, an algorithm, a calculation, an address or
  state translation, a traced sequence, a numeric trade-off.
- **Omit it** for definitional, classificatory or terminological topics — "what a file
  is", "types of scheduling", "components of a process". A manufactured example there
  adds length, not understanding.
- **Realistic figures, always.** Hex base/bound addresses (`0x00400000`), power-of-two
  page and frame sizes (4 KB), real port and RFC numbers, plausible PIDs (4312), byte
  counts (1500-byte MTU), timings in ms. Round toy numbers are acceptable only as
  counts of things ("3 processes", "4 frames"), never as an address, size or
  identifier — and never a placeholder ("some address", "value X", "xyz", "foo").

## Coverage rules (the most serious failure mode)
- A syllabus line names a topic, not its full scope. For each key takeaway, enumerate
  the sub-concepts an exam would test, then give each one a slide. "Page Faults" is
  not just definition + handling steps + service time — it also needs the **causes**
  (first-reference/demand-load, swapped-out, copy-on-write, illegal access) and
  **minor vs major** faults.
- Silently omitting a commonly-tested sub-concept is worse than any style defect.
  If a sub-concept genuinely belongs to a later session, **name the deferral** in that
  section rather than dropping it without a word.
- Record that enumeration in `coverage_map` — takeaway by takeaway, each sub-concept
  against the slide number that teaches it (or a named `deferred_to`). It is checked
  against the slides that actually exist, so the map is the difference between "I
  covered it" and "here is where I covered it".

## Recap rules
- ALL of the *previous* session's agenda items, in the same `topic: subtopics` format
  they had there. Not a fresh summary, not a shortened subset. Skip entirely for
  Session 1.

## Agenda rules
- Numbered `1.`..`N.`, mirroring the numbered Key Takeaways one-to-one. Agenda item
  *i* is the **identical text** to key takeaway *i*, copied from the curriculum —
  not summarised, not re-titled, not a word changed. Bullet count must be `<=` the
  number of key-takeaway lines. No new scope introduced here.

## What NOT to do
- No filler ("In this slide we will see...", "and all", "basically"). Get to the substance.
- No second person ("you"/"your") anywhere, including speaker notes.
- No cross-references in slide-visible text (see Voice).
- No content beyond the session's key takeaways (scope creep breaks the 40-min budget).
- No analogy on a slide that is not a first introduction — and no repeated analogies
  across slides, nor a reused domain/theme (if one slide uses postal mail, another must
  not use couriers/postcards; switch domains).
- No worked example on a purely definitional topic, and no toy or placeholder figures
  in the examples that do belong.
- No unexplained jargon.
