"""PPTX ingestion into a persistent, incremental knowledge base.

The user stores past course decks as .pptx in inputs/past_ppts/. We extract
each deck ONCE into knowledge_base/decks/<key>.json and record a hash in
knowledge_base/manifest.json. On every later run we only (re)process decks
whose file hash changed or that are new — already-ingested decks are kept as
they are and never re-extracted. Nothing from the past is dropped.

Each deck record holds:
  - a structural summary (deck title + per-slide titles)  -> always injected
  - full per-slide text + notes + tables (chunks)         -> for RAG retrieval

No API is needed for ingestion; it is pure text extraction, so the memory is
built and persisted regardless of whether the generation key is set.
"""
from __future__ import annotations
import glob
import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

from pptx import Presentation

from . import config

KB_DIR = config.ROOT / "knowledge_base"
DECKS_DIR = KB_DIR / "decks"
MANIFEST = KB_DIR / "manifest.json"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _session_no(path: Path) -> int | None:
    m = re.search(r"(\d+)", path.stem)
    return int(m.group(1)) if m else None


def _deck_key(path: Path) -> str:
    n = _session_no(path)
    return f"session_{n:02d}" if n is not None else re.sub(r"\W+", "_", path.stem.lower())


def _shape_text(shape) -> str:
    if not shape.has_text_frame:
        return ""
    return "\n".join(p.text for p in shape.text_frame.paragraphs if p.text.strip())


def _slide_notes(slide) -> str:
    if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
        return slide.notes_slide.notes_text_frame.text.strip()
    return ""


def _slide_tables(shape) -> list[list[list[str]]]:
    tables = []
    if shape.has_table:
        t = shape.table
        rows = [[cell.text.strip() for cell in row.cells] for row in t.rows]
        tables.append(rows)
    return tables


# --------------------------------------------------------------------------- #
# extraction
# --------------------------------------------------------------------------- #
def extract_deck(path: Path) -> dict:
    prs = Presentation(str(path))
    slides = []
    for i, slide in enumerate(prs.slides, start=1):
        title = ""
        body_parts, tables = [], []
        for shape in slide.shapes:
            if shape.has_table:
                tables += _slide_tables(shape)
                continue
            txt = _shape_text(shape)
            if not txt:
                continue
            # first placeholder-ish text becomes the title
            is_title = getattr(shape, "is_placeholder", False) and \
                getattr(shape.placeholder_format, "idx", None) == 0
            if is_title and not title:
                title = txt.split("\n")[0].strip()
            else:
                body_parts.append(txt)
        if not title and body_parts:
            title = body_parts[0].split("\n")[0][:80]
        slides.append({
            "n": i,
            "title": title,
            "body": "\n".join(body_parts).strip(),
            "notes": _slide_notes(slide),
            "tables": tables,
        })

    deck_title = slides[0]["title"] if slides else path.stem
    summary_lines = [f"    - Slide {s['n']}: {s['title']}" for s in slides if s["title"]]
    summary = f"{deck_title}\n" + "\n".join(summary_lines)

    return {
        "session_no": _session_no(path),
        "source_file": path.name,
        "deck_title": deck_title,
        "n_slides": len(slides),
        "summary": summary,
        "slides": slides,
    }


# --------------------------------------------------------------------------- #
# persistent KB
# --------------------------------------------------------------------------- #
def _load_manifest() -> dict:
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {}


def _save_manifest(m: dict):
    KB_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(m, indent=2), encoding="utf-8")


@dataclass
class IngestReport:
    ingested: list[str]
    skipped: list[str]
    total_decks: int


def ingest(verbose: bool = True) -> IngestReport:
    """Incrementally sync inputs/past_ppts/ into the knowledge base."""
    DECKS_DIR.mkdir(parents=True, exist_ok=True)
    pattern = config.harness()["context"]["past_ppts_glob"]
    paths = sorted(Path(p) for p in glob.glob(str(config.ROOT / pattern)))
    manifest = _load_manifest()

    ingested, skipped = [], []
    for path in paths:
        key = _deck_key(path)
        fhash = _file_hash(path)
        rec = manifest.get(key)
        deck_json = DECKS_DIR / f"{key}.json"
        if rec and rec.get("hash") == fhash and deck_json.exists():
            skipped.append(path.name)   # already in memory, unchanged
            continue
        deck = extract_deck(path)
        deck_json.write_text(json.dumps(deck, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest[key] = {
            "hash": fhash,
            "source_file": path.name,
            "session_no": deck["session_no"],
            "n_slides": deck["n_slides"],
        }
        ingested.append(path.name)

    _save_manifest(manifest)
    if verbose:
        print(f"[KB] ingested {len(ingested)} new/changed deck(s), "
              f"skipped {len(skipped)} cached, {len(manifest)} total in memory.")
    return IngestReport(ingested, skipped, len(manifest))


def load_all_decks() -> list[dict]:
    decks = []
    for f in sorted(DECKS_DIR.glob("*.json")):
        decks.append(json.loads(f.read_text()))
    decks.sort(key=lambda d: (d.get("session_no") is None, d.get("session_no") or 0))
    return decks


def decks_before(session_no: int) -> list[dict]:
    return [d for d in load_all_decks()
            if d.get("session_no") is not None and d["session_no"] < session_no]


# --------------------------------------------------------------------------- #
# extraction-completeness measure (guideline 2/3: decks must be FULLY extracted)
# --------------------------------------------------------------------------- #
def _source_slide_count(deck: dict) -> int | None:
    """Best-effort ground-truth slide count from the source .pptx, if it is still
    on disk (offline decks in inputs/past_ppts/). Synced decks are extracted from
    in-memory bytes and not kept, so this returns None for them."""
    src = deck.get("source_file")
    if not src:
        return None
    try:
        pat = config.harness()["context"]["past_ppts_glob"]
        base = (config.ROOT / pat).parent
        p = base / src
        if p.exists():
            return len(Presentation(str(p)).slides)
    except Exception:
        pass
    return None


def deck_completeness(deck: dict) -> dict:
    """Deterministic per-deck extraction health from the stored KB JSON.
    A slide with no title AND no body AND no table is treated as 'empty' — a
    likely extraction gap (or a genuinely blank slide)."""
    slides = deck.get("slides", [])
    n = len(slides)
    empty = [s.get("n") for s in slides
             if not (s.get("title") or s.get("body") or s.get("tables"))]
    # Cover (slide 1) and the last two slides are conventionally design/closing
    # slides with little text — an empty one there is NOT an extraction failure.
    # Only INTERIOR empty slides signal genuinely missed content.
    edge = {1, n, n - 1}
    interior_empty = [x for x in empty if x not in edge]
    with_body = sum(1 for s in slides if s.get("body"))
    with_notes = sum(1 for s in slides if s.get("notes"))
    with_tables = sum(1 for s in slides if s.get("tables"))
    src = _source_slide_count(deck)
    dropped = (src - n) if (src is not None) else None
    coverage = round(with_body / n, 3) if n else 0.0

    issues = []
    if n == 0:
        issues.append("no slides extracted")
    if interior_empty:
        issues.append(f"{len(interior_empty)} interior slide(s) with no title/body/table: "
                      f"{interior_empty}")
    if dropped and dropped > 0:
        issues.append(f"extracted {n} of {src} source slides ({dropped} dropped)")

    return {
        "session_no": deck.get("session_no"),
        "source_file": deck.get("source_file"),
        "n_slides": n,
        "source_slides": src,
        "empty_slides": empty,
        "interior_empty_slides": interior_empty,
        "with_body": with_body,
        "with_notes": with_notes,
        "with_tables": with_tables,
        "body_coverage": coverage,
        "ok": not issues,
        "issues": issues,
    }


def completeness_report() -> dict:
    """Extraction health across ALL ingested decks."""
    decks = load_all_decks()
    per = [deck_completeness(d) for d in decks]
    problems = [p for p in per if not p["ok"]]
    return {
        "ok": not problems,
        "decks_checked": len(per),
        "decks_with_issues": len(problems),
        "decks": per,
    }


# --------------------------------------------------------------------------- #
# BM25 lexical RAG retrieval over stored chunks (no API, offline, no deps)
# --------------------------------------------------------------------------- #
_WORD = re.compile(r"[a-z0-9]+")
# Very common words carry no topical signal — dropping them sharpens BM25's IDF.
_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "for", "on",
         "with", "as", "by", "it", "this", "that", "be", "we", "you", "how", "what",
         "why", "when", "which", "at", "from", "into", "can", "will", "its", "so"}

_K1 = 1.5
_B = 0.75


def _tokens(text: str) -> set[str]:
    return set(_WORD.findall(text.lower()))


def _tok_list(text: str) -> list[str]:
    return [t for t in _WORD.findall(text.lower()) if t not in _STOP]


def retrieve(query: str, session_no: int, top_k: int = 6) -> list[dict]:
    """Return the most query-relevant prior slides (across decks < session_no).

    BM25 ranking (Okapi, k1=1.5, b=0.75): rewards rare/distinctive query terms
    (IDF), saturates repeated matches, and normalises by slide length — far
    stronger relevance than raw token overlap. Deterministic, offline, no deps.
    Complements (does not replace) the always-injected per-deck summaries.
    """
    q_terms = [t for t in _tok_list(query)]
    if not q_terms:
        return []

    # Build the corpus of candidate prior slides.
    docs = []  # (session_no, slide, tokens)
    for deck in decks_before(session_no):
        for s in deck["slides"]:
            blob = " ".join([s.get("title", ""), s.get("body", ""), s.get("notes", "")])
            toks = _tok_list(blob)
            if toks:
                docs.append((deck["session_no"], s, toks))
    if not docs:
        return []

    N = len(docs)
    avgdl = sum(len(t) for _, _, t in docs) / N
    df: dict[str, int] = {}
    for _, _, toks in docs:
        for term in set(toks):
            df[term] = df.get(term, 0) + 1

    q_set = set(q_terms)
    scored = []
    for sn, s, toks in docs:
        dl = len(toks)
        tf: dict[str, int] = {}
        for t in toks:
            if t in q_set:
                tf[t] = tf.get(t, 0) + 1
        if not tf:
            continue
        score = 0.0
        for term, f in tf.items():
            n_q = df.get(term, 0)
            idf = math.log(1 + (N - n_q + 0.5) / (n_q + 0.5))
            score += idf * (f * (_K1 + 1)) / (f + _K1 * (1 - _B + _B * dl / avgdl))
        scored.append((score, sn, s))

    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for score, sn, s in scored[:top_k]:
        out.append({"session_no": sn, "slide": s["n"], "title": s["title"],
                    "excerpt": (s["body"] or s["notes"])[:400], "score": round(score, 3)})
    return out
