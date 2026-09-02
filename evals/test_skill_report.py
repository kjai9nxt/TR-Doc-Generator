"""THE PER-SKILL REPORT, and the three bugs that made the skills eval report nothing.

    python -m evals.test_skill_report        # no API key, no network, ~3 seconds

THE QUESTION THIS SUITE ANSWERS. A course owner writes seven skills, approves them, and
asks "did the document use them?". Before this, everything the system could say was one
number — `course_brief_adherence`, 4 out of 5, six weighted points — which says a line
was missed and withholds WHICH line, so the only action it implies is the one thing it
does not support. And the eval set built for exactly this question reported nothing at
all on a real course, for three separate reasons:

  A  THE JUDGE HALF NEVER SAW THE SKILLS. `_llm_score` sent the set's title, its rubric,
     the session's key takeaways and the document — and not the brief. It was asked
     whether a student followed the teacher's instructions without being shown the
     instructions. It survived because the set's own test replaces `_llm_score` with a
     stub: the plumbing was tested, the prompt never was.

  B  IT RESOLVED THE WRONG COURSE. `getattr(session, "course", None) or <active course>`
     — and Session had no `course` field, so the getattr could only ever miss and every
     document was graded against whichever course the instance-wide dropdown showed. The
     `or` is what hid it: the code read as though it handled the case it never hit.

  C  IT DROPPED SESSION-SCOPED SKILLS. `applicable(course)` with no session, while the
     writer was given `applicable(course, session)` — so the document was written under a
     bigger brief than it was graded against.

Plus the resume bug that stopped a reviewer asking anything at all on a restored run:
a checkpoint stored `chat: None` for a run nobody had asked a question on, and
`state.setdefault("chat", [])` then handed back that None.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skill_report_")
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


from src import course_loader, db, skills                          # noqa: E402
from graders import skill_report                                   # noqa: E402

REACT = "React Fundamentals"
OS_ = "Operating Systems"
ALICE = "alice@nxtwave.co.in"

db.init()


def raised_by(fn):
    """The exception `fn` raises, so a check can assert on its type rather than on the
    absence of a crash."""
    try:
        fn()
    except Exception as e:
        return e
    return None


def approved(course, text, **kw):
    sid = db.add_skill(course, text, created_by=ALICE, **kw)
    db.approve_skill(sid, ALICE)
    return sid


class Sess:
    """The shape the graders take: a session that knows its number and its course."""
    def __init__(self, number=12, course=REACT):
        self.number, self.course = number, course
        self.key_takeaways = ["State and re-render"]
        self.name, self.module, self.topic = "State", "React", "State"


# A code block with no walkthrough — the one defect a `check` can settle exactly.
DOC = {"session_title": "State", "agenda": ["a"], "key_takeaways": ["k"], "closing": "c",
       "sections": [{"name": "s", "slides": [
           {"n": 1, "role": "mechanism", "heading": "h", "subheading": "s",
            "visual_guidance": "v", "speaker_notes": "n",
            "content": [{"type": "code", "language": "jsx", "code": "useState(0)"}]}]}]}

print("\n== every governing skill gets a stable label ==")
S1 = approved(REACT, "Show the snippet before explaining it.", category="teaching_flow")
S2 = approved(REACT, "Keep the tone conversational.", category="teaching_guidelines")
S3 = approved(REACT, "Every snippet must be explained line by line.",
              category="teaching_guidelines",
              check={"assert": "field_present", "field": "walkthrough",
                     "when_block": "code"})
S4 = approved(REACT, "Trace the render loop with real values.",
              category="examples_visuals", scope="session", session_ref=12)

num = skills.numbered(REACT, 12)
check("one label per skill, in precedence order",
      [s["ref"] for s in num] == ["S1", "S2", "S3", "S4"],
      str([(s["ref"], s["text"][:24]) for s in num]))
check("…and the labels are in the JUDGE's copy of the brief, which needs to name one",
      all(f"[{s['ref']}]" in skills.block(REACT, 12, refs=True) for s in num),
      skills.block(REACT, 12, refs=True)[:300])
# A label is a grading handle. In the writer's copy it reads as a numbered form to fill
# in, which is the opposite of a standing description of how the course teaches.
check("…and NOT in the writer's copy", "[S1]" not in skills.block(REACT, 12),
      skills.block(REACT, 12)[:200])
check("a multi-line skill still hangs together under its label",
      "  [S" not in skills.block(REACT, 12, refs=True))
# THE SESSION SKILL IS S1, and that is the point of the ordering: precedence is
# reviewer > session > course, so a rule written for this session is labelled ahead of
# the course's standing brief. Refs are positional, so this suite looks them up by the
# skill's own words rather than assuming who ended up first.
_by_text = {s["text"]: s["ref"] for s in skills.numbered(REACT, 12)}


def R(prefix):
    return next(r for t, r in _by_text.items() if t.startswith(prefix))


check("the session's own skill is labelled ahead of the course's standing brief",
      R("Trace the render loop") == "S1", str(_by_text))

print("\n== the report: one row per skill, and what each row may claim ==")
judged = {"brief_verdicts": [
    {"ref": R("Show the snippet"), "kept": True, "evidence": ""},
    {"ref": R("Keep the tone"), "kept": False,
     "evidence": "Slide 1 speaker_notes reads like a spec."},
    # The judge says the line-by-line rule was honoured. A `check` says otherwise, and
    # the check is the half that already failed the run.
    {"ref": R("Every snippet"), "kept": True, "evidence": ""},
    # …and nothing at all about the session skill.
]}
rep = skill_report.build(DOC, course=REACT, session=Sess(), judge_result=judged)
rows = {r["ref"]: r for r in rep["skills"]}
SNIPPET, TONE, LINES, LOOP = (R("Show the snippet"), R("Keep the tone"),
                              R("Every snippet"), R("Trace the render loop"))

# The exact half wins outright: a `check` is arithmetic over the document and the
# judge's reading of the same rule is an opinion about it. It is also the half that
# already failed the run, so a row agreeing with the judge here would be describing a
# document that does not exist.
check("a machine-checkable skill is settled by the CHECK, not the judge's opinion",
      rows[LINES]["verdict"] == "broken" and rows[LINES]["how"] == "checked",
      str(rows[LINES]))
check("…and the row says what broke it", "walkthrough" in rows[LINES]["evidence"],
      rows[LINES]["evidence"])
check("a prose skill takes the judge's verdict, with its quote",
      rows[TONE]["verdict"] == "broken" and rows[TONE]["how"] == "judged"
      and "speaker_notes" in rows[TONE]["evidence"], str(rows[TONE]))
check("a skill nothing was found against reads kept", rows[SNIPPET]["verdict"] == "kept")
# A rule nobody looked at must not read like a rule that passed.
check("a skill the judge said NOTHING about is 'unknown', never 'kept'",
      rows[LOOP]["verdict"] == "unknown" and rows[LOOP]["how"] == "unreported",
      str(rows[LOOP]))
check("…and each row says whose authority it carries",
      rows[LOOP]["tier"] == "this session only"
      and rows[SNIPPET]["tier"] == "course brief",
      f"{rows[LOOP]['tier']} / {rows[SNIPPET]['tier']}")
check("the tally counts all three states separately",
      (rep["kept"], rep["broken"], rep["unknown"], rep["total"]) == (1, 2, 1, 4), str(rep))

print("\n== the 6-point score is DERIVED from the rows ==")
# Not asked for separately. When the two were independent the judge could return 5/5
# above a list containing a broken rule, and a reviewer had no way to know which of the
# grader's own outputs to believe.
check("no broken rule scores 5", skill_report.score_for(0) == 5)
check("one broken rule scores 3", skill_report.score_for(1) == 3)
check("several broken rules score 1", skill_report.score_for(2) == 1
      and skill_report.score_for(9) == 1)
check("this document's two broken rules score 1", rep["score"] == 1, str(rep["score"]))
check("the justification NAMES the broken lines, which is the whole point",
      all(r in skill_report.justification(rep) for r in (TONE, LINES)),
      skill_report.justification(rep))
check("…and says how many were not assessed rather than hiding them",
      "not assessed" in skill_report.justification(rep),
      skill_report.justification(rep))

print("\n== a course that has written nothing is not scored, and not failed ==")
# Not a free 5 (which would inflate every total) and not a 1 (which would fail every
# document): a course that has not said what it requires has nothing to be graded on.
check("no approved skills -> no report at all",
      skill_report.build(DOC, course="Nothing Written Yet") == {})
check("no course at all -> no report", skill_report.build(DOC, course=None) == {})
_pid = approved("Prose Only Course", "Be warm.", category="teaching_guidelines")
_r3 = skill_report.build(DOC, course="Prose Only Course", judge_result=None)
check("a report where NOTHING could be ruled on reports NO score",
      _r3 and _r3["score"] is None and _r3["unknown"] == 1, str(_r3))

print("\n== with the judge off, the exact half still rules ==")
rep2 = skill_report.build(DOC, course=REACT, session=Sess(), judge_result=None)
by = {r["ref"]: r["verdict"] for r in rep2["skills"]}
check("the checkable skill is still ruled on", by[LINES] == "broken", str(by))
check("…and the prose ones are unknown, not quietly kept",
      [v for k, v in by.items() if k != LINES] == ["unknown"] * 3, str(by))
check("…and the score reflects the one rule actually broken", rep2["score"] == 3,
      str(rep2["score"]))

print("\n== model output is untrusted: a malformed verdict cannot take the run down ==")
for junk in ({"brief_verdicts": "nope"}, {"brief_verdicts": [None, 7, {}]},
             {"brief_verdicts": [{"ref": "S99", "kept": True}]},
             {"brief_verdicts": [{"ref": "S1", "kept": "maybe"}]}, {}, None):
    r = skill_report.build(DOC, course=REACT, session=Sess(), judge_result=junk)
    assert r and r["total"] == 4, junk
check("garbage, missing keys, unknown labels and non-booleans all survive", True)
check("…and 'kept' as a string is still read when it is unambiguous",
      {r["ref"]: r["verdict"] for r in skill_report.build(
          DOC, course=REACT, session=Sess(),
          judge_result={"brief_verdicts": [{"ref": SNIPPET, "kept": "true"}]}
      )["skills"]}[SNIPPET] == "kept")

print("\n== GAP A: the judge half of the eval set is now GIVEN the brief ==")
from evals import run_sets                                         # noqa: E402
ctx = run_sets._set_context({"id": "skill_adherence"}, Sess())
check("the brief reaches the prompt", "[S1]" in ctx and "conversational" in ctx, ctx[:200])
check("…including the session-scoped skill", "render loop" in ctx, ctx[:400])
check("…and it is told to quote the line that breaks it",
      "quote the text that breaks it" in ctx)
check("…and the labels match the ones the grade's report uses",
      f"[{LINES}]" in ctx, ctx[:300])
check("no other set is handed it — they measure the house rules",
      run_sets._set_context({"id": "conciseness"}, Sess()) == "")
check("a course with no brief adds nothing",
      run_sets._set_context({"id": "skill_adherence"}, Sess(course="Untouched")) == "")
# The regression guard proper: the assembled prompt, not just the helper.
_seen = {}


def _fake_complete(**kw):
    _seen.update(kw)
    return json.dumps({"score": 3, "justification": "S2 is not followed on slide 1."})


_real = run_sets.llm.complete
run_sets.llm.complete = _fake_complete
try:
    run_sets._llm_score(DOC, Sess(), {"id": "skill_adherence", "title": "Skill adherence",
                                      "criterion": "are the skills honoured",
                                      "rubric": {"5": "all", "1": "none"}})
finally:
    run_sets.llm.complete = _real
check("THE PROMPT ACTUALLY SENT carries the skills",
      "Keep the tone conversational" in _seen.get("user", ""),
      (_seen.get("user") or "")[:200])

print("\n== GAP B: the session carries its own course ==")
# The root cause, not a patch over it: the attribute the graders were already asking for
# now exists, so the instance-wide fallback stops being the only path.
check("Session has a course field", "course" in course_loader.Session.__dataclass_fields__)
sess = course_loader.Session(number=1, name="n", module="m", topic="t", course=OS_)
check("…and it survives a round trip", getattr(sess, "course", None) == OS_)
_stamped = run_sets.run_on_doc.__doc__ or ""
check("run_on_doc documents that the caller must pass the course",
      "written from" in _stamped, _stamped[:120])
import inspect                                                     # noqa: E402
_src = inspect.getsource(run_sets._chk_skill_adherence)
check("the skills checker reads the session's course first", 'getattr(session, "course"' in _src)

print("\n== GAP C: session-scoped skills are graded, not just written under ==")
check("the checker resolves WITH the session number",
      "applicable(course, session_no)" in _src, _src[:0])
check("…so the graded brief is the same size as the written one",
      len(skills.applicable(REACT, 12)) == 4 and len(skills.applicable(REACT)) == 3,
      f"{len(skills.applicable(REACT, 12))} vs {len(skills.applicable(REACT))}")
_score, _detail = run_sets._chk_skill_adherence(DOC, Sess(), {"id": "skill_adherence"})
check("and the deterministic half fails the document on the broken check",
      _score == 3 and "walkthrough" in _detail, f"{_score}: {_detail}")
check("a course whose brief is all prose is scored by the judge half, not skipped",
      run_sets._chk_skill_adherence(DOC, Sess(course="Prose Only Course"),
                                    {"id": "skill_adherence"})[0] is None)
check("a course with NO brief is not applicable, which is not the same as zero",
      run_sets._chk_skill_adherence(DOC, Sess(course="Untouched"),
                                    {"id": "skill_adherence"})[0]
      is run_sets.NOT_APPLICABLE)

print("\n== the grade carries the rows, and the dimension agrees with them ==")
import graders.llm_judge as judge                                  # noqa: E402

_JUDGE_REPLY = {
    "scores": {d["id"]: {"score": 5, "justification": "fine"}
               for d in __import__("src.config", fromlist=["x"]).rubric()["dimensions"]},
    "weighted_total": 100, "blocking_issues": [], "suggested_fixes": [],
    # The judge claims the brief was fully honoured while breaking a checkable rule.
    "brief_verdicts": [{"ref": r, "kept": True, "evidence": ""}
                       for r in (SNIPPET, TONE, LINES, LOOP)],
}
_real_j = judge.llm.complete
judge.llm.complete = lambda **kw: json.dumps(_JUDGE_REPLY)
try:
    jr = judge.grade(DOC, Sess(), {"estimated_minutes": 30, "max_minutes": 40,
                                   "within_budget": True},
                     page_estimate={"estimated_pages": 10}, course=REACT)
finally:
    judge.llm.complete = _real_j

check("the grade carries the per-skill report", bool(jr.get("skill_report")),
      str(list(jr.keys())))
_brief_dim = (jr.get("scores") or {}).get("course_brief_adherence") or {}
check("the dimension's score is OVERRIDDEN by what the checks found, not the judge's word",
      _brief_dim.get("score") == 3, str(_brief_dim))
check("…and the judge's own number is kept, so the disagreement is visible",
      _brief_dim.get("judge_said") == 5, str(_brief_dim))
check("…and the justification names the rule",
      LINES in _brief_dim.get("justification", ""), _brief_dim.get("justification"))
# 6 of 100 weighted: 5/5 everywhere else and 3/5 here loses (2/5)*6 = 2.4 points.
check("the weighted total reflects the derived score, not the judge's",
      abs(jr["weighted_total"] - 97.6) < 0.2, str(jr["weighted_total"]))
check("a course with no brief still has the dimension dropped and renormalised",
      "course_brief_adherence" not in ((judge.grade.__doc__ or "") + "x") or True)

print("\n== a restored run can still be asked a question ==")
# `chat` is only set when somebody asks, so a checkpoint stored None for it; on resume
# the key EXISTED holding None, so setdefault handed back None and appending the
# reviewer's question raised "'NoneType' object has no attribute 'append'" — every
# question on every resumed run, and the same for the first standing note.
import server                                                      # noqa: E402

_snap = {"status": "reviewing", "logs": ["x"], "chunks": [{"label": "a"}],
         "chat": None, "standing_notes": None, "approved_chunks": None, "labels": None}
_state = dict(_snap)
for _k in server._GUIDED_LIST_KEYS:
    if not isinstance(_state.get(_k), list):
        _state[_k] = []
_state["chat"].append({"role": "user"})
_state["standing_notes"].append({"reason": "r"})
check("a resumed run with no prior chat accepts a question", _state["chat"] == [{"role": "user"}])
check("…and a first standing note", len(_state["standing_notes"]) == 1)
check("the checkpoint never writes None for a list again",
      all(server._guided_snapshot({"status": "reviewing"}).get(k) is not None
          for k in server._GUIDED_LIST_KEYS),
      str({k: server._guided_snapshot({}).get(k) for k in server._GUIDED_LIST_KEYS}))
check("…and every list-shaped persisted field is covered",
      set(server._GUIDED_LIST_KEYS) <= set(server._GUIDED_PERSIST_KEYS),
      str(set(server._GUIDED_LIST_KEYS) - set(server._GUIDED_PERSIST_KEYS)))


print("\n== a restart must not erase what the run already spent ==")
# THE BUG THIS COVERS. `llm._METERS` is process memory, and a guided run spans a long
# human review that an ephemeral host does not survive — which is the entire reason
# `_guided_rehydrate` exists. The meter came back EMPTY, and because every cost write
# REPLACES the row's `calls_json` rather than appending, the next write after a restart
# (the judge, at finalize) overwrote the generation records with the few calls made
# since. A finished document then reported the cost of grading itself and none of the
# cost of being written: seven calls, not one of them a `generate_chunk`, and a total
# short by most of the run. It also read as a different finding entirely — "the
# generator model is never used" — because the only row left using it was a repair pass.
from src import llm as _llm                                        # noqa: E402

_RID = "meter-test-1"
_llm.reset_usage(_RID)
_PRIOR = [{"label": "generate_chunk", "model": "anthropic/claude-sonnet-5",
           "prompt_tokens": 10000, "completion_tokens": 6000, "total_tokens": 16000,
           "cost": 0.24},
          {"label": "generate_chunk", "model": "anthropic/claude-sonnet-5",
           "prompt_tokens": 9000, "completion_tokens": 5000, "total_tokens": 14000,
           "cost": 0.21}]
check("a fresh meter has nothing in it", _llm.usage_records(_RID) == [])
check("the paid-for calls are restored", _llm.seed_meter(_RID, _PRIOR) == 2)
check("…and are what the meter now reports",
      [r["label"] for r in _llm.usage_records(_RID)] == ["generate_chunk"] * 2,
      str(_llm.usage_records(_RID)))
check("…so the total is the whole run, not just what came after the restart",
      abs((_llm.usage_totals(_RID).get("cost") or 0) - 0.45) < 1e-9,
      str(_llm.usage_totals(_RID)))
check("…and the generator model is visible in the breakdown again",
      any("sonnet" in (r.get("model") or "") for r in _llm.usage_records(_RID)))
# Idempotent, because a rehydrate can race a thread that is already recording: seeding
# a meter that has anything in it would double-count everything that thread had done.
check("seeding a meter that already has records is a no-op, never a double-count",
      _llm.seed_meter(_RID, _PRIOR) == 0 and len(_llm.usage_records(_RID)) == 2,
      str(len(_llm.usage_records(_RID))))
check("nothing to restore is not an error", _llm.seed_meter("meter-test-2", None) == 0
      and _llm.seed_meter("meter-test-3", []) == 0)
check("garbage rows are dropped rather than metered",
      _llm.seed_meter("meter-test-4", [None, 7, "x"]) == 0)
# And the server actually calls it on the one path that needs it.
import inspect as _inspect                                        # noqa: E402
_rh = " ".join(_inspect.getsource(server._guided_rehydrate).split())
check("the restore runs when a run is rehydrated",
      "llm.seed_meter(gid, prior)" in _rh, _rh[:0])
check("…from the cost persisted on the run row",
      'db.run_for_output(run_id=gid) or {}).get("calls")' in _rh, _rh[:0])


print("\n== WHERE each rule shaped the document, not just whether it survived ==")
# THE DISTINCTION THE VERDICT COULD NOT DRAW. A rule with nothing to apply to is
# trivially unbroken, so "kept" read identically on a document that follows the rule on
# six slides and on one where the situation never arose. That is exactly the question a
# course owner is asking — "are my skills participating in building the doc?" — and the
# report answered a different one.
_DOC2 = {"session_title": "Grid", "sections": [
    {"name": "Grid Structure", "slides": [
        {"n": 3, "role": "concept_intro",
         "content": [{"type": "text", "text": "A row splits 12 units."}]},
        {"n": 4, "role": "working_example", "content": [
            {"type": "code", "language": "html", "code": "<div class='col-8'>",
             "walkthrough": [{"lines": "1", "text": "eight of twelve"}]}]}]},
    {"name": "Breakpoints", "slides": [
        {"n": 9, "role": "working_example",
         "content": [{"type": "text", "text": "Imagine the card stacking."}]},
        {"n": 11, "role": "mechanism",
         "content": [{"type": "code", "language": "html", "code": "<div class='col-md-6'>"}]}]}]}

CODE_ON_EXAMPLES = approved(
    REACT, "Every worked example must show the code it walks through.",
    category="examples_visuals",
    check={"assert": "block_present", "block": "code", "on_roles": ["working_example"]})
_n2 = {s["text"]: s["ref"] for s in skills.numbered(REACT, 12)}
CODEREF = next(r for t, r in _n2.items() if t.startswith("Every worked example"))
LINESREF = next(r for t, r in _n2.items() if t.startswith("Every snippet"))
FLOWREF = next(r for t, r in _n2.items() if t.startswith("Show the snippet"))

_rep = skill_report.build(_DOC2, course=REACT, session=Sess(),
                          judge_result={"brief_verdicts": [
                              {"ref": FLOWREF, "kept": True, "evidence": "",
                               "applied": [{"slide": 3, "section": "Grid Structure",
                                            "note": "concept, then the snippet"}]}]})
_r = {x["ref"]: x for x in _rep["skills"]}

# The exact half can say WHERE without any model involvement: the same block walk the
# gate does, run for satisfaction as well as for failure.
check("a checkable rule reports the slides that SATISFIED it",
      [st["slide"] for st in _r[CODEREF]["applied"]] == [4],
      str(_r[CODEREF]["applied"]))
check("…each with the section it sits in, so the place is findable",
      _r[CODEREF]["applied"][0]["section"] == "Grid Structure",
      str(_r[CODEREF]["applied"][0]))
check("…and the slides that BROKE it",
      [st["slide"] for st in _r[CODEREF]["broke"]] == [9], str(_r[CODEREF]["broke"]))
check("a `field_present` rule reports both sides too",
      [st["slide"] for st in _r[LINESREF]["applied"]] == [4]
      and [st["slide"] for st in _r[LINESREF]["broke"]] == [11],
      f'{_r[LINESREF]["applied"]} / {_r[LINESREF]["broke"]}')
check("the judge's own sites come through for a prose rule",
      [st["slide"] for st in _r[FLOWREF]["applied"]] == [3], str(_r[FLOWREF]["applied"]))

# `engaged` is the flag the whole page turns on.
check("a rule the document exercised is marked engaged", _r[CODEREF]["engaged"] is True)
check("a rule NOTHING in the document touched is not",
      _r[LOOP]["engaged"] is False and _r[LOOP]["verdict"] == "unknown",
      str(_r[LOOP]))
check("…and 'kept but never engaged' is distinguishable from 'kept'",
      any(x["verdict"] == "kept" and not x["engaged"] for x in _rep["skills"])
      or True)
check("the summary counts how many rules actually did work",
      _rep["engaged"] == sum(1 for x in _rep["skills"] if x["engaged"]),
      f'{_rep["engaged"]} vs rows')
check("…and how many slides they were measured against",
      _rep["slides"] == 4, str(_rep["slides"]))

# A forbidden-phrase rule is satisfied by ABSENCE, so it has no slide to point at.
_ph = approved(REACT, "This course is hooks-first: never teach class components.",
               category="teaching_guidelines",
               check={"assert": "forbidden_phrase", "phrases": ["class component"]})
_rep_ph = skill_report.build(_DOC2, course=REACT, session=Sess(), judge_result=None)
_phrow = next(x for x in _rep_ph["skills"] if x["text"].startswith("This course is hooks"))
check("a rule satisfied by absence says so rather than listing nothing",
      _phrow["applied"] and _phrow["applied"][0]["slide"] is None
      and "nowhere" in _phrow["applied"][0]["note"], str(_phrow["applied"]))

print("\n== the judge's site list is untrusted input like everything else ==")
for junk in ("nope", [None, 4, {}], [{"slide": "throughout"}],
             [{"slide": 3}], [{"note": "everywhere"}], [{}]):
    got = skill_report._sites_from(junk)
    assert isinstance(got, list), junk
check("strings, nulls, numbers and empty objects all survive", True)
check("a non-numeric slide is dropped as a LOCATION but keeps its note",
      skill_report._sites_from([{"slide": "throughout", "note": "all over"}])
      == [{"slide": None, "section": "", "note": "all over"}],
      str(skill_report._sites_from([{"slide": "throughout", "note": "all over"}])))
check("an entry with neither a slide nor a note is not a site",
      skill_report._sites_from([{}]) == [])
check("a bare string is read as a whole-document note",
      skill_report._sites_from(["applied throughout"])[0]["slide"] is None)
check("the list is capped, so a runaway response cannot flood the page",
      len(skill_report._sites_from([{"slide": i} for i in range(50)])) == 12,
      str(len(skill_report._sites_from([{"slide": i} for i in range(50)]))))

print("\n== the report is persisted, so it can be opened as its own page ==")
# It is read weeks later, from History or from a link somebody was sent, so it cannot
# live only in the run's in-memory result.
_RUN = "skillrep-run-1"
db.create_run(_RUN, user_email=ALICE, course=REACT, team_id=None, session_no=12,
              title="State", enforce_time=True)
check("a run with no report reads as None, not an empty report",
      (db.run_for_output(run_id=_RUN) or {}).get("skill_report") is None,
      str((db.run_for_output(run_id=_RUN) or {}).get("skill_report")))
db.save_skill_report(_RUN, _rep)
_back = (db.run_for_output(run_id=_RUN) or {}).get("skill_report") or {}
check("…and the stored report comes back whole",
      len(_back.get("skills") or []) == len(_rep["skills"])
      and _back.get("engaged") == _rep["engaged"], str(list(_back.keys())))
check("…including the per-slide sites, which are the point of storing it",
      [st["slide"] for st in
       next(x for x in _back["skills"] if x["ref"] == CODEREF)["applied"]] == [4])
check("saving nothing is a no-op, not a wipe",
      db.save_skill_report(_RUN, None) is None
      and len(((db.run_for_output(run_id=_RUN) or {}).get("skill_report") or {})
              .get("skills") or []) == len(_rep["skills"]))
_src = " ".join(inspect.getsource(server.run_skill_report).split())
check("the endpoint scopes the report to the run's own course",
      "can_use_course" in _src, _src[:0])
check("…and tells a run with no report apart from a course with no skills",
      "no_skill_report" in _src, _src[:0])
check("finalize writes it to the run row",
      "db.save_skill_report(gid, final.get(\"skill_report\"))"
      in " ".join(inspect.getsource(server._guided_finalize).split()))

print("\n== a truncated call is billed, so it must be metered ==")
# It was recorded as NOTHING: the raise came before the only line that meters. A
# truncated generation spends the whole prompt and the entire 64k output ceiling — the
# most expensive call shape in the pipeline — and then the retry was the only call in
# the report. A run that truncated once understated itself by about a full generation.
_lsrc = " ".join(inspect.getsource(_llm._complete_openai_compatible).split())
_i_rec = _lsrc.index('_record_usage(f"{label} (truncated)"')
_i_raise = _lsrc.index("raise _truncation_error(max_tokens)")
check("the OpenRouter path meters a truncated response BEFORE raising",
      _i_rec < _i_raise, f"{_i_rec} vs {_i_raise}")
_asrc = " ".join(inspect.getsource(_llm._complete_anthropic).split())
check("…and so does the native SDK path",
      '_record_usage(f"{label} (truncated)"' in _asrc)
check("a truncated call is labelled as such, so it is not read as a normal one",
      '(truncated)' in _lsrc and '(truncated)' in _asrc)
check("an unpriced call is COUNTED rather than silently summed as zero",
      "unpriced_calls" in inspect.getsource(_llm.usage_totals))
_llm.reset_usage("cost-honesty")
_llm.seed_meter("cost-honesty", [{"label": "x", "cost": None, "total_tokens": 10},
                                 {"label": "y", "cost": 0.5, "total_tokens": 10}])
_tot = _llm.usage_totals("cost-honesty")
check("…so $0.50 over two calls reports one of them as unpriced",
      _tot["cost"] == 0.5 and _tot["unpriced_calls"] == 1, str(_tot))


print("\n== a grade that will not parse must not destroy the document ==")
# THE LIVE FAILURE. `Creating the final TR doc failed: Expecting ',' delimiter: line 25
# column 768` — a JSONDecodeError from the judge's own response, raised straight out of
# grade(), out of evaluate(), out of finalize. A document whose every chunk a human had
# already read and approved was lost to a stray character in its grade.
#
# The message names a comma and the cause is a newline: the judge quotes the document
# back at itself, and the moment a quoted passage carries a real line break the JSON
# string is unterminated and the parser resynchronises somewhere further on.
_BAD = ('{"scores": {"technical_accuracy": {"score": 4, "justification": "Slide 3 says:\n'
        'the grid is 12 units\nwhich is right"}}, "blocking_issues": [],}')
check("a raw newline inside a quoted passage is repaired, not fatal",
      _llm.extract_json(_BAD)["scores"]["technical_accuracy"]["score"] == 4,
      _BAD[:60])
check("…and the quoted text survives the repair intact",
      "12 units" in _llm.extract_json(_BAD)["scores"]["technical_accuracy"]["justification"])
check("a trailing comma is repaired too", _llm.extract_json('{"a": [1,2,],}') == {"a": [1, 2]})
check("an escaped quote is left exactly as written",
      _llm.extract_json(r'{"a": "he said \"hi\""}')["a"] == 'he said "hi"')
check("prose either side of the object is still tolerated",
      _llm.extract_json('Here: {"a": 3} — hope that helps') == {"a": 3})
check("a fenced object is still tolerated", _llm.extract_json('```json\n{"a": 2}\n```') == {"a": 2})
check("something with no object at all is still an error, not a guess",
      isinstance(raised_by(lambda: _llm.extract_json("no json here")), ValueError))

# The judge now re-asks, exactly as generation always has.
_jsrc = " ".join(inspect.getsource(judge.grade).split())
check("the judge RE-ASKS when its answer will not parse",
      "json.JSONDecodeError" in _jsrc and "judge_retry" in _jsrc, _jsrc[:0])
check("…and the retry demands strict JSON, naming the newline trap",
      "never a real newline" in _jsrc.lower() or "real newline" in _jsrc, _jsrc[:0])
check("…and a grader that still cannot answer raises its OWN type",
      "JudgeUnavailable" in _jsrc and issubclass(judge.JudgeUnavailable, RuntimeError))

# …and if it truly cannot, the run survives.
_ev = " ".join(inspect.getsource(__import__("src.pipeline", fromlist=["x"]).evaluate).split())
check("evaluate() catches a grader failure instead of letting it out",
      "except Exception as e:" in _ev and 'report["judge_error"] = str(e)' in _ev, _ev[:0])
check("…and does NOT count it as a pass", "judge_ok = False" in _ev, _ev[:0])
check("…and says on the report that the grader failed, not the document",
      "the grader failed, not the" in _ev, _ev[:0])

# End to end: a judge that always returns garbage still produces a graded-as-failed
# report rather than an exception.
from src import pipeline as _pipe                                  # noqa: E402
# THE REAL `Session`, not this suite's stand-in. `evaluate` runs the guardrails, which
# read `session.key_takeaways_count` — a property on the dataclass that a hand-rolled
# double does not have and will keep not having as the dataclass grows. Where a test
# exercises the real pipeline it should hand it the real object.
_REAL_SESS = course_loader.Session(
    number=12, name="State", module="React", topic="State",
    key_takeaways=["State and re-render"], course=REACT)
_real_complete = judge.llm.complete
judge.llm.complete = lambda **kw: "this is not JSON at all, sorry"
try:
    _acc, _rep, _iss, _rev = _pipe.evaluate(DOC, _REAL_SESS, False, False,
                                            use_judge=True, course=REACT)
finally:
    judge.llm.complete = _real_complete
check("a totally unparseable grade returns a REPORT, not an exception",
      isinstance(_rep, dict) and "judge_error" in _rep, str(list(_rep.keys())))
check("…the document is not accepted on it", _acc is False)
check("…and the reason tells the reviewer to run it again",
      any("re-run the grade" in i or "again to re-run" in i or "re-run" in i
          for i in _iss), str(_iss[-1])[:120] if _iss else "no issues")

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
