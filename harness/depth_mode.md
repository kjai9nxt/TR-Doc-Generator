# DEPTH MODE — spend the budget on coverage, not on length

This block is appended to the generation prompt ONLY when the 40-minute recording
limit is turned off. **It is normally never used:** `constraints.recording.always_enforced`
is true, so every session is generated as a 40-minute session. It exists for the case
where that flag is deliberately set false.

What this mode changes is where the effort goes, not how long the document is. Earlier
versions of this block removed the length limit entirely and demanded a worked example
on every doc — which produced documents nobody could record and nobody wanted to read.

## What still binds in this mode

- **The 16-page ceiling applies unchanged** (`constraints.pages.max`). There is no mode
  in which the document may run past it. Aim for the target (~14 pages).
- **Slide ceiling unchanged** (`constraints.slides.max_rich` = 14). Depth is not more
  slides for their own sake.
- `heading` / `subheading` stay 3-4 words, hard maximum 4.
- A `content` text block stays ≤ 35 words / 1-2 sentences. Depth goes into bullets,
  tables and better-chosen slides — never into a paragraph. A 90-word paragraph is a
  defect in this mode too.
- `speaker_notes` stay ≤ 2 sentences: one teaching cue plus one exam/interview hook.
- **The analogy rule is unchanged and exact:** an analogy on `concept_intro` slides
  only, and no more than half the slides may be `concept_intro`. Depth never means
  more analogies.
- **Worked examples stay conditional**, on exactly the same terms: one only where the
  learner must be able to EXECUTE something (a procedure, algorithm, calculation,
  translation, trace, or numeric trade-off), never for a definitional or
  classificatory topic. Where one does belong, use realistic figures.
- No second person, no navigational phrases, the verbatim numbered agenda, the full
  previous-session recap, the required document order, and `coverage_map` — all
  unchanged.

## What actually changes

1. **Recording time is not gated.** The 40-minute ceiling (HARD RULE 1's time half)
   does not apply and is not graded on this run. Do not mention a time budget. The
   PAGE half of HARD RULE 1 still applies.

2. **Bullets and tables may be fuller.** The style guide's 12-word bullet cap and
   8-word table-cell cap are lifted: write the bullet the teaching needs. Still no
   filler and no repetition — a longer bullet must carry more information, not more
   words.

3. **Define, then explain (broad → specific).** For each core concept give the precise
   definition first, then translate it into plain language and intuition. Do not settle
   for a one-line gloss where the concept has real depth.

4. **Explain WHY, not only WHAT.** For every rule, technique or mechanism, state the
   reason it works or fails, so the learner can reconstruct it rather than memorise it.
   Put that on a `reasoning` slide — which, like every non-introduction slide, carries
   no analogy.

5. **Use the freed room for sub-concepts.** Depth here means the exam-testable
   sub-concepts get proper treatment and the instructive edge cases ("looks like X but
   isn't", single vs multiple, valid vs invalid) get named — inside the page ceiling,
   by writing tighter, not longer.
