"""Offline regression for output delivery: download, Google Doc, and recovery.

    python -m evals.test_outputs        # no API key needed

WHY THIS EXISTS. A reviewer finished generating AND reviewing a TR doc, then found that
"Download Word" and "Create Google Doc" both failed, and copied the whole document out
of the preview pane by hand. The cause was not the file: both endpoints rebuilt the
filename from the CURRENTLY-synced curriculum on every request, so any re-sync, rename
or course switch between generating and downloading made the derived name miss the file
that was sitting on disk — 5 of the 9 existing outputs were unreachable that way — and a
session number absent from the new course raised KeyError, i.e. a bare HTTP 500.

These checks pin the fix (src/outputs.py) against exactly that state: the outputs
directory holds documents from an EARLIER course while a different one is synced.
"""
from __future__ import annotations
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["AUTH_DISABLED"] = "1"          # local-dev bypass; this test never deploys

import uvicorn                              # noqa: E402
from src import config, db, outputs         # noqa: E402
from server import app                      # noqa: E402

PORT = 8096
BASE = f"http://127.0.0.1:{PORT}"
OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


def get(path):
    """(status, decoded Content-Disposition, body length or error text)."""
    try:
        r = urllib.request.urlopen(BASE + path)
        cd = urllib.parse.unquote(r.headers.get("Content-Disposition") or "")
        return r.status, cd, len(r.read())
    except urllib.error.HTTPError as e:
        return e.code, "", e.read().decode()[:250]


def _any_existing_output():
    """Pick a generated .docx to test against, preferring one whose session number the
    SYNCED course names differently — the mismatch the old code could not survive.

    Only sessions with exactly ONE output file are eligible. A session number with two
    documents on disk (the same number in two different courses, which this outputs
    directory has) is genuinely ambiguous without a run id: the resolver returns the
    NEWEST, which is correct behaviour but makes 'it served the right file' an
    unanswerable assertion. So the ambiguity is excluded from the fixture rather than
    asserted against.
    """
    fmt = config.harness()["output"]["docx_filename"]
    by_session: dict[int, list[Path]] = {}
    for p in sorted(outputs.out_dir().glob("Session * _ *.docx")):
        try:
            n = int(p.name.split("_")[0].replace("Session", "").strip())
        except ValueError:
            continue
        by_session.setdefault(n, []).append(p)

    unique = {n: ps[0] for n, ps in by_session.items() if len(ps) == 1}
    fallback = None
    for n, p in sorted(unique.items()):
        fallback = fallback or (n, p)
        try:
            from src import course_loader
            s = course_loader.get_session(n)
            derived = fmt.format(N=s.number, SessionName=s.name).replace("/", "-")
        except Exception:
            return n, p                     # not in the synced course at all — ideal
        if derived != p.name:
            return n, p                     # renamed since generation — the real case
    return fallback


def _provision_fixture():
    """Render the golden into outputs/ so this test has something to resolve.

    `outputs/` is gitignored, so on CI (and any fresh clone) the directory is EMPTY and
    the whole test used to skip — passing while exercising nothing, which is worse than
    no test because the run still shows green. Returns (session_no, path, created) so
    the caller can delete a fixture it made and leave a real output alone.
    """
    from src import docx_writer
    golden = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())
    # A session number no real course uses, so it can never collide with a genuine doc.
    session_no = 9101
    golden["session_no"] = session_no
    out = outputs.out_dir()
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"Session {session_no} _ Output Delivery Fixture.docx"
    docx_writer.write_docx(golden, path)
    docx_writer.write_markdown(golden, path.with_suffix(".md"))
    return session_no, path, True


def main():
    threading.Thread(
        target=lambda: uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error"),
        daemon=True).start()
    time.sleep(4)

    target = _any_existing_output()
    created = False
    if target is None:
        session_no, path, created = _provision_fixture()
        print(f"  (no real output present — rendered a fixture: {path.name})")
    else:
        session_no, path = target
    try:
        return _run(session_no, path)
    finally:
        if created:
            for p in (path, path.with_suffix(".md")):
                try:
                    p.unlink(missing_ok=True)
                except OSError:
                    pass


def _run(session_no, path):
    print(f"\n== a doc on disk whose session the synced course names differently ==")
    print(f"   {path.name}  (session {session_no})")
    st, cd, body = get(f"/api/download/{session_no}")
    check("download by session number succeeds", st == 200, f"-> {st} {body}")
    check("it serves the file that actually exists", path.stem in cd, cd)

    print("\n== a session number missing from the synced course ==")
    st, cd, body = get("/api/download/99999")
    check("returns 404, not the old HTTP 500", st == 404, f"-> {st}")
    check("the 404 says what was searched", "Searched" in str(body), str(body)[:140])

    print("\n== explicit identifiers, and traversal safety ==")
    st, cd, _ = get(f"/api/download/{session_no}?name=" + urllib.parse.quote(path.name))
    check("an exact filename resolves", st == 200 and path.stem in cd, f"-> {st}")
    st, cd, _ = get(f"/api/download/{session_no}?name=" + urllib.parse.quote("../../.env"))
    check("a traversal attempt never escapes outputs/", st == 200 and path.stem in cd,
          f"-> {st} {cd}")

    print("\n== the markdown escape hatch (survives a page reload) ==")
    st, cd, body = get(f"/api/preview/{session_no}")
    check("preview returns the full markdown", st == 200 and body > 500, f"-> {st} {body}")

    print("\n== recovery when the instance disk has lost the file ==")
    run_id = "test_" + uuid.uuid4().hex[:8]
    db.create_run(run_id, user_email="test@nxtwave.co.in", course="test", team_id=None,
                  session_no=session_no, title="test", enforce_time=True)
    db.finish_run(run_id, status="done", accepted=True, rubric=93.0, est_minutes=35.0,
                  est_pages=9, rounds=1, slides=9, docx_path=str(path))
    outputs.persist(run_id, path)
    got = db.run_file_get(run_id, "docx")
    check("the .docx is stored against the run", got is not None and len(got[1]) > 5000)
    hidden = path.with_suffix(".docx.hidden-by-test")
    path.rename(hidden)
    try:
        st, cd, body = get(f"/api/download/{session_no}?run_id={run_id}")
        check("download still works with the file gone from disk", st == 200, f"-> {st} {body}")
        check("the recovered copy keeps its filename", path.stem in cd, cd)
    finally:
        hidden.rename(path)

    print("\n== page count reaches the dashboards ==")
    row = next((r for r in db.runs(limit=200) if r["id"] == run_id), None)
    check("the run row carries est_pages", row and row.get("est_pages") == 9,
          str(row and row.get("est_pages")))
    check("the run row carries docx_name",
          row and str(row.get("docx_name") or "").endswith(".docx"),
          str(row and row.get("docx_name")))
    s = db.summary()
    check("the summary reports the page ceiling", s.get("page_limit") == 16, str(s.get("page_limit")))
    check("the summary reports length stats",
          {"avg_pages", "max_pages_seen", "over_page_limit"} <= set(s))
    st, _, _ = get("/api/admin/runs")
    check("the admin runs endpoint responds", st == 200)

    # Remove the synthetic run so it never pollutes real analytics.
    import sqlite3
    conn = sqlite3.connect(config.KB_DIR / "tr_app.db")
    conn.execute("DELETE FROM runs WHERE id=?", (run_id,))
    conn.execute("DELETE FROM run_files WHERE run_id=?", (run_id,))
    conn.commit()
    conn.close()

    print(f"\n{OK} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
