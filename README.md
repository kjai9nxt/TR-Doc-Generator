# TR Doc Generator Agent

An automation agent that generates recording-ready **TR (Teaching Reference)
documents** for individual course sessions — one session at a time — following
your house format, keeping every session recordable in **≤ 40 minutes** and within
**16 pages**, technically correct, pedagogically ordered, and market-competitive.

Length is spent on **coverage, not ritual**: an analogy appears only where a concept is
introduced for the first time, a worked example only where the learner must be able to
execute something (and always with realistic figures), and every doc emits a
`coverage_map` that is checked against the slides it claims to have.

Built with **harness engineering**: all the "how" lives in `harness/` so the
agent never re-analyses the whole project. Change behaviour by editing the
harness, not the Python.

---

## Inputs — two Google Sheets

The agent is driven by **two Google Sheets** you provide as links when the
workflow opens. The sheet/tab name can be anything; only the column headers
matter (see the template guide — matching is trimmed + case-insensitive but the
column set must be exact, or the sheet is **discarded** with a message).

| Sheet | Required columns |
|-------|------------------|
| Course Curriculum Structure | `Topic Name`, `Session`, `Session Name`, `Key Takeaways` |
| Session Details (past decks) | `Session Name`, `PPT Link` (a Google **Slides** link) |

Both the sheets and the linked decks must be shared **“Anyone with the link →
Viewer.”**  See `harness/sheet_templates.md` (or `python run.py --template-guide`).

## Course memory + live sync

On every run the agent **syncs** with both sheets:
- validates each against its template (discards + guides you if it doesn't match),
- joins them on `Session Name` to attach a session number to each deck,
- exports each Google Slides deck to `.pptx`, extracts it **once**, and caches it
  in the persistent knowledge base (`knowledge_base/`),
- **detects changes** (added/removed/renamed sessions, changed links, edited
  decks) by content hash and re-ingests only what changed — reporting a changelog.

Past sessions are never forgotten. At generation time the agent injects a
**summary of every prior deck** (so it never re-teaches and can recap) plus
**RAG-retrieved** slides most relevant to the target topic.

```bash
python run.py                 # interactive workflow: enter links, validate, sync, generate
python run.py --sync          # re-sync with saved links and print the changelog
python run.py --watch         # keep syncing on an interval, logging changes live
python run.py --setup         # change the sheet links
python run.py --template-guide
```

## What it does (the workflow)

```
course structure sheet ─┐  validate + sync
session details sheet  ─┼─► (Slides→pptx→KB) ─► GENERATE (Claude) ──► TR-doc JSON
target session         ─┘                              │
                                                       ▼
                    ┌──────────── EVALUATE ───────────────┐
                    │  guardrails   (hard structural gates)│
                    │  time grader  (40-min budget, calib.)│
                    │  page grader  (16-page ceiling)      │
                    │  LLM judge    (rubric, /100 — always)│
                    └──────────────────┬───────────────────┘
                          accepted? ──no──► REVISE (up to N rounds)
                              │yes
                              ▼
                RENDER ──► .docx (styled) + .md + grade report
```

## Quick start

```bash
pip install -r requirements.txt
# put your key in .env  (OPENROUTER_API_KEY=... ; provider is set in harness.yaml)

# Web UI (recommended):
streamlit run app.py                          # opens in your browser at http://localhost:8501

# Or the command line:
python run.py                                 # interactive workflow (asks for both sheet links)
python run.py --session 15                     # sync + generate + full grading
# The LLM quality check and the 40-minute budget always run (harness policy —
# gates.always_run_llm_judge, constraints.recording.always_enforced).
```

### Web UI (`app.py`)
A browser front end covering the whole flow:
1. **Connect your sheets** — paste both Google Sheet links; templates are validated
   (mismatches are shown and rejected) and the decks are synced.
2. **Generate** — pick a session, watch live progress as it drafts → grades → revises.
3. **Result** — see the recording-time estimate, rubric score, and **download the
   Word `.docx`** (with an in-page preview).

Outputs land in `outputs/`:
- `Session N _ <Name>.docx` — the styled TR doc
- `Session N _ <Name>.md`  — same content, quick to review
- `Session N _ <Name>.grade.json` — per-round guardrail/time/rubric report

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
| `graders/page_grader.py` | 16-page ceiling — lays out the doc against the real `.docx` metrics. |
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
python -m evals.run_sets --session N   # score one doc against all 24 eval sets
python -m evals.run_eval --live   # full pipeline on sample sessions (needs API)
```

## Tuning

Everything is in `harness/harness.yaml`:
- `constraints.recording.*` — the 40-min budget & `elaboration_factor`
  (calibrated so the golden Session 15 lands ~36 min); `always_enforced` pins it on.
- `constraints.pages.*` — the 16-page ceiling, the target, and the `.docx` layout
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
