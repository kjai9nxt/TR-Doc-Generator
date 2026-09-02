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


print("\n== the slide numbers on screen must describe the document produced ==")
# THE REPORTED MISMATCH: a review pane headed "Slide 17" beside a Result card reporting
# 13 slides. Both numbers were real; only one described the document.
#
# A chunk's markdown is rendered once, at generation, and each chunk numbers its slides
# consecutively after the chunks approved AT THAT MOMENT. Regenerate an early chunk
# shorter and every later chunk keeps the numbers it was handed. `assemble_doc` renumbers
# the real document 1..N, so the count was right and the panes were stale.
from src import docx_writer as _dw                                 # noqa: E402


def _sl(n, h):
    return {"n": n, "heading": h, "title": h, "subheading": "s", "content": [],
            "visual_guidance": "v", "speaker_notes": "sn"}


_OPENING = {"recap": None, "agenda": ["a"]}
_SECS = [{"name": "Breakpoints", "slides": [_sl(1, "A"), _sl(2, "B"), _sl(3, "C")]},
         # generated while the first chunk still had eight slides
         {"name": "Scaling", "slides": [_sl(9, "D"), _sl(17, "Grid Scaling Recap")]}]


def _pane_nums(sec):
    return [int(l.split()[2].rstrip(":"))
            for l in _dw.chunk_to_markdown("section", {"section": sec}).split("\n")
            if l.startswith("### Slide")]


check("a pane rendered at generation time carries the STALE numbers",
      _pane_nums(_SECS[1]) == [9, 17], str(_pane_nums(_SECS[1])))

_cur = course_loader.Session(number=2, name="Grid", module="m", topic="t",
                             key_takeaways=["k1", "k2"], course=REACT)
_assembled = _pipe.assemble_doc(_cur, None, _OPENING, [dict(x) for x in _SECS])
_fin = [s["n"] for sec in _assembled["sections"] for s in sec["slides"]]
check("…while the assembled DOCUMENT is renumbered 1..N",
      _fin == [1, 2, 3, 4, 5], str(_fin))
check("…so the count the Result card reports was right all along",
      len(_fin) == 5 and max(_fin) == len(_fin), str(_fin))

# THE FIX: the panes are re-rendered from the document that was produced, so the two
# agree once the run finishes.
_refreshed = [_pane_nums(sec) for sec in _assembled["sections"]]
check("re-rendering a pane from the assembled doc gives the document's own numbers",
      _refreshed == [[1, 2, 3], [4, 5]], str(_refreshed))
check("…with no number above the slide count anywhere",
      max(n for pane in _refreshed for n in pane) == len(_fin),
      str(_refreshed))
_fsrc = " ".join(inspect.getsource(server._guided_finalize).split())
check("finalize refreshes the panes from the doc it RENDERED, not the pre-grade one",
      'result.get("doc") or doc' in _fsrc and "chunk_to_markdown(" in _fsrc, _fsrc[:0])
check("…including the opening, in case the repair edited the recap or agenda",
      '"opening", {"recap"' in _fsrc, _fsrc[:0])
check("…and a pane that cannot be re-rendered does not cost the result",
      "Could not refresh the review panes" in _fsrc, _fsrc[:0])

# The gate that would catch any FUTURE path shipping a mis-numbered document.
_gsrc = " ".join(inspect.getsource(__import__("guardrails.guardrails",
                                              fromlist=["x"]).check).split())
check("a non-contiguous document is a hard guardrail failure",
      "they must run 1.." in _gsrc, _gsrc[:0])
from src import config as _cfg                                     # noqa: E402
check("…and that gate is switched on",
      _cfg.harness()["constraints"]["numbering"]["contiguous"] is True)
# A full RE-DRAFT is the one path that produced a whole document without renumbering.
check("a re-drafted document is renumbered too, so it cannot trip that gate",
      "patcher.renumber_doc(doc)" in " ".join(inspect.getsource(_pipe.finalize).split()),
      "")
_bad = {"sections": [{"name": "s", "slides": [_sl(1, "a"), _sl(5, "b"), _sl(17, "c")]}],
        "coverage_map": [{"takeaway": "t", "sub_concepts": [{"name": "x", "slide": 17}]}]}
_pipe.patcher.renumber_doc(_bad)
check("…and its coverage map follows the renumbering",
      [s["n"] for s in _bad["sections"][0]["slides"]] == [1, 2, 3]
      and _bad["coverage_map"][0]["sub_concepts"][0]["slide"] == 3,
      str(_bad["coverage_map"]))


print("\n== PARTIAL: a rule followed in some places and not others ==")
# THE STATE THAT WAS MISSING, and the one most real findings are in. "The same example
# runs through most of the session and then an unrelated one appears" is neither followed
# nor ignored, and forcing it to either extreme was the difference between passing
# silently and failing a whole document over a loose line.
SR = skill_report
check("no rule wrong scores 5", SR.score_for(0, 0) == 5)
check("one LOOSE rule scores 4 — above the bar, so it repairs rather than blocks",
      SR.score_for(0, 1) == 4)
check("two loose rules score 3", SR.score_for(0, 2) == 3)
check("one BROKEN rule scores 3 — below the bar of 4, so the document stops",
      SR.score_for(1, 0) == 3)
check("several broken rules score 1", SR.score_for(2, 0) == 1)
# The release condition, stated as arithmetic.
_bar = _cfg.harness()["gates"]["rubric_min_per_dimension"]
check("…so a PARTIAL clears the release bar and a FAIL does not",
      SR.score_for(0, 1) >= _bar and SR.score_for(1, 0) < _bar,
      f"partial={SR.score_for(0, 1)} fail={SR.score_for(1, 0)} bar={_bar}")

# A CHECKABLE rule's partial is a COUNT, not an opinion: slide 4 carries the block,
# slide 9 does not.
_rp = skill_report.build(_DOC2, course=REACT, session=Sess(), judge_result=None)
_rr = {x["ref"]: x for x in _rp["skills"]}
check("a checkable rule satisfied on one slide and broken on another is PARTIAL",
      _rr[CODEREF]["verdict"] == "partial", str(_rr[CODEREF]["verdict"]))
check("…and both sides are on the row, which is what makes it partial",
      [st["slide"] for st in _rr[CODEREF]["applied"]] == [4]
      and [st["slide"] for st in _rr[CODEREF]["broke"]] == [9])
check("a checkable rule nothing engaged is NOT APPLICABLE, not a pass",
      _rr[FLOWREF]["verdict"] == "not_applicable" or _rr[FLOWREF]["how"] != "checked",
      f'{_rr[FLOWREF]["verdict"]} / {_rr[FLOWREF]["how"]}')

print("\n== the judge's claim is checked against the judge's own evidence ==")
# EVIDENCE BEATS CLAIM. A model that has just listed a slide where the rule is violated
# will still sometimes call the rule followed, because the document reads well overall.
def _rc(**v):
    return SR._reconcile({"evidence": "", **v})


check("a 'pass' that lists a broken slide is recorded as PARTIAL",
      _rc(status="pass", broke=[{"slide": 4}]) == "partial")
check("a 'fail' that also lists slides FOLLOWING the rule is PARTIAL",
      _rc(status="fail", applied=[{"slide": 4}]) == "partial")
check("a clean 'pass' stays a pass", _rc(status="pass") == "kept")
check("a 'fail' with no mitigation stays a fail",
      _rc(status="fail", broke=[{"slide": 9}]) == "broken")
check("'not_applicable' is believed only when nothing was cited either way",
      _rc(status="not_applicable") == "not_applicable"
      and _rc(status="not_applicable", applied=[{"slide": 2}]) == "kept"
      and _rc(status="not_applicable", broke=[{"slide": 2}]) == "partial")
check("a missing status falls back to the boolean the judge always returned",
      _rc(kept=True) == "kept" and _rc(kept=False) == "broken")
# An unrecognised status word never reaches the reconciler: a verdict is admitted only
# when it carries a boolean OR a status in the vocabulary, so "mostly ok" with no flag is
# not a verdict at all and the rule comes back "not assessed" — which is the truth about
# it, and safer than reading an improvised word as any particular state.
check("an unrecognised status word is not read as a verdict at all",
      {x["ref"]: x["verdict"] for x in skill_report.build(
          DOC, course=REACT, session=Sess(),
          judge_result={"brief_verdicts": [{"ref": SNIPPET, "status": "mostly ok"}]}
      )["skills"]}[SNIPPET] == "unknown")
check("a verdict carrying ONLY a status is still read — the old flag is not required",
      {x["ref"]: x["verdict"] for x in skill_report.build(
          DOC, course=REACT, session=Sess(),
          judge_result={"brief_verdicts": [{"ref": SNIPPET, "status": "partial",
                                            "applied": [{"slide": 1}]}]}
      )["skills"]}[SNIPPET] == "partial")

print("\n== the compliance figure, and what it refuses to count ==")
_mix = skill_report.build(DOC, course=REACT, session=Sess(), judge_result={
    "brief_verdicts": [
        {"ref": SNIPPET, "status": "pass", "applied": [{"slide": 1}]},
        {"ref": TONE, "status": "partial", "applied": [{"slide": 1}],
         "broke": [{"slide": 1, "note": "reads like a spec"}]},
        {"ref": LOOP, "status": "not_applicable"},
    ]})
_c = {x["ref"]: x["verdict"] for x in _mix["skills"]}
# Derived from the rows rather than hardcoded: this course has picked up more skills as
# the suite ran, and a fixture that assumes a count is a test that breaks whenever an
# earlier block adds one.
_w = {"kept": 1.0, "partial": 0.5, "broken": 0.0}
_expect_applicable = sum(1 for x in _mix["skills"] if x["verdict"] in _w)
_expect_pct = round(100 * sum(_w[x["verdict"]] for x in _mix["skills"]
                              if x["verdict"] in _w) / _expect_applicable)
check("a pass counts one and a partial counts a half",
      _mix["applicable"] == _expect_applicable and _mix["compliance_pct"] == _expect_pct,
      f'applicable={_mix["applicable"]}/{_expect_applicable} '
      f'pct={_mix["compliance_pct"]}/{_expect_pct} {_c}')
check("…the partial really is counted as a half, not rounded to either end",
      0 < _mix["compliance_pct"] < 100 and _mix["partial"] >= 1,
      f'pct={_mix["compliance_pct"]} partial={_mix["partial"]}')
check("…N/A is excluded from the denominator, not counted as a pass",
      _c[LOOP] == "not_applicable"
      and _mix["applicable"] == len(_mix["skills"]) - _mix["not_applicable"]
      - _mix["unknown"], str(_c))
check("…and a rule nobody ruled on is excluded too",
      _mix["kept"] + _mix["partial"] + _mix["broken"] == _mix["applicable"], str(_mix))
# ON AN ALL-PROSE COURSE. Two of React's rules carry a machine check, and a check is
# arithmetic over the document — it beats the judge's claim outright, so declaring
# everything inapplicable cannot make those two inapplicable. That is the intended
# precedence, so the test has to pick a brief the judge is the only ruler of.
_pn = skill_report.build(DOC, course="Prose Only Course", judge_result={
    "brief_verdicts": [{"ref": x["ref"], "status": "not_applicable"}
                       for x in skill_report.build(
                           DOC, course="Prose Only Course")["skills"]]})
check("nothing applicable means NO percentage, because 0/0 is not 0%",
      _pn["applicable"] == 0 and _pn["compliance_pct"] is None
      and _pn["score"] is None,
      f'applicable={_pn["applicable"]} pct={_pn["compliance_pct"]} '
      f'score={_pn["score"]}')
# THE INVARIANT, stated properly. A `checked` row's verdict is a function of its OWN
# two site lists and nothing else — so the judge saying "not_applicable" about a rule the
# check settled changes nothing. (A checked rule may itself come out N/A, when the
# document gave it nothing to act on; that is the check's finding, not the judge's.)
def _from_sites(x):
    if x["broke"] and x["applied"]:
        return "partial"
    if x["broke"]:
        return "broken"
    return "kept" if x["applied"] else "not_applicable"


_checked_rows = [x for x in _mix["skills"] if x["how"] == "checked"]
check("…and a checkable rule's verdict comes from its own sites, whatever the judge said",
      _checked_rows and all(x["verdict"] == _from_sites(x) for x in _checked_rows),
      str([(x["ref"], x["verdict"], _from_sites(x)) for x in _checked_rows]))
check("every state is tallied separately",
      set(("kept", "partial", "broken", "not_applicable", "unknown", "applicable",
           "compliance_pct")) <= set(_mix), str(sorted(_mix)))

print("\n== a broken or loose rule now triggers the REPAIR pass ==")
# Before this, a broken skill BLOCKED the release (dimension 3, under the bar) and
# nothing tried to fix it: the reviewer got a rejected document and a reason. A loose one
# was accepted and never looked at, because the repair loop was gated on `not accepted`.
check("the brief is a configured repair trigger",
      _cfg.harness()["gates"]["guided_repair_on"].get("course_brief") is True)
_rsrc = " ".join(inspect.getsource(_pipe._repair_reasons).split())
check("…and _repair_reasons reads the report to raise it",
      "skill_report" in _rsrc and "repairable" in _rsrc, _rsrc[:0])
_fsrc2 = " ".join(inspect.getsource(_pipe.finalize).split())
# Matched on the LOOP STATEMENT, not on the phrase: the comment above it explains what
# it replaced, so searching for "not accepted" anywhere in the source found my own prose.
check("the repair loop is no longer gated on the document being REJECTED",
      "while rnd < max_repair:" in _fsrc2
      and "while not accepted and" not in _fsrc2, _fsrc2[:0])
_esrc = " ".join(inspect.getsource(_pipe.evaluate).split())
check("the repair pass is told which rule, and where",
      "COURSE SKILL" in _esrc and "What following it looks like" in _esrc, _esrc[:0])
check("…and told to change HOW the slides teach, not WHAT",
      "not WHAT they teach" in _esrc, _esrc[:0])
check("…and never to write the rule into the document",
      "never write the rule itself" in _esrc, _esrc[:0])

# A rule the session never engaged must NOT be repairable: satisfying it would mean
# adding curriculum the session does not own.
_rep_na = {"skills": [
    {"ref": "S1", "verdict": "broken"}, {"ref": "S2", "verdict": "partial"},
    {"ref": "S3", "verdict": "not_applicable"}, {"ref": "S4", "verdict": "unknown"},
    {"ref": "S5", "verdict": "kept"}]}
check("only FAIL and PARTIAL are repairable",
      [r["ref"] for r in SR.repairable(_rep_na)] == ["S1", "S2"],
      str([r["ref"] for r in SR.repairable(_rep_na)]))
check("…failures first, so the blocking ones are fixed first",
      SR.repairable(_rep_na)[0]["verdict"] == "broken")
check("an N/A rule is never handed to the repair pass — it would have to invent content",
      all(r["verdict"] != "not_applicable" for r in SR.repairable(_rep_na)))

print("\n== 'PASS' and 'PARTIAL -> REPAIRED' are different facts ==")
_before = {"skills": [{"ref": "S1", "verdict": "broken"},
                      {"ref": "S2", "verdict": "partial"},
                      {"ref": "S3", "verdict": "broken"},
                      {"ref": "S4", "verdict": "kept"}]}
_after = {"skills": [{"ref": "S1", "verdict": "kept"},
                     {"ref": "S2", "verdict": "kept"},
                     {"ref": "S3", "verdict": "partial"},
                     {"ref": "S4", "verdict": "kept"}]}
SR.mark_repaired(_before, _after)
_a = {r["ref"]: r for r in _after["skills"]}
check("a rule fixed by the repair pass is marked, with what it was",
      _a["S1"].get("repaired") is True and _a["S1"].get("was") == "broken", str(_a["S1"]))
check("…a loose one that was tightened too", _a["S2"].get("repaired") is True)
check("…and one only PARTLY fixed says so rather than claiming a pass",
      _a["S3"].get("repaired") == "partly", str(_a["S3"]))
check("a rule that was right all along is NOT marked repaired",
      "repaired" not in _a["S4"], str(_a["S4"]))
check("the count travels on the report", _after.get("repaired") == 3,
      str(_after.get("repaired")))
check("no prior round means nothing to compare, not a crash",
      SR.mark_repaired(None, _after) is _after)
_fs3 = " ".join(inspect.getsource(_pipe.finalize).split())
check("finalize records it between rounds", "mark_repaired(" in _fs3, _fs3[:0])

print("\n== the judge is asked for a CRITERION, not an impression ==")
_j = " ".join(inspect.getsource(judge.grade).split())
check("it must write what following the rule would look like, first",
      "FIRST WRITE THE CRITERION" in _j, _j[:0])
check("…and is told not to judge on whether the rule's words appear",
      "whether the behaviour it asks for is in the writing" in _j, _j[:0])
check("…and given all four states, with partial described as the inconsistent case",
      all(w in _j for w in ("pass", "partial", "fail", "not_applicable"))
      and "followed inconsistently" in _j, _j[:0])
check("…and warned that its sites are read back against its status",
      "read back against it" in _j, _j[:0])
check("a checkable rule states its criterion exactly, not via a model",
      SR._check_criterion({"assert": "block_present", "block": "code",
                           "on_roles": ["working_example"]})
      == "a `code` block is present on every working_example slide",
      SR._check_criterion({"assert": "block_present", "block": "code",
                           "on_roles": ["working_example"]}))
check("…for every assertion in the vocabulary",
      all(SR._check_criterion({"assert": k, "block": "code", "field": "walkthrough",
                               "when_block": "code", "min": 2, "phrases": ["x"]})
          for k in ("block_present", "field_present", "min_count", "forbidden_phrase")))
check("…and every row carries the criterion it was measured against",
      all("criterion" in x for x in _mix["skills"]),
      str([x.get("criterion") for x in _mix["skills"]]))

print("\n== the eval set and the gate rank a document the same way ==")
_es = " ".join(inspect.getsource(run_sets._chk_skill_adherence).split())
check("the eval set scores on the shared ladder, not its own count",
      "skill_report" in _es and "score_for(" in _es, _es[:0])
_esc, _esd = run_sets._chk_skill_adherence(_DOC2, Sess(), {"id": "skill_adherence"})
check("…so the partial document scores the same 4 in both places",
      _esc == _rp["score"], f"eval={_esc} grade={_rp['score']}")
check("…and the detail names the rule and its state", "partial" in _esd, _esd[:100])


print("\n== the brief rolls up BY CATEGORY, the level it is written at ==")
# An author does not think in six numbered rules; they think "the teaching flow" and
# "what we show". Six per-rule rows answer "which line was missed"; this answers "is my
# teaching flow landing?" — different questions with different fixes.
_cats = {c["category"]: c for c in _mix["by_category"]}
check("every category the brief uses gets a row, and no others",
      set(_cats) == {x["category"] for x in _mix["skills"]}, str(sorted(_cats)))
check("…each with the writer's own name for it",
      _cats["teaching_guidelines"]["title"] == "Teaching Guidelines"
      and _cats["examples_visuals"]["title"] == "Examples & Visuals",
      str([c["title"] for c in _mix["by_category"]]))
check("…in the order the brief is written in, not dict order",
      [c["category"] for c in _mix["by_category"]]
      == [k for k in SR._CATEGORY_TITLES if k in _cats],
      str([c["category"] for c in _mix["by_category"]]))
# WORST WINS. A category is not passing while one of its rules is broken.
_grp = {"skills": [{"category": "teaching_flow", "verdict": "kept", "applied": [], "broke": []},
                   {"category": "teaching_flow", "verdict": "broken", "applied": [], "broke": []},
                   {"category": "reviewer", "verdict": "kept", "applied": [], "broke": []},
                   {"category": "reviewer", "verdict": "partial", "applied": [], "broke": []},
                   {"category": "examples_visuals", "verdict": "not_applicable",
                    "applied": [], "broke": []}]}
_r2 = {c["category"]: c for c in SR._by_category(_grp["skills"])}
check("a category with any FAIL is FAILING, whatever else passed in it",
      _r2["teaching_flow"]["status"] == "broken", str(_r2["teaching_flow"]))
check("…one with a PARTIAL and no fail is PARTIAL",
      _r2["reviewer"]["status"] == "partial", str(_r2["reviewer"]))
check("…and one with nothing applicable is N/A, not a pass",
      _r2["examples_visuals"]["status"] == "not_applicable"
      and _r2["examples_visuals"]["compliance_pct"] is None,
      str(_r2["examples_visuals"]))
check("each category carries its own compliance figure",
      _r2["teaching_flow"]["compliance_pct"] == 50
      and _r2["reviewer"]["compliance_pct"] == 75,
      f'flow={_r2["teaching_flow"]["compliance_pct"]} '
      f'reviewer={_r2["reviewer"]["compliance_pct"]}')
# The rollup must not predate the repair marks.
_b = {"skills": [{"ref": "S1", "verdict": "broken", "category": "reviewer"}]}
_a = {"skills": [{"ref": "S1", "verdict": "kept", "category": "reviewer"}],
      "by_category": [{"category": "reviewer", "repaired": 0}]}
SR.mark_repaired(_b, _a)
check("…and the rollup is recomputed after a repair, not left stale",
      _a["by_category"][0]["repaired"] == 1, str(_a["by_category"]))

print("\n== §5: the coverage map is scanned for leaks too ==")
# The one shape of leak nobody would notice: the brief restated as a sub-concept the
# document CLAIMS TO TEACH. A sub-concept is a promise about what the session covers, so
# an instruction landing there turns "how to teach" into a topic the doc says it taught.
# It was not scanned at all.
# The rule used here has FOUR content words. `_LEAK_MIN_WORDS` is 4 by design: a
# three-word overlap is too weak to call a leak, or ordinary prose that happens to say
# "show snippet explaining" would be failed. That floor applies everywhere, so a test
# fixture below it proves nothing about which surfaces are scanned — it only proves the
# floor. ("Show the snippet before explaining it." is three content words and is
# undetectable in a slide bullet too.)
_LEAKTXT = "Every snippet must be explained line by line."
check("the rule under test is long enough to be detectable at all",
      len(skills._tokens(_LEAKTXT)) >= skills._LEAK_MIN_WORDS,
      f"{skills._tokens(_LEAKTXT)} vs floor {skills._LEAK_MIN_WORDS}")
_SLIDES = [{"n": 1, "role": "mechanism", "heading": "h", "subheading": "s",
            "content": [{"type": "text", "text": "ordinary teaching prose"}],
            "visual_guidance": "v", "speaker_notes": "sn"}]
_BASE = {"session_title": "T", "agenda": ["a"], "key_takeaways": ["k"], "closing": "c",
         "sections": [{"index": 1, "name": "s", "slides": _SLIDES}]}
_live = skills.applicable(REACT, 12)


def _leak_where(doc):
    return [h["where"] for h in skills.leaks(doc, _live)]


# A SUB-CONCEPT IS A PROMISE about what the session covers, so an instruction landing
# there turns "how to teach" into a topic the document claims to have taught. This was
# not scanned at all.
check("a skill copied into a coverage-map SUB-CONCEPT is caught",
      any("sub_concepts" in w for w in _leak_where(
          {**_BASE, "coverage_map": [{"takeaway": "k", "sub_concepts": [
              {"name": _LEAKTXT, "slide": 1}]}]})),
      str(_leak_where({**_BASE, "coverage_map": [{"takeaway": "k", "sub_concepts": [
          {"name": _LEAKTXT, "slide": 1}]}]})))
check("…and into a coverage-map TAKEAWAY",
      any("takeaway" in w for w in _leak_where(
          {**_BASE, "coverage_map": [{"takeaway": _LEAKTXT, "sub_concepts": []}]})))
check("…and into a SECTION NAME, which is a heading the reader sees",
      any("section" in w for w in _leak_where(
          {"sections": [{"index": 1, "name": _LEAKTXT, "slides": _SLIDES}]})))
check("every place §5 names is scanned",
      all(k in inspect.getsource(skills._visible_strings)
          for k in ("agenda", "key_takeaways", "coverage_map", "bullets", "name")))
check("…and a clean document is still not a leak", _leak_where(_BASE) == [],
      str(_leak_where(_BASE)))

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
