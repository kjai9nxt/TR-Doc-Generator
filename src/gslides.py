"""Google Slides access. A 'PPT Link' in the Session Details sheet is a Google
Slides link. We export it as .pptx via Google's export endpoint (the deck must
be link-viewable) and hand it to the existing pptx extractor — so one extraction
code path serves both local .pptx files and Google Slides links.
"""
from __future__ import annotations
import hashlib
import re
from pathlib import Path

import requests

from . import pptx_ingest

_ID_RE = re.compile(r"/presentation/d/([a-zA-Z0-9-_]+)")


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
    resp = requests.get(export_pptx_url(link), timeout=timeout)
    if resp.status_code != 200 or resp.content[:2] != b"PK":
        raise ValueError(
            f"Could not export the Google Slides deck (HTTP {resp.status_code}). "
            "Make sure it is shared as 'Anyone with the link -> Viewer'.")
    data = resp.content
    return hashlib.md5(data).hexdigest(), data


def extract_from_bytes(data: bytes, session_no: int | None, session_name: str,
                       link: str) -> dict:
    """Extract an already-downloaded .pptx (bytes) into a KB deck record.
    Uses a per-session temp filename so parallel extraction can't clash."""
    pptx_ingest.KB_DIR.mkdir(parents=True, exist_ok=True)
    tag = session_no if session_no is not None else abs(hash(link)) % 100000
    tmp = pptx_ingest.KB_DIR / f"_tmp_{tag}.pptx"
    tmp.write_bytes(data)
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
