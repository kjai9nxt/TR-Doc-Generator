"""Google Slides access. A value in the curriculum sheet's 'PPT Links' column is a
Google Slides link. We export it as .pptx via Google's export endpoint (the deck must
be link-viewable) and hand it to the existing pptx extractor — so one extraction
code path serves both local .pptx files and Google Slides links.
"""
from __future__ import annotations
import hashlib
import io
import re
import zipfile
from pathlib import Path

import requests

from . import pptx_ingest

_ID_RE = re.compile(r"/presentation/d/([a-zA-Z0-9-_]+)")

# 1-byte stand-in written in place of each stripped media blob.
_MEDIA_PLACEHOLDER = b"\x00"


def _strip_media(data: bytes) -> bytes:
    """Return a copy of the .pptx (a zip) with every ``ppt/media/*`` blob replaced
    by a 1-byte placeholder.

    A deck's TEXT — titles, bodies, tables, speaker notes — lives entirely in the
    XML parts, which we leave untouched, so extraction is byte-for-byte identical.
    Only the image/video/audio binaries are dropped, and ``extract_deck`` never
    reads those. An image-heavy deck shrinks from tens of MB to a few KB, which is
    what lets a 512 MB host (Render free) parse it without running out of memory.

    Entry names, extensions and every other part are preserved, so all
    relationships + [Content_Types].xml stay valid and python-pptx opens the file
    cleanly. If the zip is malformed or anything unexpected happens, the ORIGINAL
    bytes are returned unchanged — correctness always wins over the memory saving.
    """
    try:
        src = io.BytesIO(data)
        out = io.BytesIO()
        with zipfile.ZipFile(src) as zin, \
                zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                if item.filename.startswith("ppt/media/"):
                    zout.writestr(item.filename, _MEDIA_PLACEHOLDER)
                else:
                    zout.writestr(item, zin.read(item.filename))
        return out.getvalue()
    except Exception:
        return data


def presentation_id(link: str) -> str:
    m = _ID_RE.search(link)
    if not m:
        raise ValueError(f"Not a valid Google Slides link: {link}")
    return m.group(1)


def export_pptx_url(link: str) -> str:
    return f"https://docs.google.com/presentation/d/{presentation_id(link)}/export/pptx"


def content_hash(link: str, timeout=(10, 40)) -> tuple[str, bytes]:
    """Download the exported .pptx and return (md5, bytes). The md5 captures the
    deck's CURRENT content, so any edit to the slides changes the hash and
    triggers re-ingestion on the next sync. timeout=(connect, read) so an
    unshared/unreachable deck fails fast instead of hanging the whole sync."""
    try:
        resp = requests.get(export_pptx_url(link), timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise ValueError(
            "Google did not answer in time while exporting this deck. A very large deck "
            "can take longer than the limit; try it on its own, or split it.") from e
    except requests.exceptions.RequestException as e:
        raise ValueError(f"Could not reach Google to export this deck: {e}") from e
    # SAY WHICH FAILURE IT WAS. One message for every status told the person to check
    # sharing even when sharing was fine and the link was simply wrong, or the deck had
    # been deleted — and there is nothing to act on in "it did not work".
    if resp.status_code in (401, 403):
        raise ValueError(
            "This deck is not readable by the agent (HTTP %d). Open it in Google Slides "
            "and set Share -> General access to 'Anyone with the link', role Viewer."
            % resp.status_code)
    if resp.status_code == 404:
        raise ValueError(
            "No Google Slides deck exists at that link (HTTP 404). Check the id — a "
            "Google DOC or SHEET link will also land here, and only Slides can be "
            "exported.")
    if resp.status_code == 429:
        raise ValueError(
            "Google is rate-limiting the export (HTTP 429). Wait a minute and add the "
            "remaining links again — the decks already read are kept.")
    if resp.status_code != 200:
        raise ValueError(f"Google refused to export this deck (HTTP {resp.status_code}).")
    if resp.content[:2] != b"PK":
        # 200 with an HTML body: Google served a sign-in or interstitial page instead of
        # the file, which is what a restricted deck looks like when it does not 403.
        raise ValueError(
            "Google returned a web page instead of the deck, which means it is not "
            "publicly readable. Set Share -> 'Anyone with the link' -> Viewer.")
    data = resp.content
    return hashlib.md5(data).hexdigest(), data


def extract_from_bytes(data: bytes, session_no: int | None, session_name: str,
                       link: str) -> dict:
    """Extract an already-downloaded .pptx (bytes) into a KB deck record.
    Uses a per-session temp filename so parallel extraction can't clash."""
    pptx_ingest.KB_DIR.mkdir(parents=True, exist_ok=True)
    tag = session_no if session_no is not None else abs(hash(link)) % 100000
    tmp = pptx_ingest.KB_DIR / f"_tmp_{tag}.pptx"
    # Drop image blobs BEFORE parsing — the text is unaffected but the file (and
    # so python-pptx's in-memory footprint) shrinks enough to fit a 512 MB host.
    # The content hash was already taken upstream on the ORIGINAL bytes, so edit
    # detection is unaffected.
    tmp.write_bytes(_strip_media(data))
    try:
        deck = pptx_ingest.extract_deck(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    # session number/name come from the sheets (authoritative), not the filename
    deck["session_no"] = session_no
    deck["source_file"] = session_name
    deck["source_link"] = link
    if not deck.get("deck_title"):
        deck["deck_title"] = session_name
    return deck


def extract_from_link(link: str, session_no: int | None, session_name: str) -> dict:
    """Download + extract a Google Slides deck into a KB deck record."""
    _, data = content_hash(link)
    return extract_from_bytes(data, session_no, session_name, link)
