# DEPTH MODE — richer, teaching-complete document

This block is appended to the generation prompt ONLY when the 40-minute recording
limit is turned OFF. It deliberately OVERRIDES the concision caps in the STYLE
GUIDE so the document teaches the topic fully instead of staying a thin skeleton.
When the limit is ON, this block is absent and the concise skeleton rules apply.

This is a HARD OVERRIDE of the STYLE GUIDE's brevity rules. In this mode the
document must be substantially FULLER and DEEPER than the default skeleton — a
document that is merely correct-but-thin is a FAILURE here.

**HARD RULE 1 of the system prompt (the 40-minute ceiling) DOES NOT APPLY in this
mode.** There is no recording-time limit: the session may run as long as teaching
the topic properly takes. Do not trim, compress, or drop material to fit any
duration, and do not mention a time budget. Recording time is not graded on this
run — length is judged only as depth versus filler.

## Concrete targets (aim for all of these)

**Depth means BREADTH OF COVERAGE, not fatter slides.** Get there by covering more
sub-concepts on more slides — never by writing longer paragraphs or longer speaker
notes. A 90-word paragraph is still a defect in this mode.

- **13–18 slides total.** Do NOT stop at one slide per key takeaway. A rich
  session needs multiple slides per takeaway plus dedicated worked-example and
  compare/contrast slides.
- **Enumerate the sub-concepts and give each its own slide.** For every key takeaway,
  list the sub-concepts an exam would test and cover each one — this is where the
  extra slides come from. A missing commonly-tested sub-concept is the most serious
  failure in this mode too.
- **Each slide's `content` stays tight:** a `text` block is still ≤ 35 words / 1-2
  sentences (one framing or definition sentence), with the detail carried by richer
  bullets and tables. In this mode bullets may be longer and more numerous than the
  12-word skeleton cap, and tables may have more rows — but the framing sentence
  does not become a paragraph.
- **Overall length: aim for ~2,200–3,000 words** of skeleton content (roughly
  double the concise skeleton), reached through MORE slides and fuller bullet/table
  detail. Depth, not padding, and not longer prose blocks.

## What changes in this mode

1. **Some concision caps are overridden — but NOT these five.** The STYLE GUIDE's
   bullet ≤12-word cap and table-cell cap DO NOT apply; write bullets and tables as
   full as the teaching needs. Write to teach, not to fit a stopwatch (there is no
   time limit in this mode). Still no filler or repetition.
   **These five hold in this mode exactly as in the default mode:**
   - `heading` / `subheading` stay 3-4 words, hard maximum 4;
   - a `content` text block stays ≤ 35 words / 1-2 sentences (depth goes into
     bullets, tables and extra slides — never into a paragraph);
   - `speaker_notes` stay ≤ 2 sentences;
   - no second person and no navigational phrases (see the STYLE GUIDE's Voice rules);
   - agenda items are the verbatim numbered key takeaways, and the recap carries all
     of the previous session's agenda items.

2. **Define, then explain (broad → specific).** For each core concept, give the
   precise/textbook definition FIRST, then translate it into plain language and
   intuition. Never settle for a one-line gloss when the concept has real depth.

3. **Worked examples are MANDATORY.** Add AT LEAST ONE dedicated slide that works
   through a concrete example step by step (a specific scenario, a small case
   analysis, a before/after, a traced sequence). Show every step of the reasoning,
   not just the result. Explicitly cover the instructive edge/contrast cases
   (e.g. "looks like X but isn't", single vs multiple, valid vs invalid). If the
   topic has a classic worked example, include it.

4. **Explain WHY, not only WHAT.** For every rule, technique, or mechanism, state
   the reason it works (or fails) — the underlying principle — so the learner can
   reconstruct it, not memorise it.

5. **ADD extra slides beyond one-per-takeaway (required, not optional).** Include
   additional slides such as: worked-example slide(s), a compare/contrast or
   "commonly confused with…" slide, and a real-world/code example where relevant.
   Group each under the most relevant agenda section. (The AGENDA still has at most
   one bullet per key takeaway — extra depth goes into slides within a section, not
   into new agenda items.) Use the higher depth-mode slide ceiling.

6. **Speaker notes stay at 2 sentences.** Depth does NOT mean longer notes. One core
   teaching cue (the trap or misconception to flag) plus one exam/interview hook —
   and stop. No restating the slide, no "close by…".

## What does NOT change

- Every slide still carries all six fields (heading, subheading, content,
  analogy, visual_guidance, speaker_notes).
- `heading` / `subheading` stay 3-4 words each (hard max 4) — unchanged by depth mode.
- `content` text blocks stay ≤ 35 words / 1-2 sentences; `speaker_notes` stay ≤ 2
  sentences. Neither cap is relaxed here.
- Slide `content` still must NOT contain spoken narration or meta-narration
  ("in this slide…", "in the previous/next session…") — that belongs in
  speaker_notes. No second person ("you"/"your") anywhere, notes included.
- Analogies still end with an explicit tie-back naming the concept.
- Content stays accurate, grounded in the source material, on-topic, and free of
  future-session leakage. Analogies stay distinct across slides (no reused
  domain/theme).
- The required document structure and ordering are unchanged.
