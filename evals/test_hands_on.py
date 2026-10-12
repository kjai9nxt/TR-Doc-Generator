"""THE PRACTICAL HANDS-ON SECTION, SCREENSHOT EXPLANATION, AND BULLET LENGTHS (1.63).

    python -m evals.test_hands_on        # no API key needed, ~5 seconds

What the reviewer reported, and what each block below pins:
  · "I am not able to increase the slide count from settings" — the recording and page
    ceilings now FOLLOW the slide ceiling (src/budgets.py), and the time grader reads them;
  · "provide a practical hands-on at the end, with a proper title" — one `kind: hands_on`
    section, last, gated on its name/slides/steps, and a guided chunk of its own;
  · "when I provide screenshots, use them at the right place and explain each" — an image
    block must sit on a slide that explains it in 30+ words; images are outside the page
    budget; the fallback placement prefers a hands-on step;
  · "2-3 words in a bullet won't work" — bullets are 5-28 word sentences, gated both ways.
"""
from __future__ import annotations
import copy
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, context_builder as CB, course_loader, pipeline, docx_writer   # noqa: E402
from src import budgets as budget_rules, skill_images                                  # noqa: E402
from graders import chunk_check, page_grader, time_grader                              # noqa: E402
from guardrails import guardrails                                                      # noqa: E402

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


spec = config.harness()
sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
prev, cur, nxt = course_loader.neighbours(15, sessions)
import yaml                                                                            # noqa: E402
_g = yaml.safe_load((ROOT / "evals" / "eval_set.yaml").read_text())["golden"]
GOLDEN = json.loads((ROOT / _g["json"]).read_text(encoding="utf-8"))

STEP = ("1. Open the AWS Management Console in a browser and sign in — the service "
        "search bar appears at the top of the page.")
HANDS_ON = {
    "kind": "hands_on",
    "name": "Hands-On: Comparing SCTP and TCP Handshakes in Wireshark",
    "slides": [{
        "n": 99, "title": "Capture Both Handshakes and Compare Them", "role": "hands_on",
        "heading": "Capture the Handshakes", "subheading": "Wireshark Walkthrough",
        "content": [
            {"type": "text", "text": "In this exercise the learner records one TCP connection "
                                     "and one SCTP association on their own machine and reads "
                                     "the handshake packets side by side, which shows why the "
                                     "SCTP cookie exists. Wireshark and a terminal are needed."},
            {"type": "bullets", "items": [
                STEP,
                "2. Start a capture on the active interface and run the provided client, so "
                "packets begin to appear in the list.",
                "3. Filter on sctp and read the INIT, INIT-ACK, COOKIE-ECHO and COOKIE-ACK "
                "rows in order, noting the cookie field.",
            ]},
            {"type": "text", "text": "When the exercise is complete the filter shows exactly "
                                     "four SCTP chunks for the association setup, and the "
                                     "TCP filter shows three segments for its handshake."},
            {"type": "bullets", "items": [
                "Capturing on the wrong interface shows no packets at all; pick the one "
                "carrying the client's traffic.",
                "Forgetting the sctp filter buries the four chunks among other traffic; apply "
                "it before reading the list.",
                "Reading the rows out of time order makes the cookie look like a reply; sort "
                "by the time column first.",
            ]},
        ],
        "visual_guidance": "Wireshark window with the sctp filter applied and four rows highlighted.",
        "speaker_notes": "Demo the capture live before the learners try it. Say that the "
                         "cookie is what TCP's SYN flood problem lacked. Interviewers ask why "
                         "four messages are not slower in practice.",
    }],
}


def with_hands_on(doc, section=HANDS_ON):
    d = copy.deepcopy(doc)
    sec = copy.deepcopy(section)
    sec["index"] = len(d["sections"]) + 1
    last = max((sl.get("n") or 0) for sc in d["sections"] for sl in sc.get("slides") or [])
    for sl in sec["slides"]:
        last += 1
        sl["n"] = last
    d["sections"].append(sec)
    return d


def fails_of(doc):
    return list(guardrails.check(doc, cur, False, False).failures)


print("\n== budgets follow the slide ceiling ==")
d = budget_rules.harness_defaults()
up = budget_rules._derive({**d, "max_slides": 30}, slides_set=True, pages_set=False)
check("30 slides raises the recording ceiling to 45 min", up["max_minutes"] == 45, str(up))
check("…and the page ceiling with it", up["max_pages"] > d["max_pages"], str(up["max_pages"]))
thirty = {"sections": [{"slides": [{"n": i, "content": []} for i in range(1, 31)]}]}
check("the time grader accepts 30 slides under that budget",
      time_grader.estimate(thirty, up)["within_budget"]
      and not time_grader.estimate(thirty)["within_budget"])
check("the prompt states the raised ceiling",
      "45 minutes" in CB.time_mode_block(True, budgets=up))
explicit = budget_rules._derive({**d, "max_slides": 30, "max_pages": 28},
                                slides_set=True, pages_set=True)
check("an explicit page ceiling is kept", explicit["max_pages"] == 28)

print("\n== the hands-on section is gated ==")
base = fails_of(GOLDEN)
check("the golden (no hands-on) is told to add one",
      any("Missing the PRACTICAL HANDS-ON" in f for f in base))
good = fails_of(with_hands_on(GOLDEN))
check("a well-formed hands-on section clears every hands-on gate",
      not any("hands-on" in f.lower() or "HANDS-ON" in f for f in good), str(good[-3:]))
check("…and adds no NEW failure of any kind",
      set(good) <= set(base), str(sorted(set(good) - set(base))[:3]))
check("…and is not counted as a takeaway section",
      guardrails.teaching_sections(with_hands_on(GOLDEN)) == GOLDEN["sections"])

vague = copy.deepcopy(HANDS_ON); vague["name"] = "Hands-On: Practice"
check("a vague title FAILS", any("does not say what the learner does" in f
                                 for f in fails_of(with_hands_on(GOLDEN, vague))))
noprefix = copy.deepcopy(HANDS_ON); noprefix["name"] = "Comparing SCTP and TCP Handshakes"
check("a name without the prefix FAILS", any("must start with" in f
                                            for f in fails_of(with_hands_on(GOLDEN, noprefix))))
nosteps = copy.deepcopy(HANDS_ON)
nosteps["slides"][0]["content"] = [b for b in nosteps["slides"][0]["content"]
                                   if b["type"] != "bullets"]
check("a hands-on without a step list FAILS", any("no step list" in f
                                                  for f in fails_of(with_hands_on(GOLDEN, nosteps))))
badrole = copy.deepcopy(HANDS_ON); badrole["slides"][0]["role"] = "mechanism"
check("a hands-on slide with another role FAILS", any("every slide there is role 'hands_on'" in f
                                                      for f in fails_of(with_hands_on(GOLDEN, badrole))))
withan = copy.deepcopy(HANDS_ON); withan["slides"][0]["analogy"] = "like a key — just as a cookie"
check("an analogy on a hands-on slide FAILS", any("must have NO analogy" in f
                                                  for f in fails_of(with_hands_on(GOLDEN, withan))))
early = copy.deepcopy(GOLDEN); sec = copy.deepcopy(HANDS_ON); sec["index"] = 1
early["sections"].insert(0, sec)
check("a hands-on that is not last FAILS", any("must be the LAST section" in f for f in fails_of(early)))

print("\n== bullets are sentences, not labels ==")
d2 = with_hands_on(GOLDEN)
d2["sections"][0]["slides"][0]["content"].append(
    {"type": "bullets", "items": ["Resource pooling", "Rapid elasticity", "Measured service"]})
f2 = fails_of(d2)
check("a 2-word bullet FAILS as a label", any("a label, not a teaching point" in f for f in f2))
d3 = with_hands_on(GOLDEN)
d3["sections"][0]["slides"][0]["content"].append(
    {"type": "bullets", "items": ["word " * 35, "a fine bullet of about eight words here",
                                  "another fine bullet of about eight words"]})
check("a 35-word bullet FAILS as a paragraph",
      any("that is a paragraph" in f for f in fails_of(d3)))

print("\n== a screenshot must be explained on its slide ==")
d4 = with_hands_on(GOLDEN)
d4["sections"][-1]["slides"][0]["content"].insert(
    2, {"type": "image", "ref": 777, "caption": "Wireshark with the sctp filter applied"})
check("an image on a slide whose text explains it passes",
      not any("does not explain it" in f for f in fails_of(d4)))
d5 = copy.deepcopy(d4)
d5["sections"][-1]["slides"][0]["content"] = [
    b for b in d5["sections"][-1]["slides"][0]["content"] if b["type"] != "text"]
check("an image on a slide with no explaining text FAILS",
      any("does not explain it" in f for f in fails_of(d5)))
pe_with, pe_without = page_grader.estimate(d4), page_grader.estimate(with_hands_on(GOLDEN))
check("the image is counted in the page total", pe_with["image_pages"] > 0, str(pe_with["image_pages"]))
check("…but not against the page gate", pe_with["text_pages"] == pe_without["text_pages"],
      f"{pe_with['text_pages']} vs {pe_without['text_pages']}")
with tempfile.TemporaryDirectory() as td:
    out = docx_writer.write_docx(d4, Path(td) / "t.docx")
    check("the renderer survives an image block it cannot find and a hands-on breaker",
          out.exists() and out.stat().st_size > 10_000)
    md = docx_writer.write_markdown(d4, Path(td) / "t.md").read_text(encoding="utf-8")
    check("the breaker reads PRACTICAL HANDS-ON: <title>",
          "PRACTICAL HANDS-ON: Comparing SCTP and TCP Handshakes in Wireshark" in md)
check("the brief tells the writer WHERE a screenshot goes and that it must be EXPLAINED",
      all(k in "\n".join(skill_images.brief([{"id": 1, "caption": "console"}]))
          for k in ("PRACTICAL HANDS-ON step", "HOW IT IS EXPLAINED", "outside the page budget")))
# fallback placement: on a word tie, the hands-on step wins
d6 = with_hands_on(GOLDEN)
notes = skill_images.reconcile(d6, [{"id": 5, "caption": "zzz qqq", "skill_text": ""}])
placed = [s for s in guardrails._slides(d6) if any(b.get("type") == "image" for b in s["content"])]
check("an unplaced screenshot with no matching words lands on the hands-on step",
      placed and placed[0]["role"] == "hands_on", str(notes))

print("\n== a hands-on only where the skills ask for one ==")
from src import db as _db, skills as _sk
_C = "__zz_hands_on_wanted"
try:
    _db._exec("DELETE FROM course_skills WHERE course=?", (_C,))
except Exception:
    pass
check("a course with no skills gets no hands-on", not CB.hands_on_wanted(_C, 1)["wanted"],
      CB.hands_on_wanted(_C, 1)["reason"])
_i1 = _db.add_skill(_C, "Use Indian-context analogies throughout.", kind="style"); _db.approve_skill(_i1, "t")
check("a skill that says nothing about practice does not switch it on",
      not CB.hands_on_wanted(_C, 1)["wanted"])
_i2 = _db.add_skill(_C, "End with a practical exercise the learner runs in VirtualBox.", kind="style")
_db.approve_skill(_i2, "t")
w = CB.hands_on_wanted(_C, 1)
check("a skill asking for a practical switches it on, naming the skill",
      w["wanted"] and "practical" in w["reason"].lower(), str(w))
try:
    _db._exec("DELETE FROM course_skills WHERE course=?", (_C,))
except Exception:
    pass
_i3 = _db.add_skill(_C, "Show the console as in the attached pictures.", kind="style"); _db.approve_skill(_i3, "t")
try:
    _db.add_skill_image(_C, _i3, key="k" * 24, filename="shot.png", mime="image/png",
                        caption="console", width=10, height=10, data_b64="AA==", created_by="t")
    w2 = CB.hands_on_wanted(_C, 1)
    check("a skill carrying a screenshot switches it on", w2["wanted"] and "screenshot" in w2["reason"], str(w2))
except Exception as e:
    print(f"  skip image case ({e})")
try:
    _db._exec("DELETE FROM course_skills WHERE course=?", (_C,))
except Exception:
    pass
no = {"hands_on": False}
check("a run without one reserves no slides", CB.hands_on_reserve(True, no) == 0)
check("…and the whole ceiling goes to the takeaways",
      sum(CB.slide_plan(cur, budgets=no)) == CB.slide_ceiling(True, no))
check("…and the prompt says not to add one",
      "NO PRACTICAL HANDS-ON SECTION" in CB.time_mode_block(True, budgets=no))
check("…and the gate does not demand one",
      not any("Missing the PRACTICAL HANDS-ON" in f
              for f in guardrails.check(GOLDEN, cur, False, False, budgets=no).failures))
check("a run WITH one still has it demanded",
      any("Missing the PRACTICAL HANDS-ON" in f
          for f in guardrails.check(GOLDEN, cur, False, False, budgets={"hands_on": True}).failures))

print("\n== the guided run has a hands-on chunk ==")
import server                                                                          # noqa: E402
n = cur.key_takeaways_count
state = {"cur": cur, "prev": prev, "nxt": nxt, "total": 1 + n + 1, "hands_on": True,
         "chunks": [{"kind": "opening", "fragment": {"agenda": ["a"]}}]
                   + [{"kind": "section", "fragment": {"section": {"slides": [{"n": i}] * 3}}}
                      for i in range(n)],
         "budgets": {}, "course": "x", "enforce_time": True}
kind, instr = server._chunk_spec(state, 1 + n)
check("the last chunk is the hands-on chunk", kind == "hands_on")
check("…with the exercise instruction", "PRACTICAL HANDS-ON section" in instr
      and "Hands-On:" in instr and "Practice" in instr)
check("…which knows how many slides the takeaways used", f"used {3 * n}" in instr, instr[:200])
check("takeaway budgets exclude the reserve",
      server._slide_budget_state(state, 1) == (3 * (n - 1), 1))
old = dict(state); old.pop("hands_on"); old["total"] = 1 + n
check("a run checkpointed before 1.63 has no hands-on chunk", server._hands_on_index(old) is None)
check("the hands-on flag is persisted with the run", "hands_on" in server._GUIDED_PERSIST_KEYS)
plan = CB.slide_plan(cur)
check("the slide plan leaves the reserve free",
      sum(plan) + CB.hands_on_reserve(True) == CB.slide_ceiling(True), str(plan))
frag = {"section": copy.deepcopy(HANDS_ON)}
res = chunk_check.gates(frag, cur, -1)
check("the chunk check runs on a hands-on fragment", res is not None)
check("…and does not fail it for lacking a takeaway or a coverage map",
      res is not None and not any("coverage" in f.lower() or "takeaway" in f.lower()
                                  for f in res.failures), str(res.failures[:3] if res else None))
doc = pipeline.assemble_doc(cur, nxt, {"recap": None, "agenda": ["1. a"]},
                            [{"name": kt, "slides": [{"n": 1}]} for kt in cur.key_takeaways]
                            + [copy.deepcopy(HANDS_ON)], [])
check("assembly keeps the hands-on section last with its own name",
      doc["sections"][-1]["kind"] == "hands_on"
      and doc["sections"][-1]["name"].startswith("Hands-On:")
      and [s["name"] for s in doc["sections"][:-1]] == list(cur.key_takeaways))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
