"""THE COURSE BRIEF IS CHECKED ON EVERY CHUNK, AND A MISSED LINE IS FIXED BEFORE REVIEW.

    python -m evals.test_brief_check        # no API key needed, ~5 seconds

Also pins the learner-clarity warning for acronyms the document never expands.
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, course_loader, db, skills, llm, generator, patcher   # noqa: E402
from guardrails import guardrails                                            # noqa: E402
import server                                                                # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


COURSE = "__zz_brief_check_course"
sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
prev, cur, nxt = course_loader.neighbours(15, sessions)

# a course with two approved skills
try:
    db._exec("DELETE FROM course_skills WHERE course=?", (COURSE,))
except Exception:
    pass
ids = [db.add_skill(COURSE, "Every mechanism slide ends with one line saying what breaks without it.",
                    kind="style"),
       db.add_skill(COURSE, "Use the railway-booking example throughout the session.", kind="style")]
for i in ids:
    db.approve_skill(i, "t@x")
rows = skills.numbered(COURSE, 15)
check("the course's skills are numbered S1, S2", [r["ref"] for r in rows] == ["S1", "S2"],
      str([r.get("ref") for r in rows]))

FRAG = {"section": {"name": cur.key_takeaways[0], "slides": [
    {"n": 1, "title": "Why SCTP Exists", "role": "concept_intro", "heading": "Why SCTP",
     "subheading": "Two Gaps", "content": [
         {"type": "text", "text": "Stream Control Transmission Protocol (SCTP) carries several "
                                  "independent streams in one association, so a lost packet on one "
                                  "stream never blocks the others. It exists because TCP holds every "
                                  "byte behind the first missing one."},
         {"type": "bullets", "items": [
             "Multi-streaming keeps separate streams moving when one of them loses a packet.",
             "Multi-homing lets one association use two network paths for failover.",
             "The four-way handshake with a cookie resists SYN-style flooding attacks."]}],
     "analogy": "A railway booking counter with several queues — just as SCTP keeps streams apart.",
     "visual_guidance": "Two queues side by side.", "speaker_notes": "Say the everyday version first. Ask what TCP would do here."}]},
    "coverage": {"takeaway": cur.key_takeaways[0], "sub_concepts": [{"name": "HOL blocking", "slide": 1},
                                                                    {"name": "multi-homing", "slide": 1}]}}

# ---- the check itself, with the judge stubbed ----
_real_complete = llm.complete
replies = []
def _fake_complete(**kw):
    return json.dumps(replies.pop(0))
llm.complete = _fake_complete
try:
    replies.append({"brief_verdicts": [
        {"ref": "S1", "criterion": "each mechanism slide ends with a breaks-without-it line",
         "status": "fail", "evidence": "slide 1 has no such line"},
        {"ref": "S2", "criterion": "the railway example recurs", "status": "pass", "evidence": "analogy"}]})
    res = skills.chunk_brief_check(COURSE, 15, FRAG)
    check("the check reads a verdict per skill", res["checked"] and set(res["verdicts"]) == {"S1", "S2"}, str(res))
    check("…and names the missed one", res["missed"] == ["S1"], str(res["missed"]))
    fix = skills.brief_fix_instruction(res)
    check("the fix instruction quotes the missed line and the evidence",
          "[S1]" in fix and "what breaks without it" in fix and "no such line" in fix, fix[:200])
    replies.append({"brief_verdicts": [{"ref": "S1", "status": "nonsense"}, {"ref": "S9", "status": "pass"}]})
    res2 = skills.chunk_brief_check(COURSE, 15, FRAG)
    check("garbage verdicts are not a pass", not res2["checked"] and res2["missed"] == [], str(res2))
    check("a course with no skills is not checked", not skills.chunk_brief_check("__zz_none", 1, FRAG)["checked"])
finally:
    llm.complete = _real_complete

# ---- the guided chunk generator patches a missed line before review ----
gid = "test-brief-check"
server.GUIDED[gid] = {
    "status": "generating_all", "session_no": 15, "session_title": cur.name,
    "prev": prev, "cur": cur, "nxt": nxt, "base_context": "ctx",
    "total": 1 + cur.key_takeaways_count + 1, "hands_on": True, "index": 0,
    "labels": ["Opening"] + [f"T{i}" for i in range(cur.key_takeaways_count)] + ["Hands-On"],
    "chunks": [{"kind": "opening", "fragment": {"agenda": ["1. a"]}, "markdown": ""}],
    "regen_index": None, "use_judge": True, "enforce_time": True, "user_email": "t@x",
    "budgets": {}, "course": COURSE, "logs": [], "result": None, "error": None,
}
_real_gen, _real_patch, _real_check, _real_apply = (generator.generate_chunk, generator.generate_patch,
                                                    skills.chunk_brief_check, patcher.apply)
calls = {"patch": 0}
checks = [
    {"checked": True, "verdicts": {"S1": {"status": "fail", "evidence": "missing"}, "S2": {"status": "pass", "evidence": ""}},
     "missed": ["S1"], "lines": {r["ref"]: r["text"] for r in rows}},
    {"checked": True, "verdicts": {"S1": {"status": "pass", "evidence": "fixed"}, "S2": {"status": "pass", "evidence": ""}},
     "missed": [], "lines": {r["ref"]: r["text"] for r in rows}},
]
def _gen(*a, **k): return copy.deepcopy(FRAG)
def _patch(*a, **k):
    calls["patch"] += 1
    check("the patch reason names the missed brief line", "[S1]" in a[3] and "breaks without it" in a[3])
    return {"edit_slides": [{"n": 1, "fields": {"speaker_notes": "Say the everyday version first. Without multi-streaming one lost packet stalls everything."}}], "note": "applied S1"}
generator.generate_chunk, generator.generate_patch = _gen, _patch
skills.chunk_brief_check = lambda *a, **k: checks.pop(0)
try:
    chunk = server._gen_one(gid, 1, [server.GUIDED[gid]["chunks"][0]])
    check("a missed brief line triggers exactly one patch", calls["patch"] == 1, str(calls))
    check("the patched chunk carries a clean brief check",
          chunk.get("brief_check", {}).get("missed") == [] and chunk["brief_check"]["checked"] == 2,
          str(chunk.get("brief_check")))
    check("…and the patch landed in the fragment",
          "Without multi-streaming" in chunk["fragment"]["section"]["slides"][0]["speaker_notes"])
    logs = "\n".join(server.GUIDED[gid]["logs"]) if isinstance(server.GUIDED[gid]["logs"][0], str) \
        else "\n".join(l.get("msg", str(l)) for l in server.GUIDED[gid]["logs"])
    check("the log says what was checked and fixed", "not applied — one patch before review" in logs and "still missed" in logs, logs[-300:])
    check("the brief_check field is persisted with the chunk",
          "chunks" in server._GUIDED_PERSIST_KEYS)
    check("the UI reads brief_check from the chunk",
          "c.brief_check" in (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8"))
finally:
    generator.generate_chunk, generator.generate_patch = _real_gen, _real_patch
    skills.chunk_brief_check, patcher.apply = _real_check, _real_apply
    server.GUIDED.pop(gid, None)
    try:
        db._exec("DELETE FROM course_skills WHERE course=?", (COURSE,))
    except Exception:
        pass

# ---- acronyms a learner cannot decode ----
print("\n== undefined acronyms are warned ==")
import yaml
_g = yaml.safe_load((ROOT / "evals" / "eval_set.yaml").read_text())["golden"]
GOLDEN = json.loads((ROOT / _g["json"]).read_text(encoding="utf-8"))
def warns_of(doc):
    return list(guardrails.check(doc, cur, False, False).warnings)
d = copy.deepcopy(GOLDEN)
d["sections"][0]["slides"][0]["content"].append({"type": "text", "text": "The QXZT layer handles it; QXZT is fast and QXZT is cheap, which matters."})
w = warns_of(d)
check("an acronym never expanded is warned, naming it", any("QXZT" in x and "never expanded" in x for x in w), str(w[-1:]))
d2 = copy.deepcopy(d)
d2["sections"][0]["slides"][0]["speaker_notes"] += " Quick Exchange Zone Transport (QXZT) is the name."
check("…and not once it is expanded anywhere in the document", not any("QXZT" in x for x in warns_of(d2)))
check("common acronyms (TCP, RFC) are not nagged", not any("RFC" in x or "TCP" in x for x in warns_of(GOLDEN)))
check("the rubric weights learner clarity and the brief heavily",
      all(w in (ROOT / "rubrics" / "tr_doc_rubric.yaml").read_text() for w in
          ("- id: clarity_and_language\n    # 4 -> 10", "- id: course_brief_adherence\n    # 6 -> 12")))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
