"""SCREENSHOTS MATCHED BY WHAT THEY SHOW; HANDS-ON SLIDES BARE AND EXTRA (1.70).

    python -m evals.test_screenshot_match     # no API key needed, ~5 seconds

The reviewer: "the screenshots are not coming relevant to the text … placed in the step in
which some other screenshot is required"; "hands-on slides should not have visual guide
and speaker notes"; "the number of slides decided for the session should not include the
hands-on, it should take additional slides". The vision calls are stubbed here; what is
pinned is everything around them: the stored description reaches the brief and the
hands-on prompt, the fallback matching uses it, a mismatch is found and turned into a
patch instruction, the two fields are stripped and gated, and the hands-on sits outside
the slide / page / time budgets.
"""
from __future__ import annotations
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import config, context_builder as CB, course_loader, db, llm, skill_images as SI  # noqa: E402
from graders import page_grader, time_grader                                            # noqa: E402
from guardrails import guardrails                                                       # noqa: E402

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
import yaml                                                                             # noqa: E402
_g = yaml.safe_load((ROOT / "evals" / "eval_set.yaml").read_text())["golden"]
GOLDEN = json.loads((ROOT / _g["json"]).read_text(encoding="utf-8"))

HO = spec["constraints"]["hands_on"]
check("harness: hands-on slides are bare and extra", HO.get("no_notes") and HO.get("extra_slides"))
check("harness: pictures are described and placement is checked",
      spec["constraints"]["images"].get("describe")
      and spec["gates"]["image_placement_check"]["enabled"])

print("\n== the picture is READ once and the reading reaches the writer ==")
SUBNETS = ("SHOWS: AWS console, VPC > Subnets list with two public subnets | VISIBLE: "
           "s5-public-subnet, s6-public-subnet-2, ap-south-1a, ap-south-1b | EVIDENCE OF: "
           "creating the second public subnet")
TARGETS = ("SHOWS: AWS console, EC2 > Target groups > s6-web-tg, Targets tab, both targets "
           "Healthy | VISIBLE: s6-web-1, s6-web-2, Healthy | EVIDENCE OF: registering targets")
IMGS = [{"id": 901, "skill_id": 7, "caption": "Screenshot from 2026-10-14 15-00-32",
         "filename": "Screenshot from 2026-10-14 15-00-32.png", "skill_text": "The examples."},
        {"id": 902, "skill_id": 7, "caption": "Pasted image (6)target health check",
         "filename": "Pasted image (6)target health check.png", "skill_text": "The examples."}]
check("a file-name caption is recognised as one",
      SI._looks_like_filename(IMGS[0]["caption"]) and SI._looks_like_filename("required 2")
      and not SI._looks_like_filename("Both targets healthy in the target group"))

calls = []
_saved = {}


def fake_vision(system, user, images, **kw):
    calls.append({"system": system, "user": user, "label": kw.get("label"), "n": len(images)})
    if "describe" in kw.get("label", ""):
        return json.dumps({"shows": "AWS console, VPC > Subnets list with two public subnets",
                           "visible_text": "s5-public-subnet, s6-public-subnet-2, ap-south-1a",
                           "evidence_of": "creating the second public subnet"})
    # verify: the explanation under 902 talks about subnets -> mismatch
    if "s6-web-tg" in user or "Healthy" in user:
        return json.dumps({"matches": True, "picture_shows": "target group healthy", "reason": "same"})
    return json.dumps({"matches": False, "picture_shows": "the target group with both targets Healthy",
                       "reason": "the text is about subnets"})


llm.complete_vision, _saved["cv"] = fake_vision, llm.complete_vision
db.skill_image, _saved["si"] = (lambda i: {"id": i, "mime": "image/png", "data_b64": "QUJD",
                                            "filename": "x.png"}), db.skill_image
db.set_skill_image_description, _saved["sd"] = (lambda i, d: _saved.setdefault("stored", {}).__setitem__(i, d) or True), db.set_skill_image_description

desc = SI.describe({"id": 1, "mime": "image/png", "data_b64": "QUJD", "filename": "a.png",
                    "caption": "Screenshot from 2026-10-14"})
check("describe() turns the vision JSON into one SHOWS | VISIBLE | EVIDENCE OF line",
      desc.startswith("SHOWS: AWS console") and "| VISIBLE: s5-public-subnet" in desc
      and "| EVIDENCE OF: creating" in desc, desc)
check("…and does not feed a file-name caption back as the author's words",
      "captioned it" not in calls[-1]["user"] and "File name: a.png" in calls[-1]["user"])
ims = copy.deepcopy(IMGS)
n0 = len(calls)
notes = SI.ensure_described(ims)
check("ensure_described reads each undescribed picture once and stores it",
      len(calls) - n0 == 2 and all(SI.description_of(i) for i in ims)
      and set(_saved.get("stored", {})) == {901, 902}, str(notes))
notes2 = SI.ensure_described(ims)
check("…and never reads a described one again", len(calls) - n0 == 2 and not notes2)
lines = "\n".join(SI.image_lines(ims))
check("the brief lists each picture with its id, order and what it SHOWS",
      "id 901 (picture 1 of 2 on this skill)" in lines and "SHOWS: AWS console" in lines, lines)
brief = "\n".join(SI.brief(ims))
check("…and tells the writer to match by what it shows, not the caption or order",
      "MATCH A PICTURE BY WHAT IT SHOWS" in brief and "left out rather than forced in" in brief)
check("a picture not yet read is flagged as such", "not yet read" in "\n".join(SI.image_lines(IMGS)))

print("\n== fallback matching uses the reading ==")
ims[0]["description"], ims[1]["description"] = SUBNETS, TARGETS
w0, w1 = SI.match_words(ims[0]), SI.match_words(ims[1])
check("match words come from the description, not the file name or the shared skill line",
      "subnets" in w0 and "healthy" in w1 and "screenshot" not in w0 and "examples" not in w0
      and "shows" not in w0, str(sorted(w0)))
doc = copy.deepcopy(GOLDEN)
doc["sections"][0]["slides"][0]["content"].append(
    {"type": "text", "text": "Register both web servers in the target group s6-web-tg and wait "
                             "until the health check shows every target healthy."})
doc["sections"][1]["slides"][0]["content"].append(
    {"type": "text", "text": "Create the second public subnet s6-public-subnet-2 in another "
                             "availability zone so the subnets span two zones."})
SI.reconcile(doc, ims)
where = {b["ref"]: s["n"] for s in SI._slides(doc) for b in s["content"] if b.get("type") == "image"}
check("the subnet picture lands on the subnet slide and the target picture on the target slide",
      where.get(901) == doc["sections"][1]["slides"][0]["n"]
      and where.get(902) == doc["sections"][0]["slides"][0]["n"], str(where))

print("\n== after the draft: is each picture under the words that explain it? ==")
frag = {"section": {"kind": "hands_on", "name": "Hands-On: Building a Load-Balanced Pair",
                    "slides": [{"n": 30, "role": "hands_on", "heading": "Create the Subnet",
                                "subheading": "Step 1", "content": [
                                    {"type": "image", "ref": 902, "caption": "Subnets"},
                                    {"type": "text", "text": "This is the subnet list after the "
                                     "second public subnet is created in a second zone, which "
                                     "the load balancer needs to spread traffic across zones."}]},
                               {"n": 31, "role": "hands_on", "heading": "Check Targets",
                                "subheading": "Step 5", "content": [
                                    {"type": "image", "ref": 901, "caption": "Targets"},
                                    {"type": "text", "text": "The target group s6-web-tg shows "
                                     "both servers Healthy, which means the load balancer will "
                                     "send requests to either of them from now on."}]}]}}
chk = SI.verify_placement(frag, ims)
check("one vision call per placed picture, each sent with the text beside it",
      chk["checked"] == 2 and all(c["label"] == "image_check" for c in calls[-2:])
      and "THE TEXT EXPLAINING THIS PICTURE" in calls[-1]["user"])
check("the picture explained as a different screen is a mismatch",
      [m["ref"] for m in chk["mismatches"]] == [902], str(chk))
fix = SI.placement_fix_instruction(chk, ims)
check("the patch names what the picture really shows and lists every picture to choose from",
      "image id 902 on slide 30" in fix and "both targets Healthy" in fix
      and "id 901" in fix and "swap in that picture's id" in fix)
frag_noimg = {"section": {"kind": "hands_on", "slides": [{"n": 1, "content": [{"type": "text", "text": "x"}]}]}}
check("a chunk without pictures is not checked at all", SI.verify_placement(frag_noimg, ims)["checked"] == 0)

print("\n== hands-on slides carry no visual guidance and no speaker notes ==")
HOSEC = {"kind": "hands_on", "name": "Hands-On: Comparing SCTP and TCP Handshakes in Wireshark",
         "slides": [{"n": 99, "title": "Capture", "role": "hands_on", "heading": "Capture Them",
                     "subheading": "Wireshark", "content": [
                         {"type": "text", "text": " ".join(["word"] * 40)},
                         {"type": "bullets", "items": [
                             "1. Install Wireshark and open a capture on the active interface.",
                             "2. Run the client to open one TCP connection and one SCTP association.",
                             "3. Filter the capture on sctp and read the four setup chunks.",
                             "4. Compare them with the three TCP handshake segments side by side."]},
                         {"type": "text", "text": " ".join(["word"] * 30)}],
                     "visual_guidance": "Wireshark window.", "speaker_notes": "Demo it live."}]}


def with_ho(doc, sec=HOSEC):
    d = copy.deepcopy(doc)
    s = copy.deepcopy(sec)
    s["index"] = len(d["sections"]) + 1
    last = max((sl.get("n") or 0) for sc in d["sections"] for sl in sc.get("slides") or [])
    for sl in s["slides"]:
        last += 1
        sl["n"] = last
    d["sections"].append(s)
    return d


def fails_of(doc, budgets=None):
    return list(guardrails.check(doc, cur, False, False, budgets=budgets).failures)


d = with_ho(GOLDEN)
fs = fails_of(d)
check("a hands-on slide WITH the two fields FAILS",
      any("carries no 'visual_guidance'" in f for f in fs) and any("carries no 'speaker_notes'" in f for f in fs))
n = CB.hands_on_strip(d)
check("hands_on_strip removes both and nothing else",
      n == 2 and "speaker_notes" not in d["sections"][-1]["slides"][0]
      and all(s.get("speaker_notes") for sec in d["sections"][:-1] for s in sec["slides"]))
fs2 = fails_of(d)
check("…and the bare slide is not 'missing' them",
      not any(("missing 'speaker_notes'" in f or "missing 'visual_guidance'" in f) for f in fs2), str(fs2[:3]))
frag2 = {"section": copy.deepcopy(HOSEC)}
check("…and works on a guided fragment", CB.hands_on_strip(frag2) == 2
      and "visual_guidance" not in frag2["section"]["slides"][0])
plain = copy.deepcopy(GOLDEN)
check("…and leaves a teaching slide alone", CB.hands_on_strip(plain) == 0)
instr = CB.hands_on_instruction("", cur)
check("the hands-on prompt asks for neither field",
      "NO `visual_guidance` AND NO `speaker_notes`" in instr
      and '"speaker_notes": "..."' not in instr and "decided by WHAT IT SHOWS" in instr)

print("\n== the hands-on takes its own slides, outside the session's budgets ==")
check("no slides are reserved from the takeaways", CB.hands_on_reserve(True) == 0)
check("the takeaway plan uses the whole ceiling", sum(CB.slide_plan(cur)) == CB.slide_ceiling(True))
check("the hands-on allowance is its own max, however many slides the takeaways used",
      CB.hands_on_allowance(True, None, slides_used=CB.slide_ceiling(True)) == int(HO["max_slides"]))
check("the prompt says the hands-on is OUTSIDE the ceiling",
      "OUTSIDE this ceiling" in CB.time_mode_block(True, budgets={"hands_on": True})
      and "OF ITS OWN" in instr)
check("…but a run without one still says so",
      "NO PRACTICAL HANDS-ON SECTION" in CB.time_mode_block(True, budgets={"hands_on": False}))
ceiling = CB.slide_ceiling(True)
full = copy.deepcopy(GOLDEN)
# pad the teaching sections to exactly the ceiling, then add a 4-slide hands-on on top
tmpl = copy.deepcopy(full["sections"][-1]["slides"][-1])
k = sum(len(s["slides"]) for s in full["sections"])
while k < ceiling:
    k += 1
    t = copy.deepcopy(tmpl); t["n"] = k
    full["sections"][-1]["slides"].append(t)
ho4 = copy.deepcopy(HOSEC)
ho4["slides"] = [copy.deepcopy(ho4["slides"][0]) for _ in range(4)]
full_ho = with_ho(full, ho4)
CB.hands_on_strip(full_ho)
fs3 = fails_of(full_ho, budgets={"hands_on": True})
check(f"{ceiling} teaching slides + 4 hands-on slides is NOT over the slide ceiling",
      not any("too many" in f for f in fs3), str([f for f in fs3 if "slides" in f][:2]))
fs4 = fails_of(full_ho, budgets={"hands_on": True, "max_slides": ceiling - 2})
check("…while the teaching sections alone are still gated",
      any("in the teaching sections" in f and "too many" in f for f in fs4), str(fs4[:2]))
bud = {"hands_on": True}
t_ho = time_grader.estimate(with_ho(GOLDEN), bud)
t_plain = time_grader.estimate(GOLDEN, bud)
check("the hands-on adds nothing to the recording estimate",
      t_ho.get("estimated_minutes", t_ho.get("total_minutes")) == t_plain.get("estimated_minutes", t_plain.get("total_minutes")),
      f"{t_ho} vs {t_plain}")
p_ho = page_grader.estimate(with_ho(GOLDEN), bud)
p_plain = page_grader.estimate(GOLDEN, bud)
check("…nor to the pages the gate counts, but is reported",
      p_ho["text_pages"] == p_plain["text_pages"] and p_ho["estimated_pages"] >= p_plain["estimated_pages"]
      and p_ho["pages_by_part"].get("hands_on_extra", 0) > 0, f"{p_ho['text_pages']} vs {p_plain['text_pages']}")
p_in = page_grader.estimate(with_ho(GOLDEN), {"hands_on": False})
check("a run that decided against a hands-on counts whatever is there",
      p_in["text_pages"] >= p_plain["text_pages"] and "hands_on_extra" not in p_in["pages_by_part"])

llm.complete_vision, db.skill_image, db.set_skill_image_description = _saved["cv"], _saved["si"], _saved["sd"]
print(f"\n{OK} ok, {FAIL} failed")
sys.exit(1 if FAIL else 0)
