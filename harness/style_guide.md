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

## Plain language, full explanations, visible flow (HARD — this is what was missing)
The reviewer's finding on the generated documents: "the content is getting shortened,
the flow is not understandable, 2-3 words in a bullet won't work." The document is
read by someone meeting the topic for the first time, so every slide is written to be
**understood on its own, by anyone**:
- **Every term is defined the first time it appears**, in the sentence that uses it —
  "a hypervisor, the software layer that lets one physical server run several
  independent virtual machines". Never assume the reader already knows a word the
  session has not explained.
- **Say the WHY, not only the WHAT.** For every rule, mechanism or number: why it
  exists, what goes wrong without it, what it costs. A fact without its reason cannot
  be reconstructed in an exam or an interview.
- **Everyday words first, the technical term second.** "the computer pauses the
  program and looks the page up on disk" before "a major page fault". No sentence a
  school-leaver could not read aloud.
- **The flow must be visible.** Each slide's paragraph opens by connecting to the
  thought before it (without navigational phrases — connect the IDEAS: "Because the
  hardware is shared, …", "That metering is what makes …"), so the section reads as
  one explanation and not as a stack of unrelated cards. Within a section: what it is →
  why it exists → how it works → where it is used → what it costs.
- **Bullets are sentences that teach, never labels.** A bullet is 5-22 words and says
  what the thing is or does and why it matters. "Resource pooling" is a heading;
  "Resource pooling means one provider's hardware is shared by many customers, with
  capacity handed to each one as it is needed" is a bullet. Both bounds are gated.
- **Concise, not long.** Every sentence earns its place: the fewest words that still
  carry the whole meaning, in plain language. A paragraph is two or three lines — it
  frames and explains; it never becomes an essay. Longer only where the topic needs
  it, not as a habit. Cutting the reason, the example or the definition is a defect;
  cutting padding is the job.
- **Titles say what the slide is about**, in words a learner would use: "Why Cloud
  Replaced Owning Servers", not "Overview". Every section, slide and hands-on exercise
  carries a proper, specific title.

## Length (HARD — two ceilings, and what to cut)
- The rendered document must be within its **page ceiling (36 by default, aim ~32;
  screenshots are not counted)** *and* recordable within its **minute ceiling (40 by
  default)** — the BUDGET block at the end of the prompt states this run's numbers. These
  are different limits and they are consumed differently:
  recording time goes at **~1.5 minutes per slide whatever is on it** (so 26 slides
  ≈ 39 minutes — the SLIDE COUNT spends the time budget), while the amount of text is
  what spends the PAGE budget (~1.3 pages and ~150-180 words of spoken content per slide).
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

## Line lengths (HARD — gated both ways)
A TR doc is a teaching reference the instructor records from and a learner can read
alone. Every line is complete enough to teach and short enough to read at a glance:
- **Bullets: 5-22 words each**, one idea per bullet, written as a full sentence. Under
  5 words is a label and fails; over 22 is a paragraph and fails. If a bullet needs a
  second idea, make it a second bullet.
- **`content` text blocks: 2-3 FULL sentences, about 30-50 words (two or three lines)
  — the norm. Not a one-liner either: a single clipped sentence does not explain.**
  Enough to explain — what this is, why it exists, how it follows from the slide
  before — in the fewest clear words. **Go beyond only where the topic demands it**
  (a definition that cannot be split, a mechanism with a necessary caveat), up to the
  hard ceiling of 75 words / 4 sentences — and on at most one paragraph in five across
  the document. Everywhere else, two or three lines.
  This cap holds in depth mode too. The paragraph explains; the bullets and tables carry
  the steps, values, conditions and trade-offs it does not state.
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
- A slide is recorded in about **1.5 minutes**. Aim for ~120-150 words of spoken
  content (`content` + `analogy` + `speaker_notes`) — a clear, well-explained slide
  written tightly, neither thin nor padded. If a slide clearly needs more than that, split it into two slides rather
  than cramming, up to the slide ceiling.
- `content` blocks: a short framing paragraph, then bullets or a table for the
  detail. A bullet = one beat; a paragraph = the thread between the beats.
- `speaker_notes`: **2-4 sentences, hard maximum 4** — the core teaching cue, how to
  explain it in plain words (the everyday phrasing or example to say aloud), what to
  point at if the slide carries a screenshot, and one exam/interview hook. Do NOT
  restate the slide body, do NOT write "close by…", do NOT write "tie back to the
  analogy". The notes are what the instructor reads while recording, so they say HOW to
  teach the slide, not what is already on it.
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

## Worked examples (mandatory for algorithms, optional elsewhere)
- **An algorithm session owes a worked example — it is not a judgement call.** Any
  session teaching a scheduling, replacement, allocation, deadlock-avoidance (Banker's)
  or fitting algorithm must trace one step by step: a concrete input (request queue,
  reference string, allocation/max matrix, burst-time table), the steps applied in
  order, and the **computed result** — total head movement, fault count, average
  waiting time, safe or unsafe.
- **One input for all of them.** Every algorithm in the session is traced on the SAME
  data, and the section ends with a single table comparing the results. Three
  algorithms on three different queues cannot be compared, which is the only reason
  they are taught together. Never invent a fresh dataset for the second algorithm.
- **State the assumptions that change the answer** on the slide, before the trace:
  initial head position *and direction*, frame count, tie-breaking, whether the queue
  is re-sorted. A trace with unstated assumptions is a result nobody can reproduce.
- **For conceptual topics an example is optional** — include one only where a number or
  a trace genuinely aids understanding.
- Otherwise, add a worked example only where the learner could follow every word and
  still not be able to **DO** the thing: a procedure, a calculation, an address or
  state translation, a traced sequence, a numeric trade-off.
- **Omit it** for definitional, classificatory or terminological topics — "what a file
  is", "types of scheduling", "components of a process". A manufactured example there
  adds length, not understanding.
- **Realistic figures, always.** Hex base/bound addresses (`0x00400000`), power-of-two
  page and frame sizes (4 KB), real port and RFC numbers, plausible PIDs (4312), byte
  counts (1500-byte MTU), timings in ms. Round toy numbers are acceptable only as
  counts of things ("3 processes", "4 frames"), never as an address, size or
  identifier — and never a placeholder ("some address", "value X", "xyz", "foo").

## Nothing is taught twice (across the whole deck)
- Every concept, definition, criteria list, comparison table and calculation appears in
  **exactly one place** — the slide where the learner first needs it.
- No introducing a list and then summarising the same list later. No re-deriving the
  same numbers in two sections: compute once, refer to the result. No two slides making
  the same point under different titles.
- This is arithmetic, not taste. The page ceiling is fixed, so a second telling is a
  sub-concept that now has no room.

## No padding a thin topic
- A takeaway naming a single point ("why X matters") gets **at most 2 slides**. One idea
  does not become three slides by being restated under three titles.
- The slide minimum is a floor for the DOCUMENT, never a quota per section. If a section
  has more slides than material, merge them and give the pages to a takeaway that
  carries several sub-topics.

## Every slide is unique and stands alone
- The paragraph must not restate that slide's own bullets; the bullets must not restate
  the table beside them. Paragraph = what this is and why it matters. Bullets and
  tables = the specifics it does not state.
- No cross-references in slide-visible text ("as seen earlier", "in the next slide",
  "last session") — those belong in `speaker_notes`.
- Slides are numbered **1..N with no gaps**. Merge or drop a slide and everything after
  it renumbers, `coverage_map` included.
- Every title, heading, subheading and visual_guidance describes what the slide
  ACTUALLY contains — no label left over from an earlier draft.

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

## The Practical Hands-On (only where the brief asks for it)
- Not every session has one. It exists only when a skill for that session clearly asks
  for a hands-on / practical / walkthrough AND screenshots are attached; the BUDGET
  block says whether this run has one. Never add one on your own.
- When it exists: after the last takeaway's section (the last theory slides), **one more
  section** (`kind: hands_on`) gives the GIST of the practical, using only concepts from
  this session. It is not the practical guide: the instructor demos the full practical
  in the recording, and the step-by-step guide is shared after the session video.
- **The title is the exercise.** "Hands-On: Launching a Free-Tier EC2 Instance",
  "Hands-On: Tracing a Page Fault with a 3-Frame Table" — never "Hands-On: Practice".
- It carries, in order: what gets built and why; the MAJOR steps as one numbered bullet
  list (4-7 items, one line each — no sub-steps, click paths, values or commands); the
  genuine screenshots — as many as it takes to show each major part (the key screen of
  each major step, the final result; no fixed number), each explained; the expected result. Unchosen screenshots are left out.
- Written in the plainest language in the document.

## Screenshots attached to the brief
- In a run with a Practical Hands-On, screenshots go ONLY in that section and only the
  genuine ones are chosen (see above). Otherwise:
- Each attached image appears **once**, on the slide or hands-on step that shows what
  it depicts (a console screenshot belongs at the step that uses the console, not on the
  concept slide that mentions the tool).
- The same slide **explains it** in a text block of 40-90 words right after the image:
  which screen this is, what each labelled part means, what to click or read, what to
  notice. Speaker notes say what to point at.
- Images are outside the page budget. A slide with a screenshot keeps its full
  teaching text — it is longer than a slide without one, never shorter.

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
