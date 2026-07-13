"""Render a TR-doc JSON into a styled .docx matching the golden format,
and a parallel Markdown file for quick review."""
from __future__ import annotations
from pathlib import Path

import docx as docxlib
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

BREAKER = "---------------------------------------"


def _labelled(doc, label: str, value: str):
    p = doc.add_paragraph()
    run = p.add_run(f"{label} ")
    run.bold = True
    p.add_run(str(value))
    return p


def _render_content(doc, blocks):
    for block in blocks or []:
        t = block.get("type")
        if t == "text":
            doc.add_paragraph(block.get("text", ""))
        elif t == "bullets":
            for item in block.get("items", []):
                doc.add_paragraph(str(item), style="List Bullet")
        elif t == "table":
            cols = block.get("columns", [])
            rows = block.get("rows", [])
            if not cols:
                continue
            table = doc.add_table(rows=1, cols=len(cols))
            table.style = "Light Grid Accent 1"
            for i, c in enumerate(cols):
                cell = table.rows[0].cells[i]
                cell.text = str(c)
                for para in cell.paragraphs:
                    for r in para.runs:
                        r.bold = True
            for row in rows:
                cells = table.add_row().cells
                for i in range(len(cols)):
                    cells[i].text = str(row[i]) if i < len(row) else ""


def write_docx(doc_json: dict, out_path: Path) -> Path:
    d = docxlib.Document()
    n = doc_json.get("session_no", "")
    title = doc_json.get("session_title", "")

    d.add_heading(f"Session {n} : {title}", level=1)
    d.add_paragraph()

    recap = doc_json.get("recap")
    if recap:
        d.add_heading(
            f"RECAP: Session {recap['prev_session_no']} : {recap['prev_session_name']}",
            level=2)
        for b in recap.get("bullets", []):
            d.add_paragraph(str(b), style="List Bullet")

    d.add_heading("Agenda for Today's Session", level=2)
    for a in doc_json.get("agenda", []):
        d.add_paragraph(str(a), style="List Bullet")

    for sec in doc_json.get("sections", []):
        d.add_paragraph()
        d.add_heading(f"{BREAKER} SECTION {sec['index']}: {sec['name']} {BREAKER}", level=2)
        for s in sec.get("slides", []):
            d.add_heading(f"Slide {s['n']}: {s['title']}", level=3)
            _labelled(d, "Heading:", s.get("heading", ""))
            _labelled(d, "Subheading:", s.get("subheading", ""))
            p = d.add_paragraph()
            p.add_run("Content:").bold = True
            _render_content(d, s.get("content", []))
            if s.get("analogy"):
                _labelled(d, "Analogy:", s["analogy"])
            if s.get("visual_guidance"):
                _labelled(d, "Visual Guidance:", s["visual_guidance"])
            if s.get("speaker_notes"):
                _labelled(d, "Speaker Notes:", f"\"{s['speaker_notes']}\"")
            d.add_paragraph()

    d.add_heading("Key Takeaways", level=2)
    for k in doc_json.get("key_takeaways", []):
        d.add_paragraph(str(k), style="List Bullet")

    d.add_paragraph()
    up = doc_json.get("upcoming_session")
    if up:
        _labelled(d, "Upcoming Session :", up)

    closing = d.add_paragraph(doc_json.get("closing", "Thank You  |  All the Best"))
    closing.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for r in closing.runs:
        r.bold = True
        r.font.size = Pt(14)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.save(str(out_path))
    return out_path


def _content_blocks(content) -> list[str]:
    """Markdown blocks for one slide's ordered content (text / bullets / table)."""
    out = []
    for block in content or []:
        bt = block.get("type")
        if bt == "text":
            out.append(block.get("text", ""))
        elif bt == "bullets":
            out.append("\n".join(f"- {i}" for i in block.get("items", [])))
        elif bt == "table":
            cols = block.get("columns", [])
            tbl = ["| " + " | ".join(cols) + " |",
                   "| " + " | ".join(["---"] * len(cols)) + " |"]
            tbl += ["| " + " | ".join(str(c) for c in row) + " |"
                    for row in block.get("rows", [])]
            out.append("\n".join(tbl))
    return out


def _slide_blocks(s: dict) -> list[str]:
    """Markdown blocks for one slide, matching the .docx layout."""
    blocks = [f"### Slide {s['n']}: {s['title']}",
              f"**Heading:** {s.get('heading','')}  \n"
              f"**Subheading:** {s.get('subheading','')}",
              "**Content:**"]
    blocks += _content_blocks(s.get("content", []))
    # Each extra is its OWN block (blank-line separated) so Analogy / Visual
    # Guidance / Speaker Notes are clearly distinguishable in the preview.
    if s.get("analogy"):
        blocks.append(f"**Analogy:** {s['analogy']}")
    if s.get("visual_guidance"):
        blocks.append(f"**Visual Guidance:** {s['visual_guidance']}")
    if s.get("speaker_notes"):
        blocks.append(f"**Speaker Notes:** \"{s['speaker_notes']}\"")
    return blocks


def chunk_to_markdown(kind: str, fragment: dict) -> str:
    """Render ONE guided chunk (opening or section) to Markdown for the review pane."""
    blocks: list[str] = []
    if kind == "opening":
        recap = fragment.get("recap")
        if recap:
            blocks.append(f"## RECAP: Session {recap.get('prev_session_no','')} : "
                          f"{recap.get('prev_session_name','')}")
            if recap.get("bullets"):
                blocks.append("\n".join(f"- {b}" for b in recap["bullets"]))
        else:
            blocks.append("_(First session — no recap.)_")
        blocks.append("## Agenda for Today's Session")
        if fragment.get("agenda"):
            blocks.append("\n".join(f"- {a}" for a in fragment["agenda"]))
    else:  # section
        sec = fragment.get("section", fragment)
        idx = sec.get("index", "")
        blocks.append(f"## {BREAKER} SECTION {idx}: {sec.get('name','')} {BREAKER}")
        for s in sec.get("slides", []):
            blocks += _slide_blocks(s)
    return "\n\n".join(blocks) + "\n"


def write_markdown(doc_json: dict, out_path: Path) -> Path:
    # Each element of `blocks` is one Markdown block. Blocks are joined with a
    # BLANK line ("\n\n") so headings, paragraphs, lists and tables each render
    # as separate elements. (A single newline is NOT a line break in Markdown —
    # that was collapsing the whole doc into one flowing paragraph in the UI.)
    # Tight label pairs use a trailing "  " (hard break) to stack without a gap.
    blocks: list[str] = []
    n, title = doc_json.get("session_no", ""), doc_json.get("session_title", "")
    blocks.append(f"# Session {n} : {title}")

    recap = doc_json.get("recap")
    if recap:
        blocks.append(f"## RECAP: Session {recap['prev_session_no']} : {recap['prev_session_name']}")
        if recap.get("bullets"):
            blocks.append("\n".join(f"- {b}" for b in recap["bullets"]))

    blocks.append("## Agenda for Today's Session")
    if doc_json.get("agenda"):
        blocks.append("\n".join(f"- {a}" for a in doc_json["agenda"]))

    for sec in doc_json.get("sections", []):
        blocks.append(f"## {BREAKER} SECTION {sec['index']}: {sec['name']} {BREAKER}")
        for s in sec.get("slides", []):
            blocks += _slide_blocks(s)

    blocks.append("## Key Takeaways")
    if doc_json.get("key_takeaways"):
        blocks.append("\n".join(f"- {k}" for k in doc_json["key_takeaways"]))
    if doc_json.get("upcoming_session"):
        blocks.append(f"**Upcoming Session :** {doc_json['upcoming_session']}")
    blocks.append(f"**{doc_json.get('closing','Thank You  |  All the Best')}**")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return out_path
