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

WHAT THE WRITER KNOWS ABOUT A PICTURE (1.70). The writer is text-only. Until now it was
told the caption, and the caption of every screenshot on the deployed course was its
filename — "Screenshot from 2026-10-09 11-03-00", "Pasted image (6)" — so the model was
matching pictures to steps blind, and the reviewer saw the VPC screenshot under the
security-group step. Now each attached picture is READ ONCE by the vision-capable judge
model (`describe`): which screen it is, what state it shows, the labels and values
visible on it. That reading is stored on the row, briefed to the writer beside the
caption, used for the fallback matching here, and — after the hands-on chunk is drafted
— checked against the explanation the writer put under each picture
(`verify_placement`), so a mismatch is patched before the reviewer reads it.
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


def description_of(img: dict) -> str:
    """What the picture SHOWS, as read by the vision model. Empty until described."""
    return " ".join(str(img.get("description") or "").split())


def _looks_like_filename(caption: str) -> bool:
    """A caption nobody wrote: the file's own name ("Screenshot from 2026-10-09 11-03-00",
    "Pasted image (6)", "required 2", "IMG_2041")."""
    c = (caption or "").strip().lower()
    return (not c or bool(re.match(r"^(screenshot|screen shot|pasted image|image|img|photo|"
                                   r"capture|untitled|required|picture)\b", c))
            or bool(re.search(r"\d{4}-\d{2}-\d{2}", c)))


# --------------------------------------------------------------------------- #
# reading a picture — once, stored
# --------------------------------------------------------------------------- #
_DESCRIBE_SYSTEM = (
    "You read ONE screenshot attached to a teaching brief and say, in plain words, what "
    "it shows — so that a writer who cannot see it can put it at the right step of a "
    "practical exercise. Be literal and specific: name the tool or website and the exact "
    "screen/page (e.g. 'AWS console, EC2 > Target groups > s6-web-tg, Targets tab'), the "
    "state it shows (e.g. 'both targets Healthy', 'build finished with Successfully built'), "
    "and copy the key visible text: titles, menu items, button labels, resource names, "
    "commands, values, messages. Do not guess what is not visible; do not add teaching. "
    "Return ONLY JSON: {\"shows\": \"<one or two sentences: the screen and its state>\", "
    "\"visible_text\": \"<the key labels/names/values/commands visible, comma-separated>\", "
    "\"evidence_of\": \"<which action or step of a practical this picture is evidence "
    "of, in a few words>\"}"
)


def describe(image_row: dict, *, label: str = "describe_image") -> str:
    """Read one picture (a row WITH `data_b64`) and return its description line, e.g.
    "SHOWS: AWS console, VPC > Subnets, two public subnets listed … | VISIBLE: s5-public-subnet,
    s6-public-subnet-2, ap-south-1a, ap-south-1b | EVIDENCE OF: the second subnet created".
    Raises on a failed call; the caller decides whether that matters."""
    from . import config, llm
    m = config.harness()["model"]
    model = str(m.get("vision") or "").strip() or m["judge"]
    ctx = []
    if image_row.get("filename"):
        ctx.append(f"File name: {image_row['filename']}")
    cap = (image_row.get("caption") or "").strip()
    if cap and not _looks_like_filename(cap):
        ctx.append(f"The author captioned it: {cap}")
    if image_row.get("skill_text"):
        ctx.append("It is attached to this instruction: "
                   + " ".join(str(image_row["skill_text"]).split())[:300])
    raw = llm.complete_vision(
        _DESCRIBE_SYSTEM,
        ("Describe this screenshot.\n" + "\n".join(ctx)).strip(),
        [(image_row.get("mime") or "image/png", image_row["data_b64"])],
        model=model, max_tokens=600, temperature=0.0, label=label)
    try:
        data = llm.extract_json(raw)
    except Exception:
        data = {}
    shows = " ".join(str(data.get("shows") or "").split())
    vis = " ".join(str(data.get("visible_text") or "").split())
    ev = " ".join(str(data.get("evidence_of") or "").split())
    if not shows:
        shows = " ".join(str(raw or "").split())[:300]
    parts = [f"SHOWS: {shows[:400]}"]
    if vis:
        parts.append(f"VISIBLE: {vis[:300]}")
    if ev:
        parts.append(f"EVIDENCE OF: {ev[:120]}")
    return " | ".join(parts)


def ensure_described(images: list[dict], *, label: str = "describe_image") -> list[str]:
    """Give every image in `images` (metadata rows) a `description`, reading and storing
    the ones that have none. Mutates the rows; returns log notes. One vision call per
    undescribed picture, on the small model — a few cents for a whole skill's set — and
    never twice for the same picture. A failure is reported, not raised: the writer then
    works from the caption as before."""
    from . import config, db
    notes: list[str] = []
    if not (config.harness()["constraints"].get("images") or {}).get("describe", True):
        return notes
    done = failed = 0
    for im in images:
        if description_of(im) or im.get("id") is None:
            continue
        try:
            row = db.skill_image(int(im["id"]))
            if not row or not row.get("data_b64"):
                continue
            desc = describe({**row, "skill_text": im.get("skill_text")}, label=label)
            if not desc:
                continue
            db.set_skill_image_description(int(im["id"]), desc)
            im["description"] = desc
            done += 1
        except Exception as e:
            failed += 1
            notes.append(f"Screenshot {im.get('id')} could not be read ({str(e)[:120]}); "
                         f"the writer gets only its caption.")
    if done:
        notes.append(f"Read {done} attached screenshot(s) so the writer knows what each one "
                     f"shows (stored; not repeated next run).")
    return notes


def match_words(img: dict) -> set[str]:
    """The words a picture is matched to slides by: what it SHOWS first, its caption,
    and — only when nothing else is known — the skill line it hangs on (which is the
    same for every picture on that skill, so it cannot tell them apart)."""
    desc = re.sub(r"\b(SHOWS|VISIBLE|EVIDENCE OF):", " ", description_of(img))
    cap = caption_of(img)
    if desc.strip():
        return _words(desc, "" if _looks_like_filename(cap) else cap)
    if not _looks_like_filename(cap):
        return _words(cap)
    return _words(cap, img.get("skill_text"))


# --------------------------------------------------------------------------- #
# the prompt
# --------------------------------------------------------------------------- #
def brief(images: list[dict], compact: bool = False) -> list[str]:
    """The lines the writer reads about the attached images. Empty when none.

    Two things the first version did not say, and the reviewer noticed both: WHERE a
    picture belongs is decided by what it SHOWS (a console screenshot belongs at the
    hands-on step that uses that console, not on the concept slide that mentions the
    tool), and a picture that is not EXPLAINED is decoration — the slide has to walk the
    learner through it. And images cost no page budget, so they are never a reason to
    shorten the teaching text around them.
    """
    if not images:
        return []
    out = ["", "## ATTACHED SCREENSHOTS — placed ONCE, where they belong, and EXPLAINED"]
    if not compact:
        out += [
            "IN A RUN WITH A PRACTICAL HANDS-ON SECTION (the BUDGET block says whether this "
            "run has one) these screenshots belong to the practical, and the rules below "
            "are replaced by these: the theory sections place NO image; the hands-on "
            "section CHOOSES the genuine ones — as many as it takes to show each major part "
            "of the practical (the key screen of each major step, the final result) — and "
            "leaves out only duplicates and sub-step screens, because the "
            "instructor demos the practical in the recording and the step-by-step "
            "practical guide is shared after the session video. Each chosen image still "
            "gets a 30-50-word explaining `text` block.",
            "",
            "IN A RUN WITHOUT ONE:",
        ]
        out += [
            "The course owner attached these pictures to the brief. Each MUST appear in the "
            "document exactly once, as a content block on the slide it belongs to:",
            '    {"type": "image", "ref": <id>, "caption": "<what the learner is looking at>"}',
            "",
            "WHERE IT GOES — decided by what the picture SHOWS, not by where its words first "
            "appear:",
            "  · a screenshot of a tool, console, dashboard or command being used goes on the "
            "PRACTICAL HANDS-ON step that performs that action (after that step's text);",
            "  · a screenshot of a result, error or output goes where that result is "
            "explained — the expected-result slide of the hands-on, or the mechanism slide "
            "that produces it;",
            "  · a diagram or architecture picture goes on the overview / concept slide that "
            "introduces that structure, after the paragraph that names its parts;",
            "  · never on a slide that merely mentions the tool in passing, and never two "
            "images on one slide unless they are two steps of one action.",
            "",
            "HOW IT IS EXPLAINED — mandatory, gated. Immediately AFTER the image block, the "
            "same slide carries a `text` block of 40-90 words, in plain language, that walks "
            "the learner through the picture: which screen or tool this is, what each "
            "labelled or highlighted part means, what to click/type/read there, and what to "
            "notice (the value, the state, the message). Write it for someone who has never "
            "opened this tool. Then let `speaker_notes` say what to POINT AT while recording.",
            "",
            "WHAT IT DOES NOT CHANGE: the slide's own teaching text stays at full depth. "
            "Images are outside the page budget, so nothing is cut to make room for one; a "
            "slide with a screenshot is LONGER than one without, not shorter.",
            "Use the id EXACTLY as given below; never invent an id, never describe an image "
            "in `visual_guidance` instead of placing it, and reword the caption only so it "
            "says precisely what the picture shows.",
            "",
        ]
    out += [
        "",
        "MATCH A PICTURE BY WHAT IT SHOWS. Each line below says what is actually visible in "
        "the picture (SHOWS / VISIBLE / EVIDENCE OF — read from the image itself; the caption "
        "may be only its file name). Put a screenshot ONLY at the step or part that it is "
        "evidence of; a step whose screen is not among the pictures gets no picture, and a "
        "picture that matches no step is left out rather than forced in. Two pictures of "
        "the same screen in different states (before/after, empty/filled, unhealthy/healthy) "
        "go with the two steps that produce those states. Write the explaining text FROM the "
        "description: name what is visible (the names, values, messages), never something "
        "the picture does not show.",
    ]
    out += image_lines(images)
    return out


def image_lines(images: list[dict]) -> list[str]:
    """One line per attached picture: id, caption, what it shows."""
    out = []
    by_skill: dict = {}
    for im in images:
        by_skill.setdefault(im.get("skill_id"), []).append(im)
    for im in images:
        sibs = by_skill.get(im.get("skill_id")) or [im]
        order = f" (picture {sibs.index(im) + 1} of {len(sibs)} on this skill)" if len(sibs) > 1 else ""
        scope = f" (session {im['session_ref']} only)" if im.get("session_ref") else ""
        cap = caption_of(im)
        desc = description_of(im)
        line = f"- id {im['id']}{order}{scope}: caption “{cap}”"
        if desc:
            line += f" — {desc}"
        elif _looks_like_filename(cap):
            who = " ".join(str(im.get("skill_text") or "").split())
            line += (" — (not yet read; the caption is only the file name)"
                     + (f" attached to the rule: {who[:160]}" if who else ""))
        out.append(line)
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


def _is_hands_on_slide(doc: dict, slide: dict) -> bool:
    for sec in doc.get("sections") or []:
        if str(sec.get("kind") or "").lower() == "hands_on" and slide in (sec.get("slides") or []):
            return True
    return str(slide.get("role") or "") == "hands_on"


def _hands_on_cfg() -> dict:
    try:
        from . import config
        return dict(config.harness()["constraints"].get("hands_on") or {})
    except Exception:
        return {}


def _hands_on_section(doc: dict) -> dict | None:
    for sec in doc.get("sections") or []:
        if str(sec.get("kind") or "").lower() == "hands_on":
            return sec
    return None


def reconcile(doc: dict, images: list[dict]) -> list[str]:
    """Make the document hold the attached images once, and nothing invented. Returns
    the notes of what was done (for the run log).

    Two modes. In a document WITH a Practical Hands-On section (and
    constraints.hands_on.select_screenshots) the writer chose the genuine screenshots
    for the practical, so an unplaced one is LEFT OUT on purpose: the hands-on
    section's count is capped only if `max_images` is set (0 = no cap, the default), and if the writer placed none at all,
    the `min_images` best-matching one(s) go onto the hands-on slides. Otherwise every
    attached image is placed, as before.

    Runs on the ASSEMBLED document, before it is graded, because that is where a
    missing or invented image is visible. Idempotent: a document already reconciled
    comes back unchanged.
    """
    notes: list[str] = []
    ho_cfg = _hands_on_cfg()
    ho_sec = _hands_on_section(doc)
    selective = bool(ho_sec is not None and ho_cfg.get("select_screenshots", False))
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
    if selective:
        return notes + _reconcile_hands_on(ho_sec, missing, ho_cfg, seen)
    if not missing:
        return notes
    slides = list(_slides(doc))
    if not slides:
        return notes + [f"{len(missing)} attached image(s) could not be placed: the "
                        f"document has no slides."]
    for im in missing:
        want = match_words(im)
        best, best_key = slides[-1], (-1, 0)
        for s in slides:
            # Word overlap first; on a tie, a hands-on step beats a concept slide,
            # because an attached screenshot is almost always of the tool in use.
            key = (len(want & _slide_words(s)), 1 if _is_hands_on_slide(doc, s) else 0)
            if key > best_key:
                best, best_key = s, key
        best.setdefault("content", []).append(
            {"type": "image", "ref": int(im["id"]), "caption": caption_of(im)})
        notes.append(f"Placed attached image {im['id']} (“{caption_of(im)}”) on "
                     f"slide {best.get('n')}: the writer had not placed it.")
    return notes


def _reconcile_hands_on(sec: dict, unplaced: list[dict], cfg: dict,
                       seen: set[int]) -> list[str]:
    """Selective mode: cap the hands-on section's screenshots, and give it the best
    match when the writer placed none anywhere. Unchosen screenshots stay out."""
    notes: list[str] = []
    cap = int(cfg.get("max_images", 0) or 0)
    floor = int(cfg.get("min_images", 1) or 0)
    ho_slides = [s for s in sec.get("slides") or [] if isinstance(s, dict)]
    count = 0
    for s in ho_slides:
        kept = []
        for b in s.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "image":
                if cap and count >= cap:
                    notes.append(f"Dropped screenshot {b.get('ref')} on slide {s.get('n')}: "
                                 f"the hands-on shows at most {cap}.")
                    continue
                count += 1
            kept.append(b)
        s["content"] = kept
    if not seen and ho_slides and unplaced and floor:
        ranked = sorted(
            unplaced, key=lambda im: -max(len(match_words(im) & _slide_words(s))
                                          for s in ho_slides))
        for im in ranked[:floor]:
            want = match_words(im)
            best = max(ho_slides, key=lambda s: len(want & _slide_words(s)))
            best.setdefault("content", []).append(
                {"type": "image", "ref": int(im["id"]), "caption": caption_of(im)})
            notes.append(f"Placed screenshot {im['id']} (“{caption_of(im)}”) on hands-on "
                         f"slide {best.get('n')}: the writer had chosen none.")
    left = len(unplaced) - (min(floor, len(unplaced)) if not seen and ho_slides else 0)
    if left > 0:
        notes.append(f"{left} attached screenshot(s) left out of the TR doc on purpose: the "
                     f"hands-on shows only the genuine ones (the full set belongs to the "
                     f"practical guide).")
    return notes


# --------------------------------------------------------------------------- #
# after the draft — is each picture under the words that explain it?
# --------------------------------------------------------------------------- #
_VERIFY_SYSTEM = (
    "You check whether a screenshot in a teaching document sits at the right place. You "
    "see the picture, the heading of the slide it is on, and the text the writer put "
    "beside it to explain it. Decide whether that text describes THIS picture — the same "
    "screen, the same state, the same names and values — or a different one. Be strict "
    "about the state: a 'targets Healthy' explanation under a picture showing 'Unhealthy' "
    "is a mismatch; a 'security group inbound rules' explanation under a subnet list is a "
    "mismatch. Return ONLY JSON: {\"matches\": true|false, \"picture_shows\": \"<one "
    "sentence: what the picture actually shows>\", \"reason\": \"<one sentence>\"}"
)


def _explanation_for(slide: dict, idx: int) -> str:
    """The words that explain the image block at `idx`: the text block right after it,
    else the one right before, else the slide's other prose."""
    content = [b for b in (slide.get("content") or []) if isinstance(b, dict)]
    after = content[idx + 1] if idx + 1 < len(content) else None
    if after and after.get("type") == "text" and str(after.get("text") or "").strip():
        return str(after["text"]).strip()
    before = content[idx - 1] if idx > 0 else None
    if before and before.get("type") == "text" and str(before.get("text") or "").strip():
        return str(before["text"]).strip()
    return " ".join(str(b.get("text") or "") for b in content if b.get("type") == "text").strip()


def verify_placement(fragment_or_doc: dict, images: list[dict], *,
                     label: str = "image_check") -> dict:
    """Look at every placed picture and the explanation under it. Returns
    `{"checked": int, "mismatches": [{"ref", "slide_n", "explained_as", "picture_shows",
    "reason"}], "notes": [str]}`. One vision call per placed picture on the small model.
    A call that fails counts as not checked — never as a mismatch."""
    from . import config, db, llm
    out: dict = {"checked": 0, "mismatches": [], "notes": []}
    by_id = {int(im["id"]): im for im in images if im.get("id") is not None}
    sec = fragment_or_doc.get("section") if isinstance(fragment_or_doc, dict) else None
    slides = ([s for s in (sec.get("slides") or []) if isinstance(s, dict)] if isinstance(sec, dict)
              else list(_slides(fragment_or_doc)))
    m = config.harness()["model"]
    model = str(m.get("vision") or "").strip() or m["judge"]
    for s in slides:
        content = [b for b in (s.get("content") or []) if isinstance(b, dict)]
        for i, b in enumerate(content):
            if b.get("type") != "image":
                continue
            try:
                ref = int(b.get("ref"))
            except (TypeError, ValueError):
                continue
            if ref not in by_id:
                continue
            expl = _explanation_for(s, i)
            if not expl:
                continue
            try:
                row = db.skill_image(ref)
                if not row or not row.get("data_b64"):
                    continue
                raw = llm.complete_vision(
                    _VERIFY_SYSTEM,
                    (f"SLIDE HEADING: {s.get('heading') or s.get('title') or ''}\n"
                     f"CAPTION THE WRITER GAVE IT: {b.get('caption') or ''}\n"
                     f"THE TEXT EXPLAINING THIS PICTURE:\n{expl}\n\n"
                     f"Does that text describe this picture?"),
                    [(row.get("mime") or "image/png", row["data_b64"])],
                    model=model, max_tokens=400, temperature=0.0, label=label)
                data = llm.extract_json(raw)
            except Exception as e:
                out["notes"].append(f"Screenshot {ref}: placement not checked ({str(e)[:100]}).")
                continue
            out["checked"] += 1
            if data.get("matches") is False:
                out["mismatches"].append({
                    "ref": ref, "slide_n": s.get("n"),
                    "explained_as": " ".join(expl.split())[:240],
                    "picture_shows": " ".join(str(data.get("picture_shows") or "").split())[:240],
                    "reason": " ".join(str(data.get("reason") or "").split())[:200]})
    return out


def placement_fix_instruction(check: dict, images: list[dict]) -> str:
    """The patch reason for the mismatches `verify_placement` found: what each picture
    really shows, and the full list to choose the right one from."""
    parts = []
    for mm in check.get("mismatches") or []:
        parts.append(
            f"- image id {mm['ref']} on slide {mm['slide_n']}: the text beside it says "
            f"“{mm['explained_as'][:160]}…” but the picture actually shows: "
            f"{mm['picture_shows'] or 'something else'}"
            + (f" ({mm['reason']})" if mm.get("reason") else "") + ".")
    return ("PUT EACH SCREENSHOT WHERE IT BELONGS. These pictures sit beside text that "
            "describes a different screen:\n" + "\n".join(parts) + "\n"
            "For each one, EITHER move the image block to the step/part whose screen it "
            "actually shows and rewrite the 30-50-word text beside it from what is visible "
            "in the picture, OR — if a different attached picture is the one that shows the "
            "screen this text describes — swap in that picture's id, OR, if no step of this "
            "practical matches the picture, remove the image block and its explanation. "
            "Never leave a picture under words that describe another screen. The attached "
            "pictures and what each shows:\n" + "\n".join(image_lines(images)))


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
