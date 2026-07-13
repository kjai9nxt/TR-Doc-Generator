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
          "heading": "Why was SCTP Created?",
          "subheading": "Two Problems TCP Could Not Solve",
          "content": [                         // ordered blocks
            {"type": "text", "text": "..."},
            {"type": "bullets", "items": ["...", "..."]},
            {"type": "table",
             "columns": ["Feature", "Detail"],
             "rows": [["Standard", "RFC 4960"], ["...", "..."]]}
          ],
          "analogy": "TCP is a single-lane road ...",   // optional, omit if not useful
          "visual_guidance": "Left: TCP one stream ...", // optional
          "speaker_notes": "..."                          // optional
        }
      ]
    }
  ],
  "key_takeaways": ["...", "..."],            // mirror course-structure takeaways
  "upcoming_session": "Network Layer & IP Addressing",  // null if final session
  "closing": "Thank You  |  All the Best"
}
```

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
6. **Key Takeaways** of the current session.
7. **Next session name** — `Upcoming Session : {name}` (omit/null on the final session).
8. **Closing** — centered `Thank You  |  All the Best`.

## Required per-slide fields (ALL SIX, on EVERY slide)
`heading`, `subheading`, `content`, `analogy`, `visual_guidance`, `speaker_notes`
are **all REQUIRED on every slide** — none may be omitted or left empty. (A missing
field is a hard guardrail failure.)

## Notes
- `recap.bullets` = the previous session's key takeaways, stated concisely.
- Tables are first-class: use them for any 2+ way comparison or spec sheet.
- Keep prose in `content` tight — this is a teaching reference, not an essay.
