"""Build the context the generator needs: prior-session material + the target.

Primary source of 'the past' = the course's PowerPoint decks, ingested into the
persistent knowledge base (see pptx_ingest). For each generation we:
  1. incrementally sync the KB (only new/changed .pptx get processed),
  2. inject a compact SUMMARY of EVERY prior deck (nothing skipped) so the agent
     never re-teaches and can recap correctly,
  3. add RAG-retrieved slides most relevant to the target topic for detail.
Prior TR docs (.docx), if any, are folded in as a secondary signal.
"""
from __future__ import annotations
import glob
import re
from pathlib import Path

import docx as docxlib

from . import config, pptx_ingest, learning
from .course_loader import Session


def _docx_paragraph_texts(path: Path) -> list[str]:
    d = docxlib.Document(str(path))
    return [(p.text.strip(), p.style.name) for p in d.paragraphs if p.text.strip()]


def summarize_past_doc(path: Path) -> str:
    """Compact one prior TR doc into title + sections + slide headings."""
    title = None
    lines: list[str] = []
    for text, style in _docx_paragraph_texts(path):
        if style == "Heading 1" and title is None:
            title = text
        elif "SECTION" in text:
            lines.append(f"  {re.sub(r'-+', '', text).strip()}")
        elif style == "Heading 3" and text.lower().startswith("slide"):
            lines.append(f"    - {text}")
    head = title or path.stem
    return head + ("\n" + "\n".join(lines) if lines else "")


def _session_no_from_name(path: Path) -> int:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else 10**6


def past_docs_summary(before_session: int) -> str:
    """Summaries of all TR docs for sessions < before_session, in order."""
    pattern = config.harness()["context"]["past_docs_glob"]
    paths = sorted(
        (Path(p) for p in glob.glob(str(config.ROOT / pattern))),
        key=_session_no_from_name,
    )
    chunks = []
    for p in paths:
        if _session_no_from_name(p) < before_session:
            chunks.append(summarize_past_doc(p))
    return "\n\n".join(chunks)


def past_ppts_context(cur: Session) -> str:
    """Build the prior-material block from the knowledge base: a summary of every
    earlier deck + RAG-retrieved relevant slides. The KB is populated by the sync
    engine (Google Slides); if it is empty we fall back to any local .pptx files
    (offline/dev mode)."""
    prior = pptx_ingest.decks_before(cur.number)
    if not prior and not pptx_ingest.load_all_decks():
        pptx_ingest.ingest(verbose=True)   # offline fallback: local inputs/past_ppts/
        prior = pptx_ingest.decks_before(cur.number)

    parts = []
    if prior:
        summaries = "\n\n".join(d["summary"] for d in prior)
        covered = ", ".join(f"S{d['session_no']}" for d in prior)
        parts.append(f"SUMMARY OF EVERY PRIOR DECK (already taught — do NOT re-teach; "
                     f"sessions covered: {covered}):\n{summaries}")

        query = cur.name + " " + " ".join(cur.key_takeaways)
        top_k = config.harness()["context"].get("rag_top_k", 6)
        hits = pptx_ingest.retrieve(query, cur.number, top_k=top_k)
        if hits:
            rag = "\n".join(
                f"  [S{h['session_no']} · Slide {h['slide']}] {h['title']}: {h['excerpt']}"
                for h in hits)
            parts.append("MOST RELEVANT PRIOR SLIDES TO THIS TOPIC (for continuity/detail):\n" + rag)
    else:
        parts.append("(No prior decks in the knowledge base yet — treat earlier "
                      "sessions' scope as given by the course structure above.)")

    docs = past_docs_summary(cur.number)
    if docs.strip():
        parts.append("PRIOR TR DOCS (secondary reference):\n" + docs)
    return "\n\n".join(parts)


def build_guided_base(prev: Session | None, cur: Session, nxt: Session | None) -> str:
    """The shared context block (course + target + prev/next + course memory),
    WITHOUT a final 'produce the doc' instruction. One-shot generation appends the
    whole-doc instruction (build_user_prompt); guided generation appends a
    per-chunk instruction (opening_instruction / takeaway_instruction)."""
    kt = "\n".join(f"- {k}" for k in cur.key_takeaways)
    prev_block = "This is the FIRST session of the course — OMIT the recap."
    if prev:
        prev_kt = "\n".join(f"  - {k}" for k in prev.key_takeaways)
        prev_block = (
            f"Previous session (for RECAP): Session {prev.number} — {prev.name}\n"
            f"Its key takeaways:\n{prev_kt}"
        )
    next_block = (
        f"Next session (for the sign-off): {nxt.name}" if nxt
        else "This is the FINAL session — set upcoming_session to null."
    )
    past = past_ppts_context(cur)

    return f"""COURSE: Computer Networks
MODULE: {cur.module}
TOPIC: {cur.topic}

=== TARGET SESSION ===
Session {cur.number}: {cur.name}
Key takeaways ({cur.key_takeaways_count}) — cover ALL, add nothing beyond:
{kt}

Agenda must have at most {cur.key_takeaways_count} bullets.

=== {prev_block} ===

=== {next_block} ===

=== COURSE MEMORY — PRIOR SESSIONS (from ingested PowerPoint decks) ===
Build on this; do NOT re-teach it. Use it for an accurate recap and smooth transitions.
{past}

{learning.learned_rules_block()}"""


def build_user_prompt(prev: Session | None, cur: Session, nxt: Session | None) -> str:
    """Assemble the full user message for one-shot whole-doc generation."""
    return (build_guided_base(prev, cur, nxt)
            + f"\nNow produce the TR doc JSON for Session {cur.number}: {cur.name}.\n")


# --------------------------------------------------------------------------- #
# Guided (chunk-by-chunk) instructions. Each returns the tail appended to the
# shared base for ONE chunk, telling the model exactly which small JSON fragment
# to emit. Section indices / boilerplate are filled at assembly, not here.
# --------------------------------------------------------------------------- #
def opening_instruction(cur: Session, prev: Session | None) -> str:
    recap_rule = ("This is the FIRST session — set \"recap\" to null."
                  if prev is None else
                  f"\"recap\" must summarise Session {prev.number} — {prev.name} "
                  f"in 2-4 crisp bullets.")
    return f"""GUIDED MODE — produce ONLY the OPENING of this doc as JSON, nothing else:
{{
  "recap": {{ "prev_session_no": <int>, "prev_session_name": "<str>",
             "bullets": ["<str>", ...] }} | null,
  "agenda": ["<str>", ...]     // at most {cur.key_takeaways_count} bullets, one per key takeaway, in order
}}
{recap_rule}
The agenda bullets must map one-to-one onto the key takeaways (same order).
Return ONLY this JSON object."""


def takeaway_instruction(cur: Session, idx: int) -> str:
    """idx is 0-based into cur.key_takeaways."""
    takeaway = cur.key_takeaways[idx]
    return f"""GUIDED MODE — produce ONLY the SECTION covering key takeaway #{idx + 1}, as JSON:
{{
  "section": {{
    "name": "<section title>",
    "slides": [ {{ "n": <int>, "title": "...", "heading": "...", "subheading": "...",
                   "content": [ ...ordered blocks per the format spec... ],
                   "analogy": "...", "visual_guidance": "...", "speaker_notes": "..." }} ]
  }}
}}
This section must teach EXACTLY this key takeaway and nothing from the others:
  "{takeaway}"
Slide numbers ("n") continue consecutively AFTER the already-approved slides shown above.
Do not repeat anything already covered in the approved chunks. Return ONLY this JSON object."""
