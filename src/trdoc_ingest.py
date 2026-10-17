"""Read an uploaded TR doc (.docx) into the same record shape as an extracted deck.

A session becomes course memory when the agent holds what was taught in it. That used
to mean its deck (a Google Slides link, or a Web Slides upload). A finished TR doc is
the better record: it is the reviewed teaching document itself, slide by slide, with
the speaker notes. So it is read into a deck-shaped record and filed in the session's
slot in the deck store, where everything that reads "already taught" (taught_index,
decks_before, retrieve, the generate list) picks it up with no change of its own.

The TR doc layout this reads (the agent's own output, and the hand-written ones it was
modelled on):

    Heading 1   Session 15 : SCTP & Quality of Service
    Heading 2   RECAP: Session 14 : …            ← the PREVIOUS session; not this one's
    Heading 2   Agenda for Today's Session
    Heading 2   ---- SECTION 1: …
    Heading 3   Slide 1: Why SCTP Exists        ← one slide starts here
                Heading: … / Subheading: … / Content: … / Visual Guidance: …
                Speaker Notes: "…"
    Heading 2   Key Takeaways

A slide starts at any paragraph reading "Slide N: title" (heading style or not). A doc
with no such markers falls back to its Heading 2/3 paragraphs as slide boundaries, so
a TR doc written in some other shape still yields its text rather than nothing.
"""
from __future__ import annotations

import hashlib
import io
import re

MAX_BYTES = 40 * 1024 * 1024

_SLIDE = re.compile(r"^\s*slide\s+(\d+)\s*[:.\-–—]\s*(.*)$", re.I)
_SECTION = re.compile(r"^[\s\-–—]*section\s+\d+\s*:", re.I)
_LABEL = re.compile(r"^\s*(heading|subheading|content|visual guidance|speaker notes|"
                    r"analogy|example)\s*:\s*(.*)$", re.I | re.S)
# Parts of a TR doc that are not material taught in THIS session.
_SKIP_HEAD = re.compile(r"^\s*(recap\b|agenda\b)", re.I)


def _heading_level(par) -> int | None:
    name = (getattr(par.style, "name", "") or "").lower()
    m = re.match(r"heading\s*(\d)", name)
    if m:
        return int(m.group(1))
    if name == "title":
        return 1
    return None


def _iter_blocks(doc):
    """Paragraphs and tables in document order (python-docx lists them separately)."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            yield Paragraph(child, doc)
        elif tag == "tbl":
            yield Table(child, doc)


def _table_rows(tbl) -> list[list[str]]:
    rows = []
    for row in tbl.rows:
        cells, last = [], None
        for c in row.cells:
            # Merged cells repeat the same cell object across the span; keep it once.
            if c._tc is last:
                continue
            last = c._tc
            cells.append(c.text.strip())
        if any(cells):
            rows.append(cells)
    return rows


def extract_trdoc(data: bytes, session_no: int | None, session_name: str,
                  filename: str) -> tuple[str, dict]:
    """(content hash, deck-shaped record) for an uploaded TR doc."""
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"That file is {len(data) / 1e6:.0f} MB; the limit is "
                         f"{MAX_BYTES // (1024 * 1024)} MB.")
    if not filename.lower().endswith(".docx"):
        raise ValueError(f"'{filename}' is not a .docx. Upload the TR doc as a Word "
                         f"document (.docx) — from Google Docs, File → Download → .docx.")
    if data[:2] != b"PK":
        raise ValueError("That .docx is not a Word file (wrong signature).")
    from docx import Document
    try:
        doc = Document(io.BytesIO(data))
    except Exception as e:
        raise ValueError(f"The TR doc could not be opened as a Word document: {e}")

    # One entry per LINE: a hand-edited doc often holds "Slide 3: …", "Heading: …" and
    # "Subheading: …" in one paragraph separated by line breaks.
    blocks = []
    for b in _iter_blocks(doc):
        if not hasattr(b, "text"):
            blocks.append(b)
            continue
        level = _heading_level(b)
        blocks += [(line, level) for line in (b.text or "").split("\n")]
    has_markers = any(isinstance(b, tuple) and _SLIDE.match(b[0]) for b in blocks)

    doc_title, slides, cur, skipping = "", [], None, False

    def start(title: str):
        nonlocal cur
        cur = {"title": title.strip(), "body": [], "notes": [], "tables": []}
        slides.append(cur)

    for b in blocks:
        if not isinstance(b, tuple):                     # a table
            if cur is not None and not skipping:
                rows = _table_rows(b)
                if rows:
                    cur["tables"].append(rows)
            continue
        text, level = b[0].strip(), b[1]
        if not text:
            continue
        m = _SLIDE.match(text)
        if level == 1 and not doc_title:
            doc_title = text
            continue
        if m and has_markers:
            skipping = False
            start(m.group(2) or f"Slide {m.group(1)}")
            continue
        if level is not None and level <= 3:
            # Recap/agenda and section dividers end the current slide; they are framing.
            if _SKIP_HEAD.match(text):
                skipping, cur = True, None
                continue
            if _SECTION.match(text) or re.match(r"^\s*-{3,}", text):
                skipping, cur = False, None
                continue
            if re.match(r"^\s*key takeaways?\b", text, re.I):
                skipping = False
                start("Key Takeaways")
                continue
            if not has_markers:
                skipping = False
                start(text)
                continue
        if skipping or cur is None:
            continue
        lm = _LABEL.match(text)
        if lm:
            label, rest = lm.group(1).lower(), lm.group(2).strip()
            if label == "speaker notes":
                if rest:
                    cur["notes"].append(rest.strip('"“” '))
                cur["_in_notes"] = True
                continue
            cur["_in_notes"] = False
            if label == "heading" and rest and not cur["title"]:
                cur["title"] = rest
            if label in ("content",) and not rest:
                continue
            if rest:
                cur["body"].append(f"{lm.group(1)}: {rest}" if label not in
                                   ("heading", "content") else rest)
            continue
        (cur["notes"] if cur.get("_in_notes") else cur["body"]).append(text)

    out = []
    for s in slides:
        body = "\n".join(s["body"]).strip()
        if not (s["title"] or body or s["tables"]):
            continue
        out.append({"n": len(out) + 1, "title": s["title"] or body.split("\n")[0][:80],
                    "body": body, "notes": "\n".join(s["notes"]).strip(),
                    "tables": s["tables"]})
    if not out:
        raise ValueError("No teaching content was found in that document. A TR doc is "
                         "read slide by slide from its “Slide N: …” headings.")

    deck_title = doc_title or session_name or filename
    summary = f"{deck_title}\n" + "\n".join(
        f"    - Slide {s['n']}: {s['title']}" for s in out if s["title"])
    chash = hashlib.md5(data).hexdigest()
    return chash, {
        "session_no": session_no,
        "source_file": filename,
        "source_kind": "trdoc",
        "source_hash": chash,
        "deck_title": deck_title,
        "n_slides": len(out),
        "summary": summary,
        "slides": out,
    }
