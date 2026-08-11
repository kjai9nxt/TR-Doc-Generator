"""Find the rendered document for a session or run — reliably.

THE BUG THIS EXISTS TO FIX. `/api/download/{n}` and `/api/gdoc/{n}` used to rebuild
the filename from scratch on every request:

    s = course_loader.get_session(session_no)          # the CURRENTLY-synced course
    path = outputs / f"Session {s.number} _ {s.name}.docx"

That is only correct while the curriculum sheet says exactly what it said when the doc
was generated. It frequently does not:

  * this is ONE shared instance with ONE active course, so another user pressing
    "Connect & Sync" with a different sheet renames every session under everyone's
    feet — the derived name stops matching the file on disk and the download 404s
    with "Generate the doc first" about a document that plainly exists;
  * a session renamed or re-numbered in the sheet does the same thing;
  * if the session number is missing from the newly-synced course, `get_session`
    raises `KeyError` and the endpoint returns a bare HTTP 500.

Measured against the current outputs directory, 5 of 9 generated documents were
already unreachable this way. A reviewer hit it after generating AND reviewing a full
doc: Download and Create-Google-Doc both failed, and she copied the document out of the
preview pane by hand.

Separately, the files live on the instance disk, which an ephemeral host wipes on
spin-down or redeploy — so even the right name can point at nothing.

THE FIX. Never re-derive. Resolve through the things that actually identify the output,
most authoritative first, and fall back to the DB copy when the disk has lost it:

    1. the run's recorded filename/path      (exact, written at render time)
    2. an explicit filename from the caller  (the UI already knows it)
    3. the newest "Session {n} _ *.docx" on disk
    4. the name derived from the synced course  (the old behaviour, last)
    5. the copy stored in the DB by run id, filename, or session number

Only step 4 can be wrong, and it now runs after everything that cannot be.
"""
from __future__ import annotations
import glob
import re
from dataclasses import dataclass
from pathlib import Path

from . import config


@dataclass
class Resolved:
    """A located document: either a real file, or bytes recovered from the DB."""
    filename: str
    path: Path | None = None
    data: bytes | None = None
    source: str = ""

    def read_bytes(self) -> bytes:
        if self.data is not None:
            return self.data
        return self.path.read_bytes()


def out_dir() -> Path:
    return config.DATA_ROOT / config.harness()["output"]["dir"]


def safe_name(name: str | None) -> str | None:
    """A caller-supplied filename, stripped to a bare basename.

    Path traversal defence: the value reaches us from a query parameter, and it is used
    to open a file inside the outputs directory. Anything with a separator, or that does
    not end in an expected extension, is rejected outright rather than sanitised.
    """
    if not name:
        return None
    base = Path(str(name)).name
    if base != str(name).strip():
        return None
    if not re.fullmatch(r"[^/\\]{1,200}\.(docx|md|json)", base):
        return None
    return base


def _derived_name(session_no: int) -> str | None:
    """The old behaviour, kept as a LAST resort: build the name from whatever course is
    synced right now. Wrong whenever the sheet changed since generation, which is the
    whole reason this module exists — but harmless once it runs after the exact lookups,
    and still useful for a doc generated before run ids were recorded."""
    try:
        from . import course_loader
        s = course_loader.get_session(session_no)
    except Exception:
        return None                        # KeyError here used to become an HTTP 500
    fmt = config.harness()["output"]["docx_filename"]
    return fmt.format(N=s.number, SessionName=s.name).replace("/", "-")


def _newest_on_disk(session_no: int, ext: str = "docx") -> Path | None:
    """Newest `Session {n} _ *.<ext>` in the outputs directory.

    The filename convention is stable even when session NAMES are not, so this finds
    the document whatever the sheet has been renamed to since. The number is matched
    with a boundary so session 1 does not match "Session 15 _ …".
    """
    hits = [Path(p) for p in glob.glob(str(out_dir() / f"Session {session_no} _ *.{ext}"))]
    hits = [p for p in hits if re.match(rf"^Session {session_no} _ ", p.name)]
    if not hits:
        return None
    return max(hits, key=lambda p: p.stat().st_mtime)


def resolve(session_no: int, *, run_id: str | None = None, filename: str | None = None,
            kind: str = "docx") -> Resolved | None:
    """Locate the rendered output. Returns None only when it genuinely does not exist
    anywhere — on disk or in the database."""
    ext = "docx" if kind == "docx" else kind
    directory = out_dir()
    candidates: list[tuple[str, Path]] = []

    # 1. what the run itself recorded.
    run = None
    if run_id:
        try:
            from . import db
            rows = db.runs(limit=1000)
            run = next((r for r in rows if r.get("id") == run_id), None)
        except Exception:
            run = None
    if run:
        for key in ("docx_name", "docx_path"):
            val = run.get(key)
            if not val:
                continue
            name = Path(str(val)).name
            if kind != "docx":
                name = name.rsplit(".", 1)[0] + f".{ext}"
            candidates.append((f"run.{key}", directory / name))

    # 2. the filename the caller passed (the UI has it in the run/result payload).
    explicit = safe_name(filename)
    if explicit:
        if kind != "docx" and not explicit.endswith(f".{ext}"):
            explicit = explicit.rsplit(".", 1)[0] + f".{ext}"
        candidates.append(("caller filename", directory / explicit))

    # 3. newest matching file on disk — immune to any renaming in the sheet.
    newest = _newest_on_disk(session_no, ext)
    if newest:
        candidates.append(("newest on disk", newest))

    # 4. the derived name, last, because it is the only guess that can be wrong.
    derived = _derived_name(session_no)
    if derived:
        if kind != "docx":
            derived = derived.rsplit(".", 1)[0] + f".{ext}"
        candidates.append(("derived from synced course", directory / derived))

    for source, path in candidates:
        try:
            if path.is_file() and path.parent.resolve() == directory.resolve():
                return Resolved(filename=path.name, path=path, source=source)
        except OSError:
            continue

    # 5. nothing on disk — recover the copy stored in the DB. This is what makes the
    #    feature survive an ephemeral host wiping the outputs directory mid-review.
    try:
        from . import db
        for got, source in (
                (db.run_file_get(run_id, kind) if run_id else None, "db by run id"),
                (db.run_file_find(explicit, kind) if explicit else None, "db by filename"),
                (db.run_file_find_by_session(session_no, kind), "db by session")):
            if got:
                name, data = got
                if kind != "docx" and not str(name).endswith(f".{ext}"):
                    name = str(name).rsplit(".", 1)[0] + f".{ext}"
                return Resolved(filename=name, data=data, source=source)
    except Exception:
        pass
    return None


def describe_attempts(session_no: int, *, run_id: str | None = None,
                      filename: str | None = None) -> str:
    """A human-readable account of what was searched, for the 404 message. A reviewer
    who has just spent an hour on a document deserves better than 'Generate the doc
    first' when the real problem is that the file is somewhere else."""
    bits = [f"outputs/Session {session_no} _ *.docx"]
    if run_id:
        bits.append(f"the record for run {run_id}")
    if safe_name(filename):
        bits.append(f"outputs/{safe_name(filename)}")
    derived = _derived_name(session_no)
    if derived:
        bits.append(f"outputs/{derived}")
    bits.append("the database copy of this run's output")
    return "; ".join(bits)


def persist(run_id: str | None, docx_path: str | Path) -> None:
    """Store this run's rendered outputs in the DB so a lost disk cannot lose them.
    Best effort: a generation that has already succeeded must never fail here."""
    if not run_id:
        return
    try:
        from . import db
        p = Path(docx_path)
        db.run_file_put(run_id, p, kind="docx")
        md = p.with_suffix(".md")
        if md.exists():
            db.run_file_put(run_id, md, kind="md")
    except Exception:
        pass
