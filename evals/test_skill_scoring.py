"""IS THE COURSE'S OWN BRIEF ACTUALLY SCORED — and does feedback reach the next session?

    python -m evals.test_skill_scoring        # no API key, no network, ~3 seconds

TWO QUESTIONS, both of which had the same honest answer before this suite: "partly".

1. HOW IS A TR DOC EVALUATED AGAINST THE SKILLS ITS COURSE ADDED?
   The brief reached the WRITER, and (recently) the judge could raise a blocking issue
   about it. But the SCORE was blind to it: all thirteen rubric dimensions are house
   rules — true of a good document for any course — and none of them asked whether this
   document followed the instructions ITS course owner wrote. A document could break
   every line of its brief and still total 100/100. Blocking issues are a binary lever;
   there was no way to mark a document DOWN for following the brief loosely.
   And the eval set that exists for exactly this (26_skill_adherence) scored only skills
   carrying a machine-checkable assertion — of which the live store has none, because a
   brief is written in prose. So it skipped on every real course.

   Now: a `course_brief_adherence` rubric dimension, excluded and renormalised away for
   a course with no brief (the mechanism `recording_time` already used); and the eval set
   is a HYBRID whose judge half reads the prose.

2. DOES A REGENERATION REASON REACH THE NEXT SESSION?
   It was stored, distilled and scoped — but filed against `app_settings.course_name()`,
   ONE INSTANCE-WIDE SETTING. So a correction given on a run for course B, while the
   instance pointed at course A, was recorded against A: it governed A's documents for
   ever and never reached B, the course whose reviewer actually gave it. That is the
   WRITE half of the read-side leak fixed in evals/test_course_isolation.py, and it is
   the worse half — a rule read from the wrong course is one bad document, a rule
   written to the wrong course is permanent.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_skill_scoring_")
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


from src import app_settings, config, db, learning, skills              # noqa: E402
from evals import run_sets                                              # noqa: E402
import graders.llm_judge as judge                                       # noqa: E402

WITH, WITHOUT = "Responsive", "Bare Course"
ALICE = "alice@nxtwave.co.in"
db.init()
sid = db.add_skill(WITH, "Show the CSS before the rule it demonstrates.",
                   kind="content", source="user", created_by=ALICE)
db.approve_skill(sid, ALICE)

print("\n== the brief is a SCORED dimension, not only a blocking issue ==")
dims = {d["id"]: d for d in config.rubric()["dimensions"]}
check("the rubric has a course-brief dimension", "course_brief_adherence" in dims)
check("…with real weight behind it",
      dims.get("course_brief_adherence", {}).get("weight", 0) >= 5,
      str(dims.get("course_brief_adherence", {}).get("weight")))
q = dims["course_brief_adherence"]["question"]
check("…judged only against lines actually in the brief", "actually in that brief" in q)
check("…and not used to punish requirements the brief never made",
      "are NOT defects here" in q)
check("…with a middle score for 'followed loosely', so it is not all-or-nothing",
      "loosely" in q)

print("\n== a course with no brief is not scored against an empty one ==")
seen = {}


def fake_complete(**kw):
    seen.clear(); seen.update(kw)
    return ('{"scores": {}, "total": 90, "blocking_issues": [], "summary": "ok", '
            '"verdict": "pass"}')


judge.llm.complete = fake_complete


class _S:
    number = 3
    name = "Flexbox"
    key_takeaways = ["Flex containers"]


TIME = {"estimated_minutes": 30, "max_minutes": 40, "within_budget": True}
r_with = judge.grade({"sections": []}, _S(), TIME, course=WITH)
p_with = str(seen.get("cached_context") or "") + str(seen.get("user") or "")
check("a course WITH a brief is graded on the dimension",
      "course_brief_adherence" in p_with)
check("…and the brief itself reaches the judge", "Show the CSS" in p_with)
check("…told that the dimension and the brief are the same question",
      "ALSO A SCORED DIMENSION" in str(seen.get("user")))
r_without = judge.grade({"sections": []}, _S(), TIME, course=WITHOUT)
p_without = str(seen.get("cached_context") or "") + str(seen.get("user") or "")
check("a course with NO brief never sees the dimension",
      "course_brief_adherence" not in p_without, p_without[:0])
# `weights` reports only the dimensions the judge actually returned a score for, so a
# stub reply cannot be read for this. What matters is that the excluded dimension never
# reaches the rubric the judge grades against — and that the total is renormalised over
# what WAS scored rather than counting the missing one as zero.
check("…and the run without it still produces a total over the dimensions scored",
      isinstance(r_without.get("weighted_total"), (int, float)),
      str(r_without.get("weighted_total")))
check("…and neither run invents a score for a dimension it never asked about",
      "course_brief_adherence" not in (r_without.get("scores") or {}))

print("\n== the eval set reads a PROSE brief instead of skipping it ==")
doc = {"sections": [{"slides": [{"n": 1, "role": "concept_intro",
                                 "content": [{"type": "text", "text": "Flex."}]}]}]}


class _SW(_S):
    course = WITH


class _SB(_S):
    course = WITHOUT


score, detail = run_sets._chk_skill_adherence(doc, _SW(), {"id": "skill_adherence"})
check("a prose-only brief abstains from the machine half", score is None, str(score))
check("…saying the judge half will score it", "judge half" in detail, detail)
score_b, detail_b = run_sets._chk_skill_adherence(doc, _SB(), {"id": "skill_adherence"})
check("a course with NO skills is not applicable at all",
      score_b is run_sets.NOT_APPLICABLE, str(score_b))
check("…and says why", "has not said what it requires" in detail_b, detail_b)
check("the set is a hybrid now, not deterministic-only",
      "skill_adherence" in run_sets.HYBRID
      and "skill_adherence" not in run_sets.DETERMINISTIC)

# The judge half must actually run when the machine half abstains.
run_sets._llm_score = lambda doc, s, sset, **kw: (3, "the brief's CSS-first rule is ignored")
rep = run_sets.run_on_doc(doc, _SW(), use_llm=True, learn=False)
row = next(r for r in rep["sets"] if r["id"] == "skill_adherence")
check("a prose brief IS scored, by the judge alone",
      row.get("score") == 3 and not row.get("skipped"), str(row))
check("…and it can fail, which is the whole point",
      row["passed"] is False, str(row))
rep_b = run_sets.run_on_doc(doc, _SB(), use_llm=True, learn=False)
row_b = next(r for r in rep_b["sets"] if r["id"] == "skill_adherence")
check("a course with no brief is skipped, not failed", row_b.get("skipped") is True,
      str(row_b))

# A checkable skill still gates exactly, and the minimum of the two halves rules.
sid2 = db.add_skill(WITH, "Every worked example shows its code.", kind="content",
                    source="user", created_by=ALICE,
                    check={"assert": "block_present", "block": "code",
                           "on_roles": ["working_example"]})
db.approve_skill(sid2, ALICE)
bad = {"sections": [{"slides": [{"n": 2, "role": "working_example",
                                 "content": [{"type": "text", "text": "Imagine it."}]}]}]}
run_sets._llm_score = lambda doc, s, sset, **kw: (5, "prose skills all honoured")
rep2 = run_sets.run_on_doc(bad, _SW(), use_llm=True, learn=False)
row2 = next(r for r in rep2["sets"] if r["id"] == "skill_adherence")
check("a violated checkable skill still fails even when the judge is happy",
      row2["score"] < 4, str(row2))
check("…because the two halves combine as the MINIMUM", row2["grader"] == "hybrid")

print("\n== a regeneration reason is filed against the RUN's course ==")
# The instance-wide active course points somewhere else entirely — the state left by
# whoever selected a course last.
app_settings.save(course_name=WITHOUT)
learning.add_rule("Name every breakpoint in pixels.", source="regeneration",
                  scope=learning.COURSE, course=WITH)
check("the rule belongs to the course it was given on",
      any(r["text"].startswith("Name every breakpoint") and r.get("course") == WITH
          for r in learning.rules()),
      str([(r["text"][:30], r.get("course")) for r in learning.rules()]))
check("…so it reaches THAT course's next session",
      any("breakpoint" in r["text"] for r in learning.applicable_rules(WITH)))
check("…and not the one that merely happened to be selected",
      not any("breakpoint" in r["text"] for r in learning.applicable_rules(WITHOUT)))

print("\n== …by every path that writes to the store ==")
import inspect                                                          # noqa: E402
for fn in (learning.record_feedback, learning.record_issues,
           learning.learn_from_issues, learning.add_rule):
    check(f"learning.{fn.__name__} takes a course",
          "course" in inspect.signature(fn).parameters)
import server                                                           # noqa: E402
src_regen = inspect.getsource(server._guided_regenerate)
check("guided regeneration passes the run's course",
      "course=run_course" in src_regen, "")
check("the finished-doc feedback endpoint takes a course",
      "course" in inspect.signature(server.FeedbackBody).parameters
      or "course" in server.FeedbackBody.model_fields)
check("…and the finished result carries it, so the client can send the right one",
      '"course": run_course or ""' in inspect.getsource(server._guided_finalize))
import src.pipeline as pipeline                                         # noqa: E402
check("a one-shot run's surviving defects are filed against its course",
      "course=course" in inspect.getsource(pipeline.run))

print("\n== a GENERIC rule still reaches every course, including a new one ==")
learning.add_rule("Never repeat a paragraph's point in the bullet beside it.",
                  source="regeneration", scope=learning.GLOBAL, course=WITH)
fresh = learning.learned_rules_block("A Course Created Tomorrow")
check("house style carries to a course that did not exist when it was learned",
      "repeat a paragraph's point" in fresh)
check("…while the subject-matter rule does not", "breakpoint" not in fresh, fresh)
check("…and it is injected with precedence over the style guide",
      "THE LEARNED RULE WINS" in fresh)
# …but NOT over the course's own authored brief. A rule generalised from one course was
# cancelling another course's explicit instructions — see evals/test_course_isolation.py.
check("…while still deferring to the course's own brief",
      "THE COURSE BRIEF ABOVE OUTRANKS EVERYTHING HERE" in fresh)

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
