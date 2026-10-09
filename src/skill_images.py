"""Screenshots attached to a skill, and how they end up INSIDE the TR doc.

An author sometimes has the exact picture a session should show — a screenshot of the
tool, a console, a diagram they drew — and a sentence of visual guidance is a poor
substitute for it. So a skill can carry images. They travel with the skill: a session
skill's images reach only that session, a course skill's reach every session, and
nothing reaches the writer until the skill is approved, like every other line of the
brief.

HOW THEY GET INTO THE DOCUMENT. The writer is told which images exist (id + caption)
and asked to place each one, once, as a content block
    {"type": "image", "ref": <id>, "caption": "..."}
on the slide that discusses it. Models forget, so `reconcile` runs on the assembled
document before it is graded: a block naming an image that does not exist is dropped,
a duplicate placement is dropped, and an image the writer never placed is appended to
the slide whose words best match its caption — so an attached screenshot is in the
document whatever the model did. The renderers (docx_writer) then draw the block as the
picture plus its caption.

Stored in the database as base64 text, down-scaled to a sane width first, so they
survive an ephemeral disk and a 512 MB host alike.
"""
from __future__ import annotations
import base64
import io
import re
import secrets

MAX_UPLOAD = 12 * 1024 * 1024
MAX_WIDTH = 1600
ACCEPTED_MIME = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                 "image/gif": ".gif"}


def normalise(data: bytes, filename: str) -> tuple[bytes, str, int, int]:
    """(bytes, mime, width, height) — re-encoded as PNG or JPEG, scaled to MAX_WIDTH.

    Pillow does the sniffing, so a .png that is really a JPEG is still read, and a
    file that is not an image at all is refused with its name.
    """
    if not data:
        raise ValueError("The uploaded file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"'{filename}' is {len(data) / 1e6:.0f} MB; the limit is "
                         f"{MAX_UPLOAD // (1024 * 1024)} MB per image.")
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(data))
        im.load()
    except Exception:
        raise ValueError(f"'{filename}' is not an image the agent can read (PNG, JPEG, "
                         f"WebP or GIF).")
    fmt = (im.format or "").upper()
    if im.width > MAX_WIDTH:
        im = im.resize((MAX_WIDTH, max(1, round(im.height * MAX_WIDTH / im.width))))
    out = io.BytesIO()
    if fmt == "JPEG" or (im.mode == "RGB" and fmt not in ("PNG", "GIF")):
        im.convert("RGB").save(out, "JPEG", quality=88, optimize=True)
        mime = "image/jpeg"
    else:
        # Screenshots are mostly flat colour and text: PNG keeps them crisp.
        if im.mode not in ("RGB", "RGBA", "P", "L"):
            im = im.convert("RGBA")
        im.save(out, "PNG", optimize=True)
        mime = "image/png"
    return out.getvalue(), mime, im.width, im.height


def new_key() -> str:
    """The unguessable handle an image is served under (see /api/skill-images)."""
    return secrets.token_hex(12)


def encode(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def decode(b64: str) -> bytes:
    return base64.b64decode(b64 or "")


def caption_of(img: dict) -> str:
    return (img.get("caption") or "").strip() or (img.get("filename") or "screenshot")


# --------------------------------------------------------------------------- #
# the prompt
# --------------------------------------------------------------------------- #
def brief(images: list[dict], compact: bool = False) -> list[str]:
    """The lines the writer reads about the attached images. Empty when none."""
    if not images:
        return []
    out = ["", "## ATTACHED SCREENSHOTS — every one of these MUST appear in the document"]
    if not compact:
        out += [
            "The course owner attached these images to the brief. Place EACH ONE, "
            "EXACTLY ONCE, as a content block on the slide whose content it illustrates:",
            '    {"type": "image", "ref": <id>, "caption": "<what the learner is looking at>"}',
            "The block goes in that slide's `content` list, at the point where the text "
            "refers to it — after the paragraph or bullets that introduce what it shows. "
            "Use the id EXACTLY as given below; never invent an id, and never describe an "
            "image in `visual_guidance` instead of placing it. A caption may be reworded "
            "to fit the slide, but it must say what the picture shows.",
            "",
        ]
    for im in images:
        who = (im.get("skill_text") or "").strip()
        scope = f" (session {im['session_ref']} only)" if im.get("session_ref") else ""
        out.append(f"- id {im['id']}: “{caption_of(im)}”{scope}"
                   + (f" — attached to: {who[:120]}" if who else ""))
    return out


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z0-9]{3,}")


def _words(*texts) -> set[str]:
    out: set[str] = set()
    for t in texts:
        out |= set(_WORD.findall(str(t or "").lower()))
    return out


def _slide_words(slide: dict) -> set[str]:
    parts = [slide.get("title"), slide.get("heading"), slide.get("subheading"),
             slide.get("visual_guidance"), slide.get("speaker_notes")]
    for b in slide.get("content") or []:
        if not isinstance(b, dict):
            continue
        parts.append(b.get("text"))
        parts += list(b.get("items") or [])
    return _words(*parts)


def _slides(doc: dict):
    for sec in doc.get("sections") or []:
        for s in sec.get("slides") or []:
            if isinstance(s, dict):
                yield s


def reconcile(doc: dict, images: list[dict]) -> list[str]:
    """Make the document hold every attached image once, and nothing else. Returns the
    notes of what was done (for the run log).

    Runs on the ASSEMBLED document, before it is graded, because that is where a
    missing or invented image is visible. Idempotent: a document already reconciled
    comes back unchanged.
    """
    notes: list[str] = []
    by_id = {int(im["id"]): im for im in images if im.get("id") is not None}
    seen: set[int] = set()
    for s in _slides(doc):
        kept = []
        for b in s.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "image":
                try:
                    ref = int(b.get("ref"))
                except (TypeError, ValueError):
                    ref = None
                if ref not in by_id:
                    notes.append(f"Dropped an image block naming unknown image "
                                 f"{b.get('ref')!r} on slide {s.get('n')}.")
                    continue
                if ref in seen:
                    notes.append(f"Dropped a second placement of image {ref} on slide "
                                 f"{s.get('n')}.")
                    continue
                seen.add(ref)
                if not str(b.get("caption") or "").strip():
                    b["caption"] = caption_of(by_id[ref])
            kept.append(b)
        if "content" in s or kept:
            s["content"] = kept
    missing = [im for i, im in by_id.items() if i not in seen]
    if not missing:
        return notes
    slides = list(_slides(doc))
    if not slides:
        return notes + [f"{len(missing)} attached image(s) could not be placed: the "
                        f"document has no slides."]
    for im in missing:
        want = _words(caption_of(im), im.get("skill_text"))
        best, best_n = slides[-1], -1
        for s in slides:
            n = len(want & _slide_words(s))
            if n > best_n:
                best, best_n = s, n
        best.setdefault("content", []).append(
            {"type": "image", "ref": int(im["id"]), "caption": caption_of(im)})
        notes.append(f"Placed attached image {im['id']} (“{caption_of(im)}”) on "
                     f"slide {best.get('n')}: the writer had not placed it.")
    return notes


def placed_refs(doc: dict) -> list[int]:
    out = []
    for s in _slides(doc):
        for b in s.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "image":
                try:
                    out.append(int(b.get("ref")))
                except (TypeError, ValueError):
                    pass
    return out
