"""OVER-LONG BULLETS AND PARAGRAPHS ARE FIXED BEFORE REVIEW, NOT JUST LOGGED.

    python -m evals.test_length_fix        # no API key needed, a few seconds

The reviewer's report: "the bullets are a bit lengthy — not all the time, but in a few
slides — and the paragraph was also large sometimes". The caps existed (bullet <= 22
words, paragraph norm 50 / hard 75) and the chunk gate measured them — but only wrote
the failure to the run log, so the section reached review looking finished and was
approved. These checks pin the detector and the pre-review patch it now drives.
"""
from __future__ import annotations
import copy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import course_loader, generator, skills          # noqa: E402
import server                                             # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
prev, cur, nxt = course_loader.neighbours(15, sessions)

LONG_BULLET = ("Multi-streaming keeps every separate stream moving independently even when one "
               "of them loses a packet somewhere along the path, unlike TCP which blocks all")
OK_BULLET = "Multi-homing lets one association use two network paths for failover."
LONG_PARA = " ".join(["SCTP carries several independent streams in one association."] * 7)
OK_PARA = ("SCTP carries several independent streams in one association. A lost packet on "
           "one stream never blocks the others.")


def frag(para, bullets, extra_paras=()):
    slides = [{"n": 1, "title": "Why SCTP", "content": [
        {"type": "text", "text": para},
        {"type": "bullets", "items": list(bullets)}]}]
    for i, p in enumerate(extra_paras, start=2):
        slides.append({"n": i, "title": f"S{i}", "content": [{"type": "text", "text": p}]})
    return {"section": {"name": cur.key_takeaways[0], "slides": slides},
            "coverage": {"takeaway": cur.key_takeaways[0], "sub_concepts": []}}


print("== the detector ==")
hits = server._chunk_length_hits(frag(LONG_PARA, [LONG_BULLET, OK_BULLET, OK_BULLET]))
kinds = sorted(h["kind"] for h in hits)
check("finds the over-long bullet and the over-cap paragraph",
      kinds == ["bullet", "paragraph"], str(hits))
check("a clean section has none",
      not server._chunk_length_hits(frag(OK_PARA, [OK_BULLET] * 3)))
mid = " ".join(["A packet lost on one stream in SCTP never blocks the other streams."] * 4)  # 56w, 4 sentences
check("one paragraph over the norm in five is allowed (topic needs it)",
      not server._chunk_length_hits(frag(mid, [OK_BULLET] * 3, [OK_PARA] * 4)))
over = server._chunk_length_hits(frag(mid, [OK_BULLET] * 3, [mid, OK_PARA]))
check("…but over the share, the longest are trimmed back to it",
      len(over) == 1 and over[0]["kind"] == "paragraph", str(over))
instr = server._length_fix_instruction(hits)
check("the fix prompt quotes the bullet in full and its limit",
      LONG_BULLET in instr and "max 22" in instr, instr[:300])
check("…and the paragraph, with the 2-3 sentence norm",
      "2-3 sentences" in instr and "SCTP carries" in instr)

print("\n== the pre-review patch ==")
gid = "test-length-fix"
server.GUIDED[gid] = {
    "status": "generating_all", "session_no": 15, "session_title": cur.name,
    "prev": prev, "cur": cur, "nxt": nxt, "base_context": "ctx",
    "total": 1 + cur.key_takeaways_count + 1, "hands_on": False, "index": 0,
    "labels": ["Opening"] + [f"T{i}" for i in range(cur.key_takeaways_count)],
    "chunks": [{"kind": "opening", "fragment": {"agenda": ["1. a"]}, "markdown": ""}],
    "regen_index": None, "use_judge": True, "enforce_time": True, "user_email": "t@x",
    "budgets": {}, "course": "", "logs": [], "result": None, "error": None,
}
calls = {"patch": 0, "reason": ""}
_real = (generator.generate_chunk, generator.generate_patch, skills.chunk_brief_check)


def _patch(*a, **k):
    calls["patch"] += 1
    calls["reason"] = a[3]
    return {"edit_slides": [{"n": 1, "fields": {"content": [
        {"type": "text", "text": OK_PARA},
        {"type": "bullets", "items": [
            "Telephony signalling was the first workload SCTP was designed to carry.",
            "Each stream numbers its own messages with a stream sequence number.",
            OK_BULLET]}]}}], "note": "trimmed"}


generator.generate_chunk = lambda *a, **k: copy.deepcopy(
    frag(LONG_PARA, [LONG_BULLET, OK_BULLET, OK_BULLET]))
generator.generate_patch = _patch
skills.chunk_brief_check = lambda *a, **k: {"checked": False, "verdicts": {}, "missed": []}
try:
    chunk = server._gen_one(gid, 1, [server.GUIDED[gid]["chunks"][0]])
    check("an over-long section triggers one patch", calls["patch"] == 1, str(calls))
    check("the patch is told what to trim", "LENGTH TO FIX" in calls["reason"])
    check("the reviewer gets the trimmed section",
          not server._chunk_length_hits(chunk["fragment"]),
          str(server._chunk_length_hits(chunk["fragment"])))
    logs = "\n".join(l if isinstance(l, str) else l.get("msg", str(l))
                     for l in server.GUIDED[gid]["logs"])
    check("the log says so", "over-long" in logs and "0 over-long line(s)" in logs, logs[-400:])
finally:
    generator.generate_chunk, generator.generate_patch, skills.chunk_brief_check = _real
    server.GUIDED.pop(gid, None)

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
