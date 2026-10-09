"""Web Slides decks — uploaded as a FILE, not fetched from a link.

A course taught from Google Slides gives the agent a link per session, and gslides.py
exports it as .pptx. A course taught from Web Slides has no such link: the author
downloads the deck and uploads the file here. Whatever the download is, it has to end
up as the SAME deck record pptx_ingest writes for a Google deck — slides with a title,
a body, notes and tables — so that retrieval, the taught index and the writer never
learn there was a second tool.

Three download shapes are read:

  .pptx          handed to the existing extractor, byte for byte the Google path;
  .pdf           one slide per page, first line as the title (pypdf);
  .html / .htm   one slide per <section> (or per <div class="slide…">), falling back to
                 the page's headings when there are no slide elements.

Anything else is refused with a message naming what IS accepted, because a deck that
could not be read must never be recorded as if it had been.
"""
from __future__ import annotations
import hashlib
import io
import re
from pathlib import PurePosixPath

from . import gslides, pptx_ingest

ACCEPTED = (".pptx", ".pdf", ".html", ".htm")
MAX_BYTES = 60 * 1024 * 1024


def _kind(filename: str) -> str:
    return PurePosixPath((filename or "").replace("\\", "/")).suffix.lower()


def accepted(filename: str) -> bool:
    return _kind(filename) in ACCEPTED


def _finish(slides: list[dict], session_no: int | None, session_name: str,
            filename: str) -> dict:
    deck_title = (slides[0]["title"] if slides and slides[0]["title"] else "") \
        or session_name or filename
    summary_lines = [f"    - Slide {s['n']}: {s['title']}" for s in slides if s["title"]]
    return {
        "session_no": session_no,
        "source_file": filename,
        "source_link": "",
        "source_kind": "web",
        "deck_title": deck_title,
        "n_slides": len(slides),
        "summary": f"{deck_title}\n" + "\n".join(summary_lines),
        "slides": slides,
    }


def _slide(n: int, text: str) -> dict:
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    title = lines[0][:80] if lines else ""
    body = "\n".join(lines[1:]) if len(lines) > 1 else ""
    return {"n": n, "title": title, "body": body, "notes": "", "tables": []}


def _from_pdf(data: bytes, session_no, session_name, filename) -> dict:
    try:
        from pypdf import PdfReader
    except ImportError as e:          # pragma: no cover — requirements.txt lists it
        raise ValueError("PDF decks need the pypdf package (pip install pypdf).") from e
    reader = PdfReader(io.BytesIO(data))
    slides = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""
        slides.append(_slide(i, text))
    if not any(s["title"] or s["body"] for s in slides):
        raise ValueError("No text could be read from this PDF — it looks like a scan or "
                         "an image-only export. Export the deck with text, or upload "
                         "the .pptx.")
    return _finish(slides, session_no, session_name, filename)


def _from_html(data: bytes, session_no, session_name, filename) -> dict:
    from lxml import html as lhtml
    doc = lhtml.fromstring(data.decode("utf-8", errors="replace"))
    for bad in doc.xpath("//script|//style|//noscript"):
        bad.getparent().remove(bad)
    nodes = doc.xpath("//section")
    if not nodes:
        nodes = doc.xpath("//*[contains(concat(' ', normalize-space(@class), ' '), ' slide')]")
    # Nested <section>s (a vertical stack inside a horizontal one) would count the
    # parent AND its children; keep only the innermost.
    nodes = [n for n in nodes if not any(c in nodes for c in n.iterdescendants())]
    slides = []
    if nodes:
        for i, n in enumerate(nodes, start=1):
            # Speaker notes (<aside>) are notes, not slide text — lifted out first.
            notes = ""
            for a in n.xpath(".//aside"):
                notes = "\n".join(t.strip() for t in a.itertext() if t.strip())
                a.getparent().remove(a)
            text = "\n".join(t.strip() for t in n.itertext() if t.strip())

            s = _slide(i, text)
            s["notes"] = notes
            slides.append(s)
    else:
        # No slide elements at all: cut the page at its headings.
        chunks, cur = [], []
        for el in doc.iter():
            if not isinstance(el.tag, str):
                continue
            if el.tag in ("h1", "h2", "h3") and cur:
                chunks.append("\n".join(cur)); cur = []
            t = (el.text or "").strip()
            if t:
                cur.append(t)
        if cur:
            chunks.append("\n".join(cur))
        slides = [_slide(i, c) for i, c in enumerate(chunks, start=1)]
    if not any(s["title"] or s["body"] for s in slides):
        raise ValueError("No slide text was found in this HTML file.")
    return _finish(slides, session_no, session_name, filename)


def extract_upload(filename: str, data: bytes, session_no: int | None,
                   session_name: str) -> tuple[str, dict]:
    """(content hash, deck record) for an uploaded deck file."""
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_BYTES:
        raise ValueError(f"That file is {len(data) / 1e6:.0f} MB; the limit is "
                         f"{MAX_BYTES // (1024 * 1024)} MB.")
    kind = _kind(filename)
    if kind not in ACCEPTED:
        raise ValueError(f"'{filename}' is not a deck the agent can read. Upload the Web "
                         f"Slides download as .pptx, .pdf or .html.")
    chash = hashlib.md5(data).hexdigest()
    if kind == ".pptx":
        if data[:2] != b"PK":
            raise ValueError("That .pptx is not a PowerPoint file (wrong signature).")
        deck = gslides.extract_from_bytes(data, session_no, session_name, "")
        deck["source_file"] = filename
        deck["source_kind"] = "web"
    elif kind == ".pdf":
        deck = _from_pdf(data, session_no, session_name, filename)
    else:
        deck = _from_html(data, session_no, session_name, filename)
    return chash, deck


def safe_name(filename: str) -> str:
    """The upload's own name, stripped to something a row and a manifest can hold."""
    base = PurePosixPath((filename or "").replace("\\", "/")).name
    return re.sub(r"[^\w .()\-\[\]]+", "_", base).strip() or "deck"
