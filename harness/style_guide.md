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
- The rendered document must be **≤ 26 pages** (aim ~23) *and* recordable in
  **≤ 40 minutes**. These are different limits and they are consumed differently:
  recording time goes at **~1.5 minutes per slide whatever is on it** (so 26 slides
  ≈ 39 minutes — the SLIDE COUNT spends the time budget), while the amount of text is
  what spends the PAGE budget (~0.85 page and ~100 words of spoken content per slide).
- So **trimming a slide's text buys no recording time.** Never thin or drop a
  sub-concept to fit the 40 minutes; that trade does not exist. Cut only to fit PAGES.
- **The budget belongs to coverage.** When the doc is too long, cut in this order:
  1. anything not on the agenda (a slide no coverage-map entry points at),
  2. every analogy that is not on a first-introduction slide,
  3. worked examples on topics that do not need one,
  4. bullets that restate their lead-in or the table beside them, and filler.
  Converting paragraphs into bullets is **not** on this list: the prose/bullet mix is
  required, and the pages it would save are negligible.
  **Never cut a sub-concept to make room.** A shorter doc that drops something an exam
  tests has failed at the thing it exists to do.

## Prose and bullets — the MIX (this is a house rule, not a preference)
A document that is nothing but bullet lists reads as choppy, looks odd on the page,
and loses the connective tissue — why this exists, how these two relate, what follows
from that table — that a bullet cannot carry. Every slide is written as a mix:

| Use a short paragraph (`text`) when… | Use `bullets` when… |
|---|---|
| framing what the slide is about | there are **3 or more** parallel items |
| defining a term and saying why it exists | they are types, steps, causes, guarantees, trade-offs |
| connecting this idea to the previous one | each item stands on its own and is substantial |
| there are only **two or three short, related points** | the items would be clumsy to read as a sentence |

- **At least 60% of slides must carry a `text` block.** A slide that opens straight
  into a list, with nothing saying what the list is, is the defect this rule exists
  to stop.
- **Never write a one- or two-item bullet list.** That is a sentence that was
  bulleted; write the sentence.
- Short points → prose. Long, independent points → bullets. If an item needs a
  comma-plus-conjunction to hold together, it belongs in a bullet of its own or in
  the paragraph, never as half a bullet.

### The paragraph and the bullets must say DIFFERENT things
The most wasteful mistake on a slide is a lead-in sentence followed by bullets that
repeat it in other words. It is not caught by "don't duplicate text" — nothing is ever
duplicated word for word — and it is not a style nit: the page ceiling is fixed, so
every repeated line is a line that cannot teach something new.

| The paragraph carries | The bullets carry |
|---|---|
| what this is, in one framing sentence | the steps of the mechanism |
| why it exists / what problem it solves | the distinct types, cases or conditions |
| how it relates to what came before | the concrete values, limits, trade-offs |
| what follows from the table beside it | where it shows up in practice |

**The deletion test, before you emit a slide:** delete the paragraph — what is lost?
Delete the bullets — what is lost? If either answer is "nothing", the slide says one
thing twice; rewrite the bullets to carry what the paragraph left out.

✗ Wrong — the bullets are the sentence again:
> Applications call generic read, write and control operations; device drivers
> translate these into device-specific commands.
> - System calls expose a uniform I/O interface
> - Device drivers hide hardware-specific command details
> - Same read/write call works across device types

✓ Right — the paragraph frames, the bullets add what it does not say:
> A uniform read/write/ioctl interface lets one application work with any device,
> because the driver below it absorbs the differences.
> - Block devices: random access in fixed-size blocks (disks)
> - Character devices: byte streams, no seeking (keyboards, serial)
> - Network devices: socket interface rather than read/write
> - Escape hatch: ioctl passes device-specific commands straight through

The same rule holds between a table and the bullets beside it, and between
`speaker_notes` and the slide body — one carrier per piece of information.

## Conciseness (HARD — this is what appears on the slide)
A TR doc is a SKELETON, not an essay. Every line must be tight enough to read at a
glance. Enforce these caps:
- **Bullets: ≤ 12 words each.** One idea per bullet. No sub-clauses, no "and also".
  If a bullet needs a comma-plus-conjunction, split it into two bullets.
- **`content` text blocks: ≤ 55 words TOTAL, 2-3 sentences.** Enough for a real
  framing paragraph, not enough for an essay. A 90-word block is a defect — this cap
  holds in depth mode too.
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

## Density (this drives the PAGE budget)
- A slide is recorded in about **1.5 minutes**. Aim for ~100 words of spoken content
  (`content` + `analogy` + `speaker_notes`) — that is a full, well-taught slide, not a
  thin one. If a slide clearly needs more than that, split it into two slides rather
  than cramming, up to the 26-slide ceiling.
- `content` blocks: a short framing paragraph, then bullets or a table for the
  detail. A bullet = one beat; a paragraph = the thread between the beats.
- `speaker_notes`: **2 sentences, hard maximum** — one core teaching cue plus one
  exam/interview hook. Nothing else. Do NOT restate the slide body, do NOT write
  "close by…", do NOT write "tie back to the analogy". A bloated note costs PAGE budget
  (recording time is paced per slide, not per word), and it buries the one cue that
  matters.
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
- **If you are not certain of a specific, leave it out and teach the concept without
  it.** "A well-known port", "a fixed-size header", "an RFC-standardised extension"
  are all acceptable; a plausible-sounding wrong number is not. This document is
  recorded and taught, so an invented figure outlives the session.
- Never invent an acronym expansion. If unsure, state the widely-accepted one.
- **Be consistent with yourself.** The same value, definition or expansion must read
  identically everywhere it appears in the document; two different numbers for one
  thing means at least one of them is wrong.
- Simplify the level of detail, never the truth. Where a rule has a standard exception
  a learner will meet, name it rather than stating the rule absolutely.
- Comparisons must be symmetric and fair (same rows for each column).

## Pedagogical ordering (per session)
1. Hook / problem the session solves (why should the learner care).
2. Core concept introduced only after the gap is felt.
3. Mechanism / how it works.
4. Comparison or contrast (table).
5. Real-world usage / where it shows up.
Each section should feel like it hands off to the next.

## Broad → specific (per section)
Within each section the order is **map first, then the territory**:
1. **Overview** — what this topic is, and which types / kinds / parts / stages it has,
   named together in one place. This is the section's first slide (`role: overview`,
   or `concept_intro` when the takeaway is one concept rather than a family).
2. **Each one in turn** — for each type named above: what it is, where it came from or
   why it was introduced, how it works, what it costs.
3. **Across them** — the comparison table, the trade-offs, where each is used.

Never open a section on one type, one formula or one step. A learner who meets the
third kind of something before knowing there are four has to rebuild the map
afterwards, and that is where a session loses people.

## Analogy placement (where an analogy is allowed at all)
- Every slide declares a `role`. An analogy is **required** on `concept_intro` and
  **forbidden** on `overview`, `mechanism`, `working_example`, `comparison`,
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
- **The takeaway line is a contract.** The curriculum writes it as
  `Topic: item; item, item`. Every item after the colon must be taught in that
  takeaway's section — none dropped, none deferred, none left "implied".
- A syllabus line then names a topic, not its full scope. For each key takeaway,
  enumerate the sub-concepts an exam would test, then give each one a slide. "Page
  Faults" is not just definition + handling steps + service time — it also needs the
  **causes** (first-reference/demand-load, swapped-out, copy-on-write, illegal access)
  and **minor vs major** faults.
- **Deferral is a last resort.** `deferred_to` is for a sub-concept that genuinely
  belongs to a later session — never for something the line names, never to make room.
  A takeaway with most of its sub-concepts deferred has been postponed, not taught.
- **The session must be complete when it ends**: every takeaway delivered, nothing left
  dangling, and the closing matter present.

## Do not re-teach earlier sessions
- The context carries an **ALREADY TAUGHT** inventory built from the decks earlier
  sessions actually recorded, plus the prior slides closest to this topic. The learner
  has seen all of it.
- No slide may introduce, define or walk through anything on that list. Use those terms
  freely instead — they are established ground.
- When a takeaway deliberately revisits an earlier topic, **start above where the
  earlier session stopped**: the deeper mechanism, the harder case, the edge it did not
  reach. Going deeper is required; re-running the introduction is a failure.
- The Recap is the only place a prior concept is restated, and only as its one line.
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
- **No content beyond the session's key takeaways.** The agenda is the scope: every
  slide teaches a sub-concept named in `coverage_map`, and a slide nothing points at
  is cut. An adjacent topic spends the budget on something the learner was not
  promised and pushes out something they were.
- No wall of bullets, and no one- or two-item bullet list (see the mix rules above).
- No specific you are not sure of — teach the concept without it instead.
- No analogy on a slide that is not a first introduction — and no repeated analogies
  across slides, nor a reused domain/theme (if one slide uses postal mail, another must
  not use couriers/postcards; switch domains).
- No worked example on a purely definitional topic, and no toy or placeholder figures
  in the examples that do belong.
- No unexplained jargon.
