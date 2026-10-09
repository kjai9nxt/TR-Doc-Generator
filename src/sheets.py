"""Google Sheets access + strict template validation.

The user provides a normal Google Sheets share link. We convert it to a CSV
export URL and read it (the sheet must be shared 'Anyone with the link ->
Viewer'). Column headers are validated against the templates in the harness:
matching is trimmed + case-insensitive, but the SET of columns must match
exactly — any missing or extra column DISCARDS the sheet.
"""
from __future__ import annotations
import csv
import io
import re
from dataclasses import dataclass

import requests

from . import config


class TemplateError(Exception):
    """Raised when a sheet does not match its required template."""
    def __init__(self, label: str, missing: list[str], extra: list[str], found: list[str]):
        self.label, self.missing, self.extra, self.found = label, missing, extra, found
        super().__init__(self._message())

    def _message(self) -> str:
        lines = [f"❌  The '{self.label}' sheet does not match the required template and was discarded."]
        if self.missing:
            lines.append(f"    Missing column(s): {', '.join(self.missing)}")
        if self.extra:
            lines.append(f"    Unexpected extra column(s): {', '.join(self.extra)}")
        lines.append(f"    Columns found: {', '.join(self.found) or '(none)'}")
        lines.append("    Please fix the sheet to match the template and re-enter the link.")
        lines.append(f"    See the template guide: {config.harness()['sheet_templates']['guide_file']}")
        return "\n".join(lines)


@dataclass
class SheetData:
    label: str
    headers: list[str]
    rows: list[dict]        # each row keyed by ORIGINAL header text
    csv_url: str


# --------------------------------------------------------------------------- #
# link -> CSV export URL
# --------------------------------------------------------------------------- #
_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_GID_RE = re.compile(r"[#&?]gid=(\d+)")


def to_csv_url(link: str) -> str:
    m = _ID_RE.search(link)
    if not m:
        raise ValueError(f"Not a valid Google Sheets link: {link}")
    sheet_id = m.group(1)
    gid_m = _GID_RE.search(link)
    gid = gid_m.group(1) if gid_m else "0"
    return f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid={gid}"


def _norm(col: str) -> str:
    return re.sub(r"\s+", " ", str(col).strip()).lower()


# --------------------------------------------------------------------------- #
# fetch + validate
# --------------------------------------------------------------------------- #
def fetch_raw(link: str, timeout: int = 30) -> tuple[list[str], list[dict], str]:
    url = to_csv_url(link)
    resp = requests.get(url, timeout=timeout)
    # Google serves UTF-8 CSV but often omits the charset, so requests guesses
    # latin-1 and mangles en-dashes/curly quotes. Force UTF-8 (tolerate a BOM).
    text = resp.content.decode("utf-8-sig", errors="replace")
    if resp.status_code != 200 or text.lstrip().lower().startswith("<!doctype html"):
        raise ValueError(
            f"Could not read the sheet (HTTP {resp.status_code}). "
            "Make sure it is shared as 'Anyone with the link -> Viewer'.")
    reader = csv.reader(io.StringIO(text))
    all_rows = [r for r in reader]
    if not all_rows:
        return [], [], url
    headers = [h.strip() for h in all_rows[0]]
    rows = []
    for raw in all_rows[1:]:
        if not any(cell.strip() for cell in raw):
            continue  # skip blank rows
        rows.append({headers[i]: (raw[i] if i < len(raw) else "") for i in range(len(headers))})
    return headers, rows, url


def deck_columns(template_key: str = "course_structure") -> dict[str, str]:
    """The deck column each teaching tool uses: {"google": "PPT Links", "web": "Web Slides"}.

    A sheet carries exactly ONE of them, and which one says how the course's decks
    arrive — a Google Slides link the agent fetches, or a Web Slides file the author
    downloads and uploads, in which case the cell holds the file's name (or nothing).
    """
    tpl = config.harness()["sheet_templates"][template_key]
    return {"google": tpl.get("ppt_link_column", "PPT Links"),
            "web": tpl.get("web_slides_column", "Web Slides")}


def deck_column_present(headers: list[str], template_key: str = "course_structure") -> str | None:
    """Which teaching tool's deck column this sheet has — "google", "web", or None."""
    found = {_norm(h) for h in headers if h.strip()}
    for mode, col in deck_columns(template_key).items():
        if _norm(col) in found:
            return mode
    return None


def validate(headers: list[str], template_key: str) -> None:
    tpl = config.harness()["sheet_templates"][template_key]
    required = list(tpl["required_columns"])
    label = tpl["label"]
    found_norm = {_norm(h) for h in headers if h.strip()}
    # THE DECK COLUMN IS ONE OF TWO. "PPT Links" for a course taught from Google Slides,
    # "Web Slides" for one taught from Web Slides. Exactly one must be present: with
    # neither there is no way to tell a recorded session from one still needing a doc,
    # and with both there is no way to tell which the author meant.
    cols = deck_columns(template_key)
    alternates = {_norm(c) for c in cols.values()}
    required = [c for c in required if _norm(c) not in alternates]
    req_norm = {_norm(c) for c in required} | alternates
    missing = [c for c in required if _norm(c) not in found_norm]
    present = [c for c in cols.values() if _norm(c) in found_norm]
    if not present:
        missing.append(" or ".join(f"'{c}'" for c in cols.values()))
    extra = [h for h in headers if h.strip() and _norm(h) not in req_norm]
    if len(present) > 1:
        extra.append(f"both {' and '.join(present)} — keep only the one this course uses")
    if missing or extra:
        raise TemplateError(label, missing, extra, headers)



def load_sheet(link: str, template_key: str) -> SheetData:
    """Fetch a sheet by link and validate it against the named template.
    Raises TemplateError (discard) or ValueError (unreadable)."""
    tpl = config.harness()["sheet_templates"][template_key]
    headers, rows, url = fetch_raw(link)
    validate(headers, template_key)
    return SheetData(label=tpl["label"], headers=headers, rows=rows, csv_url=url)


def guide_text() -> str:
    return config.read_text(config.harness()["sheet_templates"]["guide_file"])
