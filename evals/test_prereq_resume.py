"""A long deck read that is INTERRUPTED — what survives it, and how it is finished.

    python -m evals.test_prereq_resume       # no API key, no network, ~2 seconds

WHY THIS EXISTS. An external prerequisite of 29 Google Slides links takes minutes to
read, and the deployed instance is a free one that sleeps, redeploys and can be killed
mid-request. That happened: the browser showed "8 of 29 deck(s) read, 343 slide(s) so
far", then HTTP 502 on the job poll, and afterwards the panel said

    Read from 1 course(s): 0 session(s), 0 slides, 0 distinct topics.

which was TRUE. Two separate faults produced it, and this suite covers both:

  1. A deck was written to the instance DISK, and the only thing that copied decks to the
     cloud DB was kb_backup() — called ONCE, after every link had been read. The
     prerequisite ROW commits before the first fetch. So an interruption at link 9 left
     the row attached and all nine decks on a disk that was wiped: a prerequisite with
     nothing behind it. Decks now mirror as they land.
  2. Sending the same list again answered 409, and the only way past it was to remove the
     prerequisite — which DELETES the decks already read. There was no way to finish a
     part-read import. It now resumes, skipping links whose deck is already stored.

Turso is simulated with a local libSQL file: real driver, real kb_files table, no
network and nothing near the deployed database.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_prereq_resume_")
os.environ["TR_DATA_DIR"] = TMP
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


from src import config, db, pptx_ingest, prereqs                    # noqa: E402

# A cloud backend, faked at the ONE predicate everything else asks — that predicate is
# what gates every mirror-to-DB path. The store underneath stays the ordinary local
# SQLite file (nothing here goes near a network), so kb_files behaves exactly as it does
# on the deployed instance while the test stays offline and disposable.
import sqlite3                                                      # noqa: E402


def _local_connect():
    db.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db.DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


db._use_turso = lambda: True
db._connect = _local_connect
db.init()

COURSE, PREREQ = "Responsive", "STATIC WEBSITE"
ALICE = "khushi@nxtwave.co.in"
LINKS = [f"https://docs.google.com/presentation/d/S{i}/edit" for i in range(1, 30)]


def fake_deck(n):
    return {"session_no": n, "deck_title": f"Static Web {n}", "n_slides": 12,
            "source_link": LINKS[n - 1],
            "slides": [{"n": i, "title": f"Topic {n}.{i}", "body": "text"}
                       for i in range(1, 13)]}


def kb_rows():
    return {r["path"] for r in db._query("SELECT path FROM kb_files")}


print("\n== a deck reaches the cloud DB the moment it is written ==")
pptx_ingest.put_deck(COURSE, 1, fake_deck(1), prereq=PREREQ)
rows = kb_rows()
deck_rows = {p for p in rows if p.startswith("decks/")}
check("the deck is in kb_files without any kb_backup() call",
      any(p.endswith("prereq/static_website_50c999/session_01.json") for p in deck_rows),
      str(sorted(deck_rows)))
check("…and so is its manifest, which is what says the deck is already read",
      any(p.endswith("prereq/static_website_50c999/manifest.json") for p in deck_rows),
      str(sorted(deck_rows)))

print("\n== an import cut off part-way keeps everything it had read ==")
db.add_prereq(COURSE, PREREQ, added_by=ALICE, kind="external")
for n in range(2, 10):                      # links 2..9 — where the real one was killed
    pptx_ingest.put_deck(COURSE, n, fake_deck(n), prereq=PREREQ)
mirrored = {p for p in kb_rows() if "prereq/static_website" in p and "session_" in p}
check("nine decks are mirrored, not nought", len(mirrored) == 9, str(len(mirrored)))

# THE INSTANCE DIES. Its disk goes with it; the DB does not.
import shutil                                                       # noqa: E402
shutil.rmtree(config.KB_DIR / "decks")
check("the disk really is wiped", not (config.KB_DIR / "decks").exists())
restored = db.kb_restore()
check("kb_restore brings the decks back", restored >= 9, str(restored))
check("…all nine of them",
      pptx_ingest.deck_session_numbers(COURSE, prereq=PREREQ) == set(range(1, 10)),
      str(sorted(pptx_ingest.deck_session_numbers(COURSE, prereq=PREREQ))))
report = prereqs.coverage_report(COURSE)
check("…and the panel now reports them instead of zero",
      report["sessions_indexed"] == 9 and report["slides_indexed"] == 108,
      f"{report['sessions_indexed']} session(s), {report['slides_indexed']} slide(s)")
check("…with the topics they carry assumed knowledge",
      report["topics_indexed"] == 108, str(report["topics_indexed"]))

print("\n== sending the same list again finishes it, and refetches nothing ==")
import server                                                       # noqa: E402

fetched = []


class _FakeSlides:
    @staticmethod
    def content_hash(link):
        fetched.append(link)
        return "h" + link[-6:], b"pptx-bytes"

    @staticmethod
    def extract_from_bytes(data, n, name, link):
        return dict(fake_deck(n), source_link=link)


server.gslides = _FakeSlides
job_id = "resumecheck01"
server.JOBS[job_id] = {"status": "running", "logs": [], "result": None, "error": None,
                       "error_kind": None, "progress": {}}
server._run_prereq_ingest(job_id, COURSE, PREREQ, LINKS)
job = server.JOBS[job_id]
check("the job finishes", job["status"] == "done", str(job.get("error")))
check("all 29 decks are read", job["result"]["decks"] == 29, str(job["result"]))
check("…but only the 20 missing ones were fetched — the nine already read were skipped",
      len(fetched) == 20, f"fetched {len(fetched)}")
check("…and none of the skipped links was fetched",
      not (set(fetched) & set(LINKS[:9])), str(sorted(set(fetched) & set(LINKS[:9]))))
check("the slide count covers all of them, kept ones included",
      job["result"]["slides"] == 29 * 12, str(job["result"]["slides"]))
check("the coverage report agrees",
      prereqs.coverage_report(COURSE)["sessions_indexed"] == 29,
      str(prereqs.coverage_report(COURSE)["sessions_indexed"]))

print("\n== a link whose deck was read from a DIFFERENT source is re-read ==")
moved = list(LINKS)
moved[4] = "https://docs.google.com/presentation/d/REPLACED/edit"
fetched.clear()
server.JOBS[job_id] = {"status": "running", "logs": [], "result": None, "error": None,
                       "error_kind": None, "progress": {}}
server._run_prereq_ingest(job_id, COURSE, PREREQ, moved)
check("exactly the changed link is fetched again", fetched == [moved[4]], str(fetched))
check("…and the stored deck now points at it",
      pptx_ingest.get_deck(COURSE, 5, prereq=PREREQ)["source_link"] == moved[4],
      str(pptx_ingest.get_deck(COURSE, 5, prereq=PREREQ)["source_link"]))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
