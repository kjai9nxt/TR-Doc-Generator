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
  .html / .htm   three shapes, tried in order:
                   · a BUILT WEB SLIDES DECK (a Vite/React bundle: `<div id="root">` plus one
                     big script) — the slides are JavaScript objects inside the script
                     ({id, section, label, element: …, notes: …}); `_from_bundle` reads
                     them straight out of the source, since no HTML exists until it runs;
                   · one slide per <section> (or per <div class="slide…">);
                   · the page's headings when there are no slide elements.

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


# --------------------------------------------------------------------------- #
# Built Web Slides decks (a JS bundle)
# --------------------------------------------------------------------------- #
# The download of a Web Slides deck is NOT a page of slides. It is the deck's
# single-file build: a `<div id="root">`, the fonts and images inlined as data URIs, and
# one minified module script in which the slides are an array of objects —
#     {id:"cc-nist",section:"Cloud Computing Basics",label:"NIST formal view",
#      element:s.jsx(ye,{kicker:"…",title:"NIST Formal View",lead:"…",points:["…"],
#                        children:s.jsx(d8,{title:"NIST Definition",children:"…"})}),
#      notes:`…`}
# — so a DOM reader finds nothing (the page is empty until React runs) and fell back to
# "cut at headings", which read the whole deck as ONE slide. The slide text is still
# all there, as string literals under meaningful keys, which is what this reads.
#
# It is a tokenizer, not a JavaScript parser: it walks the array entry by entry
# (tracking strings, template literals and bracket depth) and, inside each entry,
# records every string literal with the key it sits under. Keys that are plainly
# styling or identity (className, icon, id, src…) are dropped; everything else is slide
# text, in source order, which is also reading order on the slide.

_SKIP_KEYS = {
    "id", "key", "classname", "class", "icon", "src", "href", "kind", "variant",
    "tone", "color", "colour", "style", "transition", "family", "font", "align",
    "size", "width", "height", "lesson", "fill", "stroke", "d", "viewbox",
    "strokewidth", "strokelinecap", "strokelinejoin", "xmlns", "type", "marks",
    "mark", "image", "img", "alt", "role", "aria-hidden", "aria-label", "lang",
    "mode", "layout", "theme", "accent", "bg", "background", "as", "lit",
}
# The first argument of a JSX call is the element's tag when it is a string —
# s.jsx("div", …) — and a tag is not slide text.
_HTML_TAGS = {
    "div", "span", "p", "svg", "path", "g", "rect", "circle", "ellipse", "line",
    "polyline", "polygon", "ul", "ol", "li", "section", "article", "header", "footer",
    "main", "nav", "img", "br", "hr", "strong", "em", "b", "i", "u", "small", "h1", "h2",
    "h3", "h4", "h5", "h6", "table", "thead", "tbody", "tr", "td", "th", "figure",
    "figcaption", "button", "a", "code", "pre", "blockquote", "sup", "sub", "label",
    "input", "form", "iframe", "video", "audio", "source", "defs", "lineargradient",
    "stop", "clippath", "mask", "use", "text", "tspan", "foreignobject",
}
_TITLE_KEYS = ("title",)
_SUBTITLE_KEYS = ("kicker", "subtitle", "sub", "eyebrow")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")


def _skip_string(s: str, i: int) -> int:
    """`i` is on the opening quote; returns the index just past the closing one."""
    q = s[i]
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == q:
            return i + 1
        i += 1
    return n


def _skip_template(s: str, i: int) -> int:
    """`i` is on the opening backtick. `${…}` may nest braces and strings."""
    i += 1
    n = len(s)
    while i < n:
        c = s[i]
        if c == "\\":
            i += 2
            continue
        if c == "`":
            return i + 1
        if c == "$" and i + 1 < n and s[i + 1] == "{":
            i = _skip_balanced(s, i + 1)
            continue
        i += 1
    return n


def _skip_balanced(s: str, i: int) -> int:
    """`i` is on an opening bracket; returns the index just past its match."""
    pairs = {"{": "}", "[": "]", "(": ")"}
    close = pairs[s[i]]
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c in "\"'":
            i = _skip_string(s, i)
            continue
        if c == "`":
            i = _skip_template(s, i)
            continue
        if c in pairs:
            depth += 1
        elif c in "}])":
            depth -= 1
            if depth == 0 and c == close:
                return i + 1
        i += 1
    return n


def _unescape(raw: str) -> str:
    """The value of a JS string literal's body."""
    def one(m):
        e = m.group(1)
        if e == "n":
            return "\n"
        if e == "t":
            return " "
        if e[0] == "u" and len(e) == 5:
            try:
                return chr(int(e[1:], 16))
            except ValueError:
                return ""
        if e[0] == "x" and len(e) == 3:
            try:
                return chr(int(e[1:], 16))
            except ValueError:
                return ""
        return e
    return re.sub(r"\\(u[0-9A-Fa-f]{4}|x[0-9A-Fa-f]{2}|.)", one, raw)


def _literal_value(s: str, i: int, j: int) -> str:
    """The text of the literal at s[i:j] (quotes or backticks included)."""
    body = s[i + 1:j - 1]
    if s[i] == "`":
        # Drop the `${…}` expressions — they are code, not slide text.
        out, k, n = [], 0, len(body)
        while k < n:
            if body[k] == "$" and k + 1 < n and body[k + 1] == "{":
                k = _skip_balanced(body, k + 1)
                continue
            out.append(body[k])
            k += 1
        body = "".join(out)
    return _unescape(body)


def _array_entries(s: str, start: int) -> tuple[list[str], int]:
    """The top-level entries of the array whose `[` is at `start`, and the index of
    its `]`."""
    i = start + 1
    n = len(s)
    depth = 0
    entries: list[str] = []
    cur = i
    while i < n:
        c = s[i]
        if c in "\"'":
            i = _skip_string(s, i)
            continue
        if c == "`":
            i = _skip_template(s, i)
            continue
        if c in "[{(":
            depth += 1
        elif c in "]})":
            if depth == 0:
                entries.append(s[cur:i])
                return entries, i
            depth -= 1
        elif c == "," and depth == 0:
            entries.append(s[cur:i])
            cur = i + 1
        i += 1
    return entries, n


def _keyed_strings(entry: str) -> list[tuple[str | None, str, int]]:
    """Every string literal in one entry as (key, value, depth), in source order.

    The key is the identifier the literal sits under: `title:"…"` gives "title", and a
    literal inside `points:[…]` or `panels:[{…}]` inherits the nearest enclosing key
    ("points", then "label"/"body" for the inner object's own pairs). A string that is
    itself a key (`"aria-label":…`) is skipped.
    """
    out: list[tuple[str | None, str, int]] = []
    stack: list[str | None] = []
    pending: str | None = None         # identifier just seen, if followed by ':'
    i, n = 0, len(entry)
    while i < n:
        c = entry[i]
        if c in "\"'`":
            j = _skip_template(entry, i) if c == "`" else _skip_string(entry, i)
            # A quoted KEY: the literal is followed by ':' (not '::' — not JS).
            k = j
            while k < n and entry[k] in " \t\r\n":
                k += 1
            if k < n and entry[k] == ":":
                pending = entry[i + 1:j - 1].lower()
                i = k + 1
                continue
            key = pending if pending is not None else (stack[-1] if stack else None)
            out.append((key, _literal_value(entry, i, j), len(stack)))
            pending = None
            i = j
            continue
        if c in "[{(":
            stack.append(pending)
            pending = None
            i += 1
            continue
        if c in "]})":
            if stack:
                stack.pop()
            pending = None
            i += 1
            continue
        m = _IDENT.match(entry, i)
        if m:
            k = m.end()
            while k < n and entry[k] in " \t\r\n":
                k += 1
            pending = m.group().lower() if (k < n and entry[k] == ":") else None
            i = m.end()
            continue
        if c == ",":
            pending = None
        i += 1
    return out


def _bundle_slide(n: int, entry: str) -> dict | None:
    pairs = _keyed_strings(entry)
    section = label = notes = ""
    title = ""
    lines: list[str] = []
    for key, val, depth in pairs:
        val = " ".join(val.split())
        if not val:
            continue
        # The entry is an object, so its own pairs sit at depth 1.
        if depth <= 1:
            if key == "section":
                section = val
            elif key == "label":
                label = val
            elif key == "notes":
                notes = val
            elif key == "id":
                pass
            continue
        if key in _SKIP_KEYS or val.startswith("data:") or val.startswith("#"):
            continue
        if key is None and val.lower() in _HTML_TAGS:
            continue
        if key in _TITLE_KEYS and not title:
            title = val[:80]
            continue
        if key in _SUBTITLE_KEYS:
            if val.lower() in ("agenda for today's session",) or val == title:
                continue
        if len(val) < 2 and not val.isdigit():
            continue
        if lines and lines[-1] == val:
            continue
        lines.append(val)
    if not (title or label or lines or notes):
        return None
    # A generic element title ("Analogy") under a specific label ("Analogy: a furnished
    # apartment") — the label is the better name. A divider's name is its section.
    if label and title and label.lower().startswith(title.lower()) and len(label) > len(title):
        title = label[:80]
    if not title and label.lower().startswith("section divider") and section:
        title = f"Section: {section}"
    return {"n": n, "title": title or label or section, "body": "\n".join(lines),
            "notes": notes, "tables": [], "section": section, "label": label}


def _from_bundle(text: str) -> list[dict]:
    """The slides of a built Web Slides deck, or [] when `text` is not one.

    Several arrays may start with `[{id:"…"` (a toolbar's icon list looks the same from
    the outside), so every candidate is read and the one whose entries carry `element:`
    — the deck itself — wins. Ties go to the longest.
    """
    best: list[dict] = []
    for m in re.finditer(r'\[\{id:"', text):
        entries, _ = _array_entries(text, m.start())
        if len(entries) < 2 or sum(1 for e in entries if "element:" in e) < len(entries) // 2:
            continue
        slides = [s for s in (_bundle_slide(i, e) for i, e in enumerate(entries, 1)) if s]
        # Renumber after dropping any entry that carried nothing readable.
        for i, s in enumerate(slides, 1):
            s["n"] = i
        if len(slides) > len(best):
            best = slides
    return best


def _from_html(data: bytes, session_no, session_name, filename) -> dict:
    text = data.decode("utf-8", errors="replace")
    # A built deck first: the DOM of one is an empty root, so lxml would find nothing
    # and the heading fallback would read the whole file as a single slide.
    bundle = _from_bundle(text)
    if len(bundle) >= 2:
        deck = _finish(bundle, session_no, session_name, filename)
        # The page's <title> is the deck's name; the first slide is a brand frame.
        m = re.search(r"<title>\s*(.*?)\s*</title>", text, re.I | re.S)
        if m and m.group(1).strip():
            deck["deck_title"] = " ".join(m.group(1).split())[:120]
            deck["summary"] = deck["deck_title"] + "\n" + deck["summary"].split("\n", 1)[-1]
        return deck
    from lxml import html as lhtml
    doc = lhtml.fromstring(text)
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
