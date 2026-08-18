"""The DEPLOYED database driver, exercised on the paths that are new.

    python -m evals.test_cloud_driver     # skips if libsql-experimental is absent

WHY THIS EXISTS. The deployed instance runs on Turso (libSQL); every other suite runs on
local SQLite, so the cloud driver had no coverage at all. That was tolerable while every
write went through _exec() — one statement, one connection — but renumbering a
curriculum does something nothing else does: several execute()s and then a single
commit(), with the order chosen so rows do not collide on a primary key that has not
moved yet. On a driver that handled that differently, a shift could half-apply and leave
two sessions sharing a number.

It runs the REAL src.db functions with the REAL driver against a LOCAL libsql file, so
it proves the API contract without touching the deployed database — which it must never
do, because that is the users' live curriculum.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="libsql_check_")
os.environ["TR_DATA_DIR"] = TMP
# Never let a stray cloud URL turn this into a write against production.
os.environ.pop("TURSO_DATABASE_URL", None)
os.environ.pop("TURSO_AUTH_TOKEN", None)

try:
    import libsql_experimental as libsql      # noqa: E402
except ImportError:
    print("libsql-experimental is not installed — skipping the cloud-driver checks.\n"
          "  pip install libsql-experimental   (it is in requirements.txt, so the "
          "deployed instance has it)")
    sys.exit(0)

from src import db                            # noqa: E402

DBFILE = os.path.join(TMP, "libsql_local.db")
# Point db at a LOCAL libsql file: real driver, no network, no production.

# Force every db.* call through the libSQL driver, pointed at a local file.
db._connect = lambda: libsql.connect(DBFILE)

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


print(f"\ndriver: libsql_experimental, local file {DBFILE}\n")
db.init()
print("== the schema is created through the driver ==")
rows = db._query("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
names = [r["name"] for r in rows]
check("curriculum + kb_files exist",
      "curriculum" in names and "kb_files" in names, str(names))

print("\n== a curriculum written through the driver ==")
C = "Turso Check Course"
for n in (1, 2, 3, 4):
    db.curriculum_upsert(C, n, session_name=f"original {n}",
                         key_takeaways=[f"takeaway {n}"],
                         ppt_link=f"https://x/{n}")
got = [(r["session_no"], r["session_name"]) for r in db.curriculum(C)]
check("four rows round-trip", got == [(1, "original 1"), (2, "original 2"),
                                      (3, "original 3"), (4, "original 4")], str(got))

print("\n== THE MULTI-STATEMENT SHIFT — the thing that had never run on this driver ==")
mapping = db.curriculum_shift_from(C, 2, by=1)
got = [(r["session_no"], r["session_name"]) for r in db.curriculum(C)]
check("the shift reports what moved", mapping == {4: 5, 3: 4, 2: 3}, str(mapping))
check("EVERY row actually moved in the database",
      got == [(1, "original 1"), (3, "original 2"), (4, "original 3"), (5, "original 4")],
      str(got))
check("…nothing was lost to a primary-key collision", len(got) == 4, str(got))

db.curriculum_upsert(C, 2, session_name="inserted", key_takeaways=["new"])
got = [(r["session_no"], r["session_name"]) for r in db.curriculum(C)]
check("the new row lands in the gap", got[1] == (2, "inserted"), str(got))

print("\n== shifting DOWN (a delete) ==")
db.curriculum_delete(C, 2)
mapping = db.curriculum_shift_from(C, 3, by=-1)
got = [(r["session_no"], r["session_name"]) for r in db.curriculum(C)]
check("the gap closes", [n for n, _ in got] == [1, 2, 3, 4], str(got))
check("…content stays with its row",
      [t for _, t in got] == ["original 1", "original 2", "original 3", "original 4"],
      str(got))

print("\n== the kb_files rename, same multi-statement pattern ==")
for rel, body in (("decks/session_03.json", "three"), ("decks/session_04.json", "four")):
    db._exec("INSERT OR REPLACE INTO kb_files (path, content, updated_at) VALUES (?,?,?)",
             (rel, body, "now"))
n = db.kb_rename_decks({3: 4, 4: 5})
stored = {r["path"]: r["content"]
          for r in db._query("SELECT path, content FROM kb_files")}
check("both rows moved", n == 2, f"moved {n}")
check("…without clobbering each other",
      stored.get("decks/session_04.json") == "three"
      and stored.get("decks/session_05.json") == "four", str(stored))
check("…and no temp path survived",
      not [k for k in stored if "__moving__" in k], str(list(stored)))
check("kb_forget removes one", db.kb_forget("decks/session_04.json")
      and "decks/session_04.json" not in
      {r["path"] for r in db._query("SELECT path FROM kb_files")})

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
