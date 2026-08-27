"""Per-course configuration: the same document, two courses, two verdicts.

    python -m evals.test_course_profile        # no API key needed, ~2 seconds

WHY THIS EXISTS. Everything about what a good TR doc looks like was ONE set of numbers
in harness.yaml, applied to every course on the instance — and several of them are
plainly about Computer Networks:

  · `market_reference_platforms` is Scaler, GeeksforGeeks, TutorialsPoint, JavaTpoint and
    "standard university CN/CS syllabi". A React document is graded for market parity
    against a networking syllabus.
  · `slide_roles.values` has no role for a code walkthrough — and the analogy rule is a
    BICONDITIONAL keyed on those roles, so a course cannot add one without saying what
    the analogy rule for it is.
  · `content.min_slides_with_text_share`, `min_bullet_items` and the text-block caps set
    one prose density for a theory course and a code-along alike.
  · the rubric's weights and pass bar are the same for a course whose worked examples are
    address translations and one whose worked examples are snippets.

A course profile overrides those, per course, from the database — and the property that
matters most is the LAST section here: a course with no profile behaves exactly as the
instance does today.

Deliberately NOT overridable, and asserted: a course may redistribute rubric weight but
may not lower the pass bar. A gate a course can switch off is a gate that gets switched
off.
"""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_profile_test_")
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


from src import config, course_loader, db, profiles                # noqa: E402
from guardrails import guardrails                                  # noqa: E402

db.init()
GOLDEN = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())
cur = course_loader.get_session(
    15, course_loader.load_sessions(ROOT / "Final CN Structure.xlsx"))

CN = "Computer Networks"
REACT = "React Fundamentals"

print("\n== a course with no profile gets the harness, exactly as today ==")
d = profiles.for_course(CN)
h = config.harness()
check("the source says so", d["source"] == "harness default", str(d["source"]))
check("market platforms are the harness's",
      d["market_reference_platforms"] == h["market_reference_platforms"],
      str(d["market_reference_platforms"]))
check("the slide roles are the harness's",
      d["slide_roles"]["values"] == h["constraints"]["slide_roles"]["values"],
      str(d["slide_roles"]["values"]))
check("…and so is the pass bar",
      d["gates"]["rubric_min_total"] == h["gates"]["rubric_min_total"],
      str(d["gates"]))
check("no course at all still resolves", profiles.for_course(None)["source"]
      == "harness default")

print("\n== a course can name the platforms it is measured against ==")
db.set_course_profile(REACT, {
    "market_reference_platforms": ["react.dev", "MDN Web Docs", "Kent C. Dodds"],
    "doc_kind": "code_along",
})
r = profiles.for_course(REACT)
check("the React course names its own",
      r["market_reference_platforms"] == ["react.dev", "MDN Web Docs", "Kent C. Dodds"],
      str(r["market_reference_platforms"]))
check("…and is no longer graded against a networking syllabus",
      not any("CN" in p for p in r["market_reference_platforms"]),
      str(r["market_reference_platforms"]))
check("the other course is untouched",
      profiles.for_course(CN)["market_reference_platforms"]
      == h["market_reference_platforms"], str(profiles.for_course(CN)))
check("the source names the level it came from", r["source"] == "course profile",
      str(r["source"]))
check("…and unset keys still fall through to the harness",
      r["gates"]["rubric_min_total"] == h["gates"]["rubric_min_total"],
      str(r["gates"]))

print("\n== a course can add a slide role, and must declare its analogy rule ==")
db.set_course_profile(REACT, {
    "market_reference_platforms": ["react.dev"],
    "slide_roles": {"values": list(h["constraints"]["slide_roles"]["values"])
                    + ["code_walkthrough"]},
    "analogy": {"banned_on_roles": list(h["constraints"]["analogy"]["banned_on_roles"])
                + ["code_walkthrough"]},
})
r = profiles.for_course(REACT)
check("the new role is in the vocabulary",
      "code_walkthrough" in r["slide_roles"]["values"], str(r["slide_roles"]["values"]))
check("…and the analogy rule covers it",
      "code_walkthrough" in r["analogy"]["banned_on_roles"], str(r["analogy"]))
# The gate is what proves it: the role is only real if guardrails accept it.
d2 = copy.deepcopy(GOLDEN)
d2["sections"][0]["slides"][0]["role"] = "code_walkthrough"
d2["sections"][0]["slides"][0].pop("analogy", None)
r_cn = guardrails.check(d2, cur, False, False, profile=profiles.for_course(CN))
r_rx = guardrails.check(d2, cur, False, False, profile=profiles.for_course(REACT))
check("the CN course rejects the unknown role",
      any("code_walkthrough" in f and "not one of" in f for f in r_cn.failures),
      "; ".join(r_cn.failures)[:180])
check("…and the React course accepts it",
      not any("code_walkthrough" in f and "not one of" in f for f in r_rx.failures),
      "; ".join(r_rx.failures)[:180])
# A role nobody declared an analogy rule for is a hole in the biconditional.
check("a role added without an analogy rule is refused",
      not db.set_course_profile("Broken Course", {
          "slide_roles": {"values": list(h["constraints"]["slide_roles"]["values"])
                          + ["mystery_role"]}}),
      "adding a role must require saying whether an analogy belongs on it")

print("\n== a course can set its own prose density ==")
TIGHT = "Tight Course"
db.set_course_profile(TIGHT, {"content": {"min_bullet_items": 2}})
two_items = copy.deepcopy(GOLDEN)
two_items["sections"][0]["slides"][0]["content"] = [
    {"type": "text", "text": "A framing line."},
    {"type": "bullets", "items": ["first point", "second point"]}]
strict = guardrails.check(two_items, cur, False, False, profile=profiles.for_course(CN))
loose = guardrails.check(two_items, cur, False, False,
                         profile=profiles.for_course(TIGHT))
check("the default course rejects a two-item list",
      any("min 3" in f for f in strict.failures), "; ".join(strict.failures)[:160])
check("…and a course that allows two does not",
      not any("min 2" in f or "min 3" in f for f in loose.failures),
      "; ".join(loose.failures)[:160])

print("\n== weights may be redistributed; the pass bar may not be lowered ==")
db.set_course_profile(REACT, {"rubric_weights": {"example_quality": 12,
                                                 "analogy_discipline": 2}})
r = profiles.for_course(REACT)
check("a weight can be raised", r["rubric_weights"].get("example_quality") == 12,
      str(r["rubric_weights"]))
check("…and another lowered", r["rubric_weights"].get("analogy_discipline") == 2,
      str(r["rubric_weights"]))
check("a dimension that does not exist is refused",
      not db.set_course_profile("Bad Weights", {"rubric_weights": {"vibes": 9}}),
      "a weight must name a real rubric dimension")
floor = h["gates"]["rubric_min_total"]
check("the pass bar cannot be lowered",
      not db.set_course_profile("Soft Course",
                                {"gates": {"rubric_min_total": floor - 20}}),
      f"a course must not be able to drop the bar below {floor}")
check("…but it CAN be raised",
      db.set_course_profile("Strict Course",
                            {"gates": {"rubric_min_total": floor + 5}}),
      "a course may hold itself to more")
check("…and that is what applies",
      profiles.for_course("Strict Course")["gates"]["rubric_min_total"] == floor + 5,
      str(profiles.for_course("Strict Course")["gates"]))

print("\n== only known keys are accepted ==")
check("an unknown top-level key is refused",
      not db.set_course_profile("Odd Course", {"make_it_good": True}),
      "the profile is a closed whitelist, not arbitrary config")
check("…and nothing was stored for it",
      db.course_profile("Odd Course") == {}, str(db.course_profile("Odd Course")))

print("\n== course_type moves off the instance-wide setting ==")
# It was ONE value in app_settings for the whole instance, so two courses shared it.
db.set_course_profile("Interview Course", {"course_type": "interview"})
check("a course carries its own type",
      profiles.for_course("Interview Course")["course_type"] == "interview",
      str(profiles.for_course("Interview Course")["course_type"]))
check("…and another course is unaffected",
      profiles.for_course(CN)["course_type"] == "semester",
      str(profiles.for_course(CN)["course_type"]))
check("an unknown course type is refused",
      not db.set_course_profile("Weird Course", {"course_type": "interpretive dance"}))

print("\n== THE REGRESSION THAT MATTERS: no profile == today ==")
# Everything above is new capability. This is the one that says the capability costs
# nothing: an instance that sets no profile grades exactly as it did before.
before = guardrails.check(GOLDEN, cur, False, False)
after = guardrails.check(GOLDEN, cur, False, False, profile=profiles.for_course(CN))
check("the golden's failures are identical with and without a profile",
      sorted(before.failures) == sorted(after.failures),
      f"{sorted(set(before.failures) ^ set(after.failures))}")
check("…and its warnings too",
      sorted(before.warnings) == sorted(after.warnings),
      f"{sorted(set(before.warnings) ^ set(after.warnings))}")
check("…and the verdict", before.passed == after.passed)

print("\n== deleting a course takes its profile with it ==")
# Every per-course table has to be in delete_course, or each deleted course leaves a row
# behind — which is exactly how the orphaned team_courses rows accumulated.
db.set_course_profile("Doomed Profile Course", {"course_type": "interview"})
check("it is stored", db.course_profile("Doomed Profile Course") != {})
db.delete_course("Doomed Profile Course")
check("…and gone with the course", db.course_profile("Doomed Profile Course") == {},
      str(db.course_profile("Doomed Profile Course")))
check("…so it resolves to the harness again",
      profiles.for_course("Doomed Profile Course")["course_type"] == "semester")

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
