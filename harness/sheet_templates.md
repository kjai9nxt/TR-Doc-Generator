# Google Sheet Template — How Your Sheet Must Look

The agent reads **one** Google Sheet: your course curriculum. The **sheet/tab name
can be anything** — only the **column headers** matter, and they must match *exactly*
(extra spaces and letter-case are ignored, but no column may be missing and no extra
column may be present). A sheet that does not match is **discarded** and you will be
asked to resend it in the correct template.

The sheet must be shared so the link is viewable: in Google Sheets →
**Share → General access → "Anyone with the link" → Viewer**.

---

## The Course Curriculum Structure sheet

Exactly these five columns (in any order):

| Topic Name | Session | Session Name | Key Takeaways | PPT Links |
|------------|---------|--------------|---------------|-----------|
| Transport Layer Basics | 9 | Introduction to Transport Layer | 1. Role of Transport Layer: …<br>2. Port Numbers & Sockets: …<br>3. Multiplexing & Demultiplexing: … | https://docs.google.com/presentation/d/…/edit |
| Transport Layer Basics | 10 | Understanding TCP and UDP | 1. TCP vs UDP: … | *(blank — not recorded yet)* |

- **Topic Name** — the module/topic this session belongs to. It may be filled in on
  the first row of a topic only; blank rows inherit it.
- **Session** — the session number (e.g. `9`).
- **Session Name** — the session's title.
- **Key Takeaways** — one line per takeaway (newline-separated; numbering or `-`
  bullets are both fine). These become the agenda, the section breakers and the Key
  Takeaways list **verbatim**, so write them the way they should appear.
- **PPT Links** — the Google **Slides** link for that session's deck, on the same row
  as the session. The deck itself must also be link-viewable.

### About the PPT Links column
- **Leave it blank for a session that has not been recorded yet.** A blank is not an
  error: sessions *with* a deck are treated as already taught (they become the agent's
  memory of the course and are excluded from the "generate a TR doc" list), and
  sessions *without* one are the sessions still needing a TR doc.
- This replaces the old second sheet. Because the link now sits on the session's own
  row, a deck can no longer be silently lost by renaming a session in one sheet and
  not the other.

---

## Common reasons the sheet is discarded
- A required column is **missing** or **misspelled** (e.g. `Sessions` instead of
  `Session`, or `PPT Link` instead of `PPT Links`).
- An **extra** column is present that is not in the template.
- The link is **not shared** ("Anyone with the link → Viewer") so the agent cannot
  read it.

Fix the template and re-enter the link — the agent will re-validate immediately.
