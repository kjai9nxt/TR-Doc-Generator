"""Offline regression for curriculum RENUMBERING: insert, delete, and the deck mirror.

    python -m evals.test_curriculum_order        # no API key, no cloud DB needed

WHY THIS EXISTS. Two separate bugs shipped in this one feature, and neither was caught
by anything:

  1. The insert button gave a new row the next FREE number instead of the position's
     number, so inserting at the top of a 34-session course produced "Session 35" above
     Session 1. A curriculum is an ORDERED list — the row you put first IS session 1.

  2. The fix for (1) renumbered correctly but did it one row at a time, holding a single
     interactive transaction open across one round trip PER SESSION. Against Turso the
     stream backing that transaction is reclaimed mid-walk and the insert dies with
     `status=404 Not Found, body={"error":"stream not found: …"}`. kb_rename_decks had
     the same shape, three statements per deck, and failed SILENTLY — its caller only
     prints — so the cloud mirror quietly stopped following the curriculum.

So this pins both properties: the numbers that come out, and the fact that the work is
done in a FIXED, small number of statements no matter how long the course is. The
second half is the one that matters against a cloud database, and it is invisible to a
test that only checks the resulting numbers.
"""
from __future__ import annotations
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import db                           # noqa: E402

OK = FAIL = 0
C = "RenumberTest"


def check(label, cond, detail=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}" + (f"  ({detail})" if detail else ""))


def reset(n=34):
    db._exec("DELETE FROM curriculum WHERE course=?", (C,))
    for i in range(1, n + 1):
        db.curriculum_upsert(C, i, topic="", session_name=f"S{i}",
                             key_takeaways=[], ppt_link="")


def rows():
    return sorted(db.curriculum(C), key=lambda r: r["session_no"])


def nums():
    return [r["session_no"] for r in rows()]


def names():
    return [r["session_name"] for r in rows()]


def deck(n):
    return f"decks/session_{int(n):02d}.json"


def put_decks(paths):
    db._exec("DELETE FROM kb_files")
    for p in paths:
        db._exec("INSERT INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
                 (p, f"content-of-{p}", "t0"))


def deck_state():
    return {r["path"]: r["content"] for r in db._query("SELECT path, content FROM kb_files")}


def isolate() -> Path:
    """Point EVERY piece of state this test touches at a throwaway directory.

    Not optional, and not just the database. These checks drive the real endpoints, and
    a curriculum endpoint does far more than write a table: it renumbers extracted decks
    on disk, rewrites course_structure.json and manifest.json, and — on delete — prunes
    every deck the curriculum no longer links. Run against the real knowledge base with
    a synthetic 35-session course that has no deck links, that prune is indiscriminate:
    it deletes the whole extracted course. It did, the first time this file was run.

    The module-level paths are resolved at import, so redirecting config alone is not
    enough — each one has to be re-pointed by name.
    """
    tmp = Path(tempfile.mkdtemp(prefix="curriculum_order_"))
    kb = tmp / "knowledge_base"
    (kb / "decks").mkdir(parents=True, exist_ok=True)

    from src import config, pptx_ingest, sync
    config.KB_DIR = kb
    pptx_ingest.KB_DIR = kb
    pptx_ingest.DECKS_DIR = kb / "decks"
    pptx_ingest.MANIFEST = kb / "manifest.json"
    sync.KB = kb
    sync.COURSE_CACHE = kb / "course_structure.json"
    db.DB_PATH = kb / "renumber_test.db"
    return kb


def main() -> int:
    kb = isolate()
    db.init()

    # Prove the isolation before anything is allowed to run: if the real knowledge base
    # is still reachable from here, the checks below would eat it.
    real_kb = Path(__file__).resolve().parent.parent / "knowledge_base"
    from src import config, pptx_ingest, sync
    for name, got in (("config.KB_DIR", config.KB_DIR),
                      ("pptx_ingest.DECKS_DIR", pptx_ingest.DECKS_DIR),
                      ("pptx_ingest.MANIFEST", pptx_ingest.MANIFEST),
                      ("sync.COURSE_CACHE", sync.COURSE_CACHE),
                      ("db.DB_PATH", db.DB_PATH)):
        assert real_kb not in Path(got).parents and Path(got) != real_kb, \
            f"{name} still points inside the real knowledge base ({got})"
    print(f"(isolated: {kb})\n")

    print("== insert takes the POSITION's number, not the next free one ==")
    reset()
    mapping = db.curriculum_shift_from(C, 1, by=1)
    db.curriculum_upsert(C, 1, topic="", session_name="Brand new",
                         key_takeaways=[], ppt_link="")
    check("a course of 34 becomes 1..35 with no gaps", nums() == list(range(1, 36)))
    check("the new row is session 1, not 35", names()[0] == "Brand new")
    check("the old session 1 moved to 2", names()[1] == "S1")
    check("every shifted row is reported back", mapping == {i: i + 1 for i in range(1, 35)})

    reset()
    db.curriculum_shift_from(C, 10, by=1)
    db.curriculum_upsert(C, 10, topic="", session_name="Mid new",
                         key_takeaways=[], ppt_link="")
    check("inserting at 10 leaves 1..9 alone", names()[:9] == [f"S{i}" for i in range(1, 10)])
    check("…puts the new row at 10", names()[9] == "Mid new")
    check("…and pushes the old 10 to 11", names()[10] == "S10")

    print("\n== delete CLOSES the gap ==")
    reset()
    db.curriculum_delete(C, 5)
    db.curriculum_shift_from(C, 6, by=-1)
    check("34 sessions become a contiguous 1..33", nums() == list(range(1, 34)))
    check("the old session 6 is now 5", names()[4] == "S6")
    reset()
    db.curriculum_delete(C, 1)
    db.curriculum_shift_from(C, 2, by=-1)
    check("deleting session 1 still lands on 1 (no 0, no negative)",
          nums() == list(range(1, 34)) and names()[0] == "S2")

    print("\n== a hand-edited, non-contiguous course keeps its shape ==")
    db._exec("DELETE FROM curriculum WHERE course=?", (C,))
    for i in (1, 2, 5, 9):
        db.curriculum_upsert(C, i, topic="", session_name=f"S{i}",
                             key_takeaways=[], ppt_link="")
    db.curriculum_shift_from(C, 5, by=1)
    check("1,2,5,9 shifted from 5 becomes 1,2,6,10", nums() == [1, 2, 6, 10])
    check("each row kept its own content", names() == ["S1", "S2", "S5", "S9"])

    print("\n== no-ops and other courses ==")
    reset(3)
    check("by=0 changes nothing", db.curriculum_shift_from(C, 1, by=0) == {} and nums() == [1, 2, 3])
    check("a position past the end changes nothing",
          db.curriculum_shift_from(C, 99, by=1) == {} and nums() == [1, 2, 3])
    reset(5)
    for i in range(1, 6):
        db.curriculum_upsert("OtherCourse", i, topic="", session_name=f"O{i}",
                             key_takeaways=[], ppt_link="")
    db.curriculum_shift_from(C, 1, by=1)
    check("a shift touches ONE course only",
          sorted(r["session_no"] for r in db.curriculum("OtherCourse")) == [1, 2, 3, 4, 5])

    print("\n== the deck mirror follows, including the chained-collision case ==")
    put_decks([deck(3), deck(4), deck(5)])
    moved = db.kb_rename_decks({3: 4, 4: 5, 5: 6})
    st = deck_state()
    check("a 3->4, 4->5, 5->6 chain does not lose a row", moved == 3 and sorted(st) ==
          [deck(4), deck(5), deck(6)])
    check("content travels with the number", st[deck(4)] == f"content-of-{deck(3)}")
    check("no __moving__ placeholder is left behind",
          not any("__moving__" in p for p in st))
    put_decks([deck(3), deck(4)])
    db.kb_rename_decks({3: 4})
    st = deck_state()
    check("an orphan on the destination is replaced, not duplicated",
          sorted(st) == [deck(4)] and st[deck(4)] == f"content-of-{deck(3)}")
    put_decks([deck(3), "course_structure.json"])
    db.kb_rename_decks({3: 4})
    check("non-deck KB files are untouched",
          deck_state().get("course_structure.json") == "content-of-course_structure.json")

    print("\n== the work is a FIXED number of statements, not one per session ==")
    # The property that broke against Turso. Counted by wrapping the module's own
    # connection factory, so it measures what actually reaches the database.
    class Counting:
        def __init__(self, conn):
            self._conn, self.n = conn, 0

        def cursor(self):
            outer = self

            class Cur:
                def __init__(self, c):
                    self._c = c

                def execute(self, sql, *a):
                    outer.n += 1
                    return self._c.execute(sql, *a)

                def __getattr__(self, k):
                    return getattr(self._c, k)

            return Cur(self._conn.cursor())

        def __getattr__(self, k):
            return getattr(self._conn, k)

    real_connect = db._connect
    seen = []

    def counting_connect():
        c = Counting(real_connect())
        seen.append(c)
        return c

    for size in (5, 34, 120):
        reset(size)
        seen.clear()
        db._connect = counting_connect
        try:
            db.curriculum_shift_from(C, 1, by=1)
        finally:
            db._connect = real_connect
        held = max((c.n for c in seen), default=0)
        check(f"shifting a {size}-session course sends {held} statement(s), not {size}",
              held <= 3, f"{held} statements on one connection")

    for size in (5, 34, 120):
        put_decks([deck(i) for i in range(1, size + 1)])
        seen.clear()
        db._connect = counting_connect
        try:
            db.kb_rename_decks({i: i + 1 for i in range(1, size + 1)})
        finally:
            db._connect = real_connect
        held = max((c.n for c in seen), default=0)
        check(f"renaming {size} decks sends {held} statement(s), not {3 * size}",
              held <= 4, f"{held} statements on one connection")

    print("\n== the Generate dropdown follows the curriculum ==")
    # The third bug in this feature. The dashboard table and the dropdown are two views
    # of one curriculum, and only the table was being refreshed by insert and delete —
    # so a session removed from the curriculum stayed in the dropdown, and picking it
    # started a run against a session that no longer existed. Both views now come back
    # in the same reply, which is what these pin.
    import os
    os.environ["AUTH_DISABLED"] = "1"
    from server import (insert_curriculum_row, delete_curriculum_row,   # noqa: E402
                        save_curriculum, CurriculumInsertBody,
                        CurriculumSaveBody, CurriculumRow)
    user = {"email": "regression@test", "is_admin": True}

    reset(35)
    d = delete_curriculum_row(35, C, user)
    check("a delete reply carries the dropdown at all", "sessions" in d)
    check("the deleted session is gone from the TABLE",
          35 not in [r["session_no"] for r in d["rows"]])
    check("…and from the DROPDOWN",
          35 not in [s["number"] for s in d["sessions"]])
    check("the two views agree",
          sorted(s["number"] for s in d["sessions"]) ==
          sorted(r["session_no"] for r in d["rows"]))

    reset(34)
    d = insert_curriculum_row(CurriculumInsertBody(at_session_no=1, course=C), None, user)
    check("an insert reply carries the dropdown too", "sessions" in d)
    check("the dropdown shows 1..35", sorted(s["number"] for s in d["sessions"]) ==
          list(range(1, 36)))

    reset(5)
    d = save_curriculum(CurriculumSaveBody(course=C, rows=[
        CurriculumRow(session_no=3, session_name="Renamed", topic="",
                      key_takeaways=["A: b"], ppt_link=None)]), None, user)
    check("a save reply carries the dropdown", "sessions" in d)
    check("a rename reaches the dropdown",
          next(s["name"] for s in d["sessions"] if s["number"] == 3) == "Renamed")

    # The rule the dropdown is FOR: a session whose deck is already recorded does not
    # need a TR doc, so it is not offered. Pinned so the fix above cannot quietly turn
    # the dropdown into a plain copy of the table.
    reset(4)
    db.curriculum_upsert(C, 2, topic="", session_name="S2", key_takeaways=["A: b"],
                         ppt_link="https://docs.google.com/presentation/d/x")
    d = save_curriculum(CurriculumSaveBody(course=C, rows=[]), None, user)
    check("a session with a deck link stays IN the table",
          2 in [r["session_no"] for r in d["rows"]])
    check("…and OUT of the dropdown", 2 not in [s["number"] for s in d["sessions"]])

    print(f"\n{OK} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
