# Google Sheet Templates — How Your Sheets Must Look

The agent reads **two** Google Sheets. The **sheet/tab name can be anything** —
only the **column headers** matter, and they must match *exactly* (extra spaces
and letter-case are ignored, but no column may be missing and no extra column
may be present). A sheet that does not match is **discarded** and you will be
asked to resend it in the correct template.

Both sheets must be shared so the link is viewable: in Google Sheets →
**Share → General access → “Anyone with the link” → Viewer**.

---

## Sheet 1 — Course Curriculum Structure

Exactly these four columns (in any order):

| Topic Name | Session | Session Name | Key Takeaways |
|------------|---------|--------------|---------------|
| Transport Layer Basics | 9 | Introduction to Transport Layer | - Role of Transport Layer<br>- Port Numbers & Sockets<br>- Multiplexing & Demultiplexing |
| … | … | … | … |

- **Topic Name** — the module/topic this session belongs to.
- **Session** — the session number (e.g. `9`).
- **Session Name** — the session's title. This is the **join key** with Sheet 2.
- **Key Takeaways** — one line per takeaway (newline-separated, `-` bullets fine).

## Sheet 2 — Session Details (past decks)

Exactly these two columns (in any order):

| Session Name | PPT Link |
|--------------|----------|
| Introduction to Transport Layer | https://docs.google.com/presentation/d/…/edit |
| Understanding TCP and UDP | https://docs.google.com/presentation/d/…/edit |

- **Session Name** — must match a Session Name in Sheet 1 (this is how a deck is
  attached to its session).
- **PPT Link** — the Google **Slides** link for that session's deck. The deck
  itself must also be link-viewable.

---

## Common reasons a sheet is discarded
- A required column is **missing** or **misspelled** (e.g. `Sessions` instead of `Session`).
- An **extra** column is present that is not in the template.
- The link is **not shared** (“Anyone with the link → Viewer”) so the agent cannot read it.

Fix the template and re-enter the link — the agent will re-validate immediately.
