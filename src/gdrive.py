"""Upload a generated .docx to the signed-in user's Google Drive, CONVERTING it
to a native Google Doc, and return its link.

We reuse the .docx the pipeline already produced (so formatting is preserved) and
let Drive convert it — far simpler and higher-fidelity than rebuilding the doc via
the Docs API.

Auth model: the FRONTEND obtains a short-lived Drive access token via Google
Identity Services (scope drive.file) when the user clicks the button, and passes
it here. Because we upload with the USER's token, the file is created in the
USER's own Drive — they own it and are the only editor (which is exactly the
"edit access only for the logged-in user" requirement). No service account, no
Workspace-admin setup.

Only `requests` is used (already a dependency) — no Google SDK needed.
"""
from __future__ import annotations
import json
from pathlib import Path

import requests

_UPLOAD_URL = ("https://www.googleapis.com/upload/drive/v3/files"
               "?uploadType=multipart&fields=id,webViewLink,name")
_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_GDOC_MIME = "application/vnd.google-apps.document"


def upload_as_gdoc(docx_path: str | Path, name: str, access_token: str) -> dict:
    """Upload docx_path to the token owner's Drive as a Google Doc named `name`.
    Returns {"id", "webViewLink", "name"}. Raises RuntimeError on API failure."""
    return upload_as_gdoc_bytes(Path(docx_path).read_bytes(), name, access_token)


def upload_as_gdoc_bytes(docx_bytes: bytes, name: str, access_token: str) -> dict:
    """Same, from bytes already in hand.

    Needed because the .docx is not always on this instance's disk: an ephemeral host
    wipes the outputs directory on restart, so the copy may come back from the database
    instead (see src/outputs.py). Without this the recovered document could be
    downloaded but not turned into a Google Doc — half a fix.
    """
    if not access_token:
        raise RuntimeError("Missing Google Drive access token.")
    if not docx_bytes:
        raise RuntimeError("The generated document is empty.")
    meta = {"name": name, "mimeType": _GDOC_MIME}   # target type => Drive converts
    boundary = "tr-doc-generator-boundary-7f3a"
    body = b"".join([
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
        json.dumps(meta).encode(),
        f"\r\n--{boundary}\r\nContent-Type: {_DOCX_MIME}\r\n\r\n".encode(),
        docx_bytes,
        f"\r\n--{boundary}--\r\n".encode(),
    ])
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": f"multipart/related; boundary={boundary}",
    }
    r = requests.post(_UPLOAD_URL, headers=headers, data=body, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"Drive API HTTP {r.status_code}: {r.text[:300]}")
    return r.json()
