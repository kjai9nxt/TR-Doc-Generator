# TR Doc — Exact Format Specification

Derived from the golden reference (Session 15: SCTP & Quality of Service).
The generator must emit a document that maps 1:1 onto this skeleton so the
`docx_writer` can render it with the correct Word styles.

## Emitted format (intermediate)

The generator returns **structured JSON** (schema below), NOT raw prose. The
`docx_writer` turns JSON into the styled `.docx`. This keeps rendering
deterministic and lets graders inspect fields directly.

```json
{
  "session_no": 15,
  "session_title": "SCTP & Quality of Service",
  "recap": {                                  // null if session 1
    "prev_session_no": 14,
    "prev_session_name": "TCP: Data, Flow & Congestion",
    "bullets": ["one-line reminder", "..."]   // 2-4 crisp reminders
  },
  "agenda": ["SCTP — Features...", "..."],    // count <= key_takeaways count
  "sections": [
    {
      "index": 1,
      "name": "SCTP — Features, Multi-Streaming & Multi-Homing",
      "slides": [
        {
          "n": 1,
          "title": "Why SCTP Exists — TCP's Two Problems",
          "role": "concept_intro",                   // why this slide exists
          "heading": "Why SCTP Exists",              // 3-4 words MAX
          "subheading": "Two Gaps TCP Left",         // 3-4 words MAX
          "content": [                         // ordered blocks
            {"type": "text", "text": "..."},
            {"type": "bullets", "items": ["...", "..."]},
            {"type": "table",
             "columns": ["Feature", "Detail"],
             "rows": [["Standard", "RFC 4960"], ["...", "..."]]},
            {"type": "code",                   // a snippet the learner reads
             "language": "jsx",                // REQUIRED — the fence/highlight language
             "code": "line one\nline two",     // REQUIRED — real newlines, no fences
             "walkthrough": [                  // what each part of it does
               {"lines": "1", "text": "..."},  // one line
               {"lines": "2-3", "text": "..."} // …or a range
             ]}
          ],
          "analogy": "TCP is a single-lane road ...",   // ONLY on concept_intro
          "visual_guidance": "Left: TCP one stream ...",
          "speaker_notes": "..."
        }
      ]
    },
    {                                          // LAST section: the practical exercise
      "index": 5,
      "kind": "hands_on",                      // marks it as practice, not a takeaway
      "name": "Hands-On: Tracing an SCTP Association Setup in Wireshark",
      "slides": [
        {
          "n": 19,
          "title": "Capture and Read the Four-Way Handshake",
          "role": "hands_on",                  // every slide in this section
          "heading": "Capture the Handshake",
          "subheading": "Wireshark Walkthrough",
          "content": [
            {"type": "text", "text": "<the task, why it matters, what is needed>"},
            {"type": "bullets", "items": ["1. Open Wireshark and … — the … list appears.",
                                           "2. …", "3. …"]},
            {"type": "image", "ref": 12, "caption": "<what the learner is looking at>"},
            {"type": "text", "text": "<40-90 words explaining the screenshot>"},
            {"type": "text", "text": "<the expected result, so the learner can check>"},
            {"type": "bullets", "items": ["<common mistake and how to fix it>", "…", "…"]}
          ]                                    // NO visual_guidance, NO speaker_notes here
        }
      ]
    }
  ],
  "coverage_map": [                           // planning artifact — NOT rendered
    {"takeaway": "SCTP: Stream Control Transmission Protocol",
     "sub_concepts": [
       {"name": "Head-of-line blocking in TCP", "slide": 1},
       {"name": "Partial reliability (PR-SCTP)",
        "deferred_to": "Session 16 — covered with SCTP extensions"}
     ]}
  ],
  "key_takeaways": ["...", "..."],            // mirror course-structure takeaways
  "upcoming_session": "Network Layer & IP Addressing",  // null if final session
  "closing": "Thank You  |  All the Best"
}
```

### The `code` block

For a course that teaches code. Three rules, all gated:

* **`language` and `code` are required.** An empty snippet is a slide that shows nothing.
* **`code` carries real newlines and no fences.** The renderer adds the fence; a snippet
  that fences itself renders as literal backticks.
* **`walkthrough` is what the learner READS about the snippet**, and it is the code
  slide's prose. A `lines` value is `"4"` or `"2-6"` and must point at lines the snippet
  actually has — a walkthrough citing line 7 of a four-line snippet is a broken
  reference, exactly like a coverage entry pointing at a slide that is not there.

A code slide with no walkthrough counts as carrying **no prose**, and the document is
gated on the share of slides that do (`constraints.content.min_slides_with_text_share`).
Code without explanation is not teaching, and the gate says so.


### The `image` block (attached screenshots)

`{"type": "image", "ref": <id>, "caption": "..."}` places a screenshot the course owner
attached to the brief (the ids are listed under ATTACHED SCREENSHOTS). Rules, all gated:
each placed image appears **exactly once** (in a run WITH a hands-on section, only the
genuine ones are placed — as many as the major parts need, all inside that section; otherwise every attached
image is placed); it goes on the slide (or hands-on step)
that shows what it depicts; and the **same slide carries a `text` block of 30+ words
explaining it** — which screen this is, what each labelled part means, what to notice.
Images are **outside the page budget**: never shorten a slide's teaching text to fit one.

### The Practical Hands-On section (only when the brief asks for it)

Present only when a skill for the session asks for a hands-on / practical / walkthrough
AND screenshots are attached — the BUDGET block states whether this run has one. When it does,
the document's **last section** is one practical exercise: `"kind": "hands_on"`, name
`Hands-On: <the concrete thing the learner does>` (at least 3 words after the prefix —
"Hands-On: Practice" fails), 1-6 slides OF ITS OWN — on top of the session's slide count,
outside the slide, page and recording budgets —, every slide `role: "hands_on"`, no
analogy, **no `visual_guidance` and no `speaker_notes`** (the instructor demonstrates the
practical live; a hands-on slide carries only its content blocks), not in the coverage map
and not counted as a takeaway section. It is the GIST of the
practical (the demo is in the recording; the step-by-step practical guide follows the
session video). It carries, in order: what gets built and why (text), the MAJOR steps as
ONE numbered bullet list of 4-7 one-line items (<= 20 words, no sub-steps), the genuine
screenshots — as many as it takes to show each major part, each with a 30-50-word
explanation —, and the expected
result (text). Which picture goes where is decided by WHAT IT SHOWS (each attached
picture is described to the writer: the screen, its state, the visible names and
values), never by its caption or file name; a picture sits only at the step it is
evidence of, and its explanation names what is visible in it. It may use only what the session taught. Rendered as
`------ PRACTICAL HANDS-ON: <title> ------`.

`role` is one of `overview`, `concept_intro`, `mechanism`, `working_example`,
`comparison`, `advantages_limitations`, `reasoning`, `application`, `summary`, and
`hands_on` (only inside the hands-on section). Like
`coverage_map`, it is a planning field: the renderer ignores it, so it costs no pages.

**A `concept_intro` paragraph follows one recipe**, so a first-time reader is never
handed a definition cold: (1) the idea in everyday words — what it is and the problem
it solves, as a friend would say it; (2) the precise definition, with the term named and
any acronym expanded as `Full Name (ACR)`; (3) why it matters — what goes wrong without
it, or where the learner will meet it. Two or three short sentences, ≤ 50 words as
the norm (longer only if the concept truly needs it); the bullets then carry
the parts, steps or kinds. The same order holds for `speaker_notes` on that slide: say
the everyday version first, then the exact term.

`overview` is the **broad** slide that opens a section — what the topic is and which
types/kinds/parts it has, named together before any one of them is taught. **The first
slide of every section must be `overview` or `concept_intro`**; anything else means
the section opened on a detail, and that is a hard failure.

## Rendering rules (docx_writer)

| JSON element                     | Word style / rendering                                   |
|----------------------------------|----------------------------------------------------------|
| `session_title`                  | Heading 1 → `Session {n} : {title}`                      |
| `recap`                          | Heading 2 → `RECAP: Session {p} : {name}` + bullets      |
| `agenda`                         | Heading 2 `Agenda for Today's Session` + bullet list     |
| section breaker                  | Heading 2 → `------ SECTION {i}: {name} ------`           |
| slide                            | Heading 3 → `Slide {n}: {title}`                         |
| `Heading:` / `Subheading:`       | normal paragraph, bold label prefix                      |
| `Content:` label then blocks     | normal paragraphs / bullets / native Word tables         |
| `Analogy:` / `Visual Guidance:` / `Speaker Notes:` | normal paragraph, bold label prefix (only if present) |
| `key_takeaways`                  | Heading 2 `Key Takeaways` + bullet list                  |
| `upcoming_session`               | normal → `Upcoming Session : {name}`                     |
| `closing`                        | normal, centered → `Thank You  |  All the Best`          |

## Section breaker literal

The breaker uses the same dash-wrapped form as the golden doc:
`--------------------------------------- SECTION {i}: {NAME} ---------------------------------------`

## Exact document layout (in this order)
1. **Session name** — `Session {n} : {title}` (Heading 1).
2. **Recap of the previous session** — its heading + the **previous session's key
   takeaways** as the recap bullets (omit entirely for Session 1).
3. **Agenda for the current session** — one bullet per key takeaway (count ≤ takeaways).
4. **One section breaker per agenda item** — each agenda item gets its own section,
   in the same order as the agenda.
5. **Slides** under each section — every slide carries all six fields (below).
6. **Practical Hands-On** — one more section (`kind: hands_on`) ONLY when the session's
   skill asks for one AND screenshots are attached; a title naming what the learner does.
7. **Key Takeaways** of the current session.
8. **Next session name** — `Upcoming Session : {name}` (omit/null on the final session).
9. **Closing** — centered `Thank You  |  All the Best`.

## Required per-slide fields
`role`, `heading`, `subheading`, `content`, `visual_guidance`, `speaker_notes` are
**REQUIRED on every slide** — none may be omitted or left empty. (A missing field is a
hard guardrail failure.) The one exception: slides of the Practical Hands-On section
carry **no** `visual_guidance` and **no** `speaker_notes` — omit both fields there (a
present one fails).

`analogy` is **conditional, and exactly so**: required when `role == "concept_intro"`,
**forbidden** on `overview`, `mechanism`, `working_example`, `comparison`,
`advantages_limitations`, `reasoning`, `application` and `summary`. Both a missing
analogy on a first introduction and a present one anywhere else are hard failures. No
more than half the slides may be `concept_intro`.

## Length ceiling
The rendered document must be within the page ceiling the BUDGET block states
(**36 pages by default, target ~32**; images excluded), estimated deterministically
from this layout by `graders/page_grader.py`, and independently recordable within the
stated minutes (40 by default) — which at ~1.5 minutes per slide means **≤ 26 slides** by
default (a course may set more, and its minute and page budgets grow with it). Over either ceiling
is a hard failure. Recording time is spent by the slide COUNT, so trimming text buys none
of it; trim only to fit PAGES, and when trimming cut ritual (unneeded analogies,
unwarranted examples, restatement, filler) — never a sub-concept.

## `coverage_map` (required)
One entry per key takeaway, in curriculum order, `takeaway` byte-identical to the
curriculum line, at least **2** sub-concepts each, and every `slide` value resolvable
to a slide in the document. A sub-concept deliberately left to a later session carries
`deferred_to` instead of `slide` and must also be named in the section text. Guardrails
verify the map against the slides that exist, which is what turns a silently missing
sub-concept into a visible failure.

`heading` and `subheading` are **3-4 word labels — hard maximum 4 words each** (no
period). A 5-word heading or subheading is a hard guardrail failure, in every mode
including depth mode. `title` keeps the looser ≤ 8-word phrase cap.

## Agenda / Recap / Key Takeaways — exact text rules
- `agenda[i]` is **byte-identical to `key_takeaways[i]`**, both numbered `1.`..`N.`
  and in the same order. Copied from the curriculum: not summarised, not re-titled,
  not one word changed. The section breaker for item *i* uses that same text.
- `recap.bullets` = **ALL of the previous session's agenda items**, in the same
  `topic: subtopics` format they had there — not a fresh summary, not a subset.
- These three lists are **exempt from every word cap**. Copy them exactly even when
  they run long; never trim one to fit a cap.

## Per-slide content rules
- **Mix prose and bullets — a document of nothing but bullets is a defect.**
  - `content` text blocks (short paragraphs): **2-3 sentences, ≤ 50 words — two or
    three lines — as the norm.** They EXPLAIN in plain language — what this is, why
    it exists, how it connects to the slide before — in as few words as carry the
    whole meaning. Two or three short related points belong here as sentences, not as
    a bullet list. A paragraph may run longer ONLY where the topic demands it, up to
    the hard ceiling of **75 words / 4 sentences**; past that is a hard failure, and so
    is a document where more than one paragraph in five exceeds the norm.
  - `bullets`: **minimum 3 items**, parallel and substantial (types, steps, causes,
    guarantees, trade-offs), **each a full sentence of 5-22 words** that says what the
    thing is or does and why it matters. A 2-3 word bullet is a label and a hard
    failure; a 25-word bullet is a paragraph and a hard failure. A one- or two-item
    list is a sentence somebody bulleted — write the sentence.
  - **At least 60% of slides must contain a `text` block.** A slide that opens
    straight into a list, with nothing saying what the list is, fails this.
- **No redundancy on a slide — and this means PARAPHRASE, not just verbatim.** The
  paragraph and the bullets under it must carry different information: the paragraph
  frames (what this is, why it exists, how it relates), the bullets carry the
  specifics it does not state (steps, types, values, conditions, trade-offs, cases).
  A bullet that restates its lead-in in other words is a hard failure — it is checked
  by word overlap, not by exact match. Apply the deletion test: if deleting the
  paragraph loses nothing, or deleting the bullets loses nothing, the slide says one
  thing twice. The same holds for a table and the bullets beside it, and for
  `speaker_notes` versus the slide body. One carrier per piece of information —
  the page ceiling is fixed, so a repeated line costs a line of coverage.
- `speaker_notes`: **≤ 4 sentences** — the teaching cue, how to explain it in plain
  words (and what to point at on a screenshot, if there is one), one exam/interview hook.
- `analogy`: on a `concept_intro` slide only; ends with an explicit tie-back naming the
  concept ("… — just as <how the concept works>"), and matches the concept structurally.
- A `working_example` slide traces a concrete case step by step using **realistic
  figures** (hex base addresses, power-of-two page sizes, real ports/RFCs, plausible
  PIDs and byte counts) — at least two concrete values, and no placeholders ("some
  address", "value X", "xyz"). Add one only where the learner must be able to EXECUTE
  something; omit it for definitional or classificatory topics.
- Slide-visible text (`title`, `heading`, `subheading`, `content`, `analogy`) carries
  **no second person** ("you"/"your") and **no navigational phrases** ("last
  session", "as we saw earlier", "in the next slide", …). `speaker_notes` may keep a
  lighter continuity voice but still no "you".

## Notes
- Tables are first-class: use them for any 2+ way comparison or spec sheet.
- Keep prose in `content` tight — this is a teaching reference, not an essay. Tight
  does not mean absent: the short paragraphs are what carry the connective reasoning
  a bullet list cannot.
- Every slide must teach a sub-concept named in `coverage_map`. A slide nothing in the
  map points at is off-agenda and fails, unless its role is `overview`, `comparison`
  or `summary` (those serve several sub-concepts at once).
