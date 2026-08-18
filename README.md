# TR Doc Generator Agent

An automation agent that generates recording-ready **TR (Teaching Reference)
documents** for individual course sessions — one session at a time — following
your house format, keeping every session recordable in **≤ 40 minutes** and within
**26 pages**, technically correct, pedagogically ordered, and market-competitive.

Length is spent on **coverage, not ritual**: an analogy appears only where a concept is
introduced for the first time, a worked example only where the learner must be able to
execute something (and always with realistic figures), and every doc emits a
`coverage_map` that is checked against the slides it claims to have.

**A key takeaway is a contract.** The curriculum writes one as `Topic: item; item, item`
— every item after the colon is owed to the learner, and a guardrail checks each one is
actually taught in that takeaway's section. Deferring to a later session is a last
resort, never a way to make room, and never for something the line itself names.

Built with **harness engineering**: all the "how" lives in `harness/` so the
agent never re-analyses the whole project. Change behaviour by editing the
harness, not the Python.

---

## The curriculum lives in the agent

The course is stored in the agent's own database and edited in the **curriculum
dashboard**: add a session, fix a takeaway, attach a deck link, press Save. A Google
Sheet is only how a course gets in the **first time**.

Two things follow from that, and both were the point:

- **A deck is downloaded once, ever.** Each row records the content hash of the deck
  extracted from its link, so a sync fetches only links that are new or changed.
  Editing a takeaway downloads nothing. (Google's Slides export endpoint sends no
  ETag, no `Last-Modified` and `Cache-Control: no-store`, so "did this change?" cannot
  be asked without downloading the whole ~4.7 MB file — which is exactly why the old
  behaviour cost ~100 s per sync on a 30-deck course. It is now ~1 s.)
- **No sheet is needed to generate.** Open the app and the course is there.

`↻ Re-check all decks` re-downloads everything, for the one case the cheap path cannot
see: somebody edited the slides behind a link that did not change.

## Input — one Google Sheet (first time only)

The agent is driven by **one Google Sheet** you provide as a link: your course
curriculum. The sheet/tab name can be anything; only the column headers matter (see
the template guide — matching is trimmed + case-insensitive but the column set must
be exact, or the sheet is **discarded** with a message).

| Sheet | Required columns |
|-------|------------------|
| Course Curriculum Structure | `Topic Name`, `Session`, `Session Name`, `Key Takeaways`, `PPT Links` |

`PPT Links` holds the Google **Slides** deck for a session that has already been
recorded, on that session's own row; leave it **blank** for a session still to come.
(It replaces the old second sheet joined on `Session Name` — with the link on the
row, renaming a session can no longer detach it from its deck.)

The sheet and the linked decks must be shared **“Anyone with the link → Viewer.”**
See `harness/sheet_templates.md` (or `python run.py --template-guide`).

## Course memory + live sync

On every run the agent **syncs** with the sheet:
- validates it against the template (discards + guides you if it doesn't match),
- reads each row's deck link straight off that row,
- exports each Google Slides deck to `.pptx`, extracts it **once**, and caches it
  in the persistent knowledge base (`knowledge_base/`),
- **detects changes** (added/removed/renamed sessions, changed links, edited
  decks) by content hash and re-ingests only what changed — reporting a changelog.

Past sessions are never forgotten, and the decks are not decoration — they decide what
the new document is allowed to say. At generation time the agent injects:

- an **already-taught index** — every earlier session's distinct topics, extracted from
  the deck that was actually recorded (de-duplicated: 950 titled slides become ~290
  topics). The writer is told this is binding: build on it, never re-teach it.
- the **prior slides themselves** for the topic at hand — retrieved for the session, and
  again per takeaway inside each guided chunk, so the model can see how far the earlier
  session went and start above it.

The same index goes to the **LLM judge** (which used to grade "no repetition" without
ever being shown what earlier sessions taught) and to a **guardrail** that fails any
slide re-introducing a concept an earlier deck already introduced. Revisiting a topic to
go *deeper* is required whenever a takeaway names it — repeating the introduction is not.

```bash
python run.py                 # interactive setup: enter the sheet link, validate, sync
python run.py --sync          # re-sync with the saved link and print the changelog
python run.py --watch         # keep syncing on an interval, logging changes live
python run.py --setup         # change the sheet link
python run.py --template-guide
```

## What it does (the workflow)

Generation is **always guided**: the doc is written one chunk per key takeaway and a
human approves each chunk before the document is assembled. (The old one-shot mode —
a whole doc drafted in a single call, unseen until it was finished — has been removed.)

```
curriculum sheet ──┐  validate + sync
 (+ PPT Links)     ├─► (Slides→pptx→KB) ─► GENERATE CHUNK i (one per takeaway)
target session ────┘                              │
                                                  ▼
                                   HUMAN REVIEW: approve / regenerate with a reason
                                                  │ all approved
                                                  ▼
                                              ASSEMBLE
                    ┌──────────── EVALUATE ───────────────┐
                    │  guardrails   (hard structural gates)│
                    │  time grader  (40-min budget, calib.)│
                    │  page grader  (26-page ceiling)      │
                    │  LLM judge    (rubric, /100 — always)│
                    └──────────────────┬───────────────────┘
                       length/accuracy failure ──► bounded REPAIR (keep best)
                              │
                              ▼
                RENDER ──► .docx (styled) + .md + grade report
```

## Quick start

```bash
pip install -r requirements.txt
# put your key in .env  (OPENROUTER_API_KEY=... ; provider is set in harness.yaml)

./start.sh          # backend (FastAPI) + React UI — open the URL it prints

# Command line (sheet sync only; docs are written in the web app):
python run.py                 # interactive setup (asks for the one sheet link)
python run.py --sync
# The LLM quality check and the 40-minute budget always run (harness policy —
# gates.always_run_llm_judge, constraints.recording.always_enforced).
```

### Web UI (`server.py` + `frontend/`)
A browser front end covering the whole flow:
1. **Connect your sheet** — paste the curriculum Google Sheet link; the template is
   validated (mismatches are shown and rejected) and the decks are synced.
2. **Generate all chunks** — pick a session; one chunk is generated per key takeaway.
3. **Review each chunk** — approve it, or regenerate with a reason (the reason is also
   distilled into a durable rule for future sessions).
4. **Create the final TR doc** — see the recording-time estimate, rubric score, and
   **download the Word `.docx`** (with an in-page preview).

Outputs land in `outputs/`:
- `Session N _ <Name>.docx` — the styled TR doc
- `Session N _ <Name>.md`  — same content, quick to review
- `Session N _ <Name>.grade.json` — per-round guardrail/time/rubric report

#### Changing the UI: rebuild the bundle, always

`frontend/dist` is **committed**, and it is what the deployed instance serves —
`render.yaml`'s build command is `pip install -r requirements.txt`, so nothing rebuilds
the bundle on deploy. Editing `frontend/src` alone therefore ships the *old* UI: the
repo shows the new code and the browser runs the old, indefinitely and silently. That
is how the curriculum "insert a session" button went on numbering new rows **35** at
the top of a 34-session course long after both `server.py` and `App.jsx` had been
fixed.

So every frontend change is two things:

```bash
cd frontend && npm run build
git add frontend/dist
```

Two guards make sure that is not something anyone has to remember:

- **A pre-commit hook** blocks a commit that stages `frontend/src` (or `index.html`,
  `vite.config.js`, `package.json`, `package-lock.json`) without staging
  `frontend/dist`. It compares staged paths only — no Node, no build, no delay.
  Install it once per clone:

  ```bash
  git config core.hooksPath .githooks
  ```

  Bypass for a change that genuinely cannot affect the bundle:
  `SKIP_DIST_CHECK=1 git commit …`

- **CI** does the authoritative check in the `frontend-build` job: it rebuilds and
  fails if the result differs byte-for-byte from what is committed. Vite's output is
  deterministic for a given source and lockfile (verified identical across Node 18 and
  Node 22, and across repeated builds), so any difference means a stale bundle. Run the
  same check yourself with `npm run verify:dist` from `frontend/`.

## Layout

| Path | Role |
|------|------|
| `harness/harness.yaml` | **Single source of truth** — model, constraints, structure, gates. |
| `harness/system_prompt.md` | Generation contract (the agent's instructions). |
| `harness/format_spec.md` | Exact TR-doc JSON schema + render rules. |
| `harness/style_guide.md` | Tone, density, pedagogy rules. |
| `rubrics/tr_doc_rubric.yaml` | 13 scored dimensions → /100 (LLM judge). |
| `guardrails/guardrails.py` | Deterministic hard gates (structure, recap, slide roles, analogy placement, example realism, coverage map…). |
| `graders/time_grader.py` | 40-min recording estimator (calibrated to the golden). |
| `graders/page_grader.py` | 26-page ceiling — lays out the doc against the real `.docx` metrics. |
| `graders/llm_judge.py` | LLM-as-judge rubric scorer. |
| `src/patcher.py` | Applies a surgical regeneration patch, so untouched slides stay byte-identical. |
| `evals/` | Golden fixture (Session 15) + 24 eval sets + runners + gate regression. |
| `src/pptx_ingest.py` | PPTX extraction + persistent, incremental knowledge base + RAG. |
| `src/` | Loader, context builder, generator, docx writer, pipeline. |
| `inputs/course/` | Course-structure `.xlsx` (already contains the CN structure). |
| `inputs/past_ppts/` | **Drop the course's `.pptx` decks here** — the agent's memory. |
| `knowledge_base/` | Persistent extracted memory (auto-managed; don't edit by hand). |
| `outputs/` | Generated Word docs + grade reports. |

## Output

The TR doc is always a **Word document** (`.docx`) with the exact house styling
(Heading 1/2/3, section breakers, native tables). A parallel `.md` is written
only for quick review.

## Evals

```bash
python -m evals.run_eval          # offline: golden through all gates (no API)
python -m evals.test_gates        # offline: each gate fires on its own defect (no API)
python -m evals.test_api_contracts # offline: handlers only read fields their model declares
python -m evals.test_endpoints    # boots the real server, real HTTP, throwaway DB (no API)
python -m evals.test_cloud_driver # the DEPLOYED driver (libSQL) on a local file; skips if absent
npm --prefix frontend run test:ui # mounts the real App.jsx in jsdom
python -m evals.run_sets --session N   # score one doc against all 24 eval sets
python -m evals.run_eval --live   # full pipeline on sample sessions (needs API)
```

`test_endpoints` and `test_api_contracts` exist because the content suites all call the
pipeline directly and the UI harness stubs the API, so for a while **nothing ran the
HTTP handlers** — a request field a handler read but its model never declared shipped
broken with every suite green. `test_endpoints` starts uvicorn and posts real JSON;
only sign-in and the generation thread are faked, and the thread only after the handler
has finished, so body parsing, curriculum lookup and the run row are all real.

## Tuning

Everything is in `harness/harness.yaml`:
- `constraints.recording.*` — the 40-min budget & `elaboration_factor`
  (calibrated so the golden Session 15 lands ~36 min); `always_enforced` pins it on.
- `constraints.pages.*` — the 26-page ceiling, the target, and the `.docx` layout
  metrics the page estimator uses.
- `constraints.slide_roles` / `constraints.analogy` — the slide-role vocabulary and the
  analogy-placement biconditional (required iff `concept_intro`).
- `constraints.worked_example` / `constraints.examples` — when an example earns a slide,
  and what counts as a realistic figure.
- `constraints.coverage.*` — the `coverage_map` requirement and its minimums.
- `regeneration.*` — surgical (patch) vs full re-draft, and the over-broad-patch warning.
- `gates.*` — rubric thresholds, revision rounds, `always_run_llm_judge`.
- `constraints.slides.*` — min/max slide count.
- `market_reference_platforms` / `pedagogy` — fed into generation.

## Status

Offline pipeline (parse → guardrails → time → render → evals) is **verified end
to end** against the golden. Generation + LLM judge activate the moment
`ANTHROPIC_API_KEY` is set.
