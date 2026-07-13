# TR Doc — Style Guide

Tone, density, and voice rules. Calibrated against the golden reference.

## Voice
- Instructional, warm, confident. Second person ("you") when addressing the learner.
- Short declarative sentences. One idea per sentence.
- Indian English spelling and everyday Indian-context analogies (WhatsApp, trains,
  hospital ER, school canteen — as in the golden doc).

## Conciseness (HARD — this is what appears on the slide)
A TR doc is a SKELETON, not an essay. Every line must be tight enough to read at a
glance. Enforce these caps:
- **Bullets: ≤ 12 words each.** One idea per bullet. No sub-clauses, no "and also".
  If a bullet needs a comma-plus-conjunction, split it into two bullets.
- **`text` blocks: ≤ 2 short sentences,** each ≤ 18 words. Prefer bullets/tables over
  `text` — reach for a paragraph only when a list genuinely won't do.
- **`heading` / `subheading` / `title`: a phrase, not a sentence** (≤ 8 words, no period).
- **Table cells: ≤ 8 words.** Keywords, not prose.
- Cut filler words ("basically", "in order to", "it is important to note that").
  Write "TCP guarantees order" — not "It is important to understand that TCP is a
  protocol which basically guarantees that data arrives in order."

## Density (this drives the 40-minute budget)
- A slide should be *speakable in 2-5 minutes*. If a slide's content would take
  longer, split it into two slides — never shrink the font / cram.
- `content` blocks: prefer bullets and tables over paragraphs. A bullet = one beat.
- `speaker_notes`: 2-4 crisp sentences — the natural narration of the slide, spoken
  aloud. This is the primary signal for the time estimator. Do NOT restate the bullets
  verbatim; add the connective explanation a teacher would say.
- Analogy: 1-2 sentences. Concrete, everyday, not abstract.
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

## Recap rules
- 2-4 one-line reminders of the *previous* session's key ideas — enough to reload
  context, not a re-teach. Skip entirely for Session 1.

## Agenda rules
- Mirror the session's key takeaways at a higher level. Bullet count must be
  `<=` the number of key-takeaway lines. No new scope introduced here.

## What NOT to do
- No filler ("In this slide we will see..."). Get to the substance.
- No content beyond the session's key takeaways (scope creep breaks the 40-min budget).
- No repeated analogies across slides.
- No unexplained jargon.
