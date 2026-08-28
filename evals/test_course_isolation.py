"""WHICH COURSE'S RULES A DOCUMENT IS WRITTEN AND GRADED UNDER.

    python -m evals.test_course_isolation      # no API key, no network, ~2 seconds

WHY THIS EXISTS. Everything about a run is resolved per run — the curriculum, the decks,
the profile, the prerequisites, the budgets — except two things, and they were the two
that decide how the document is WRITTEN:

  · `generator._learned()` asked for the rules block with no course, and
  · `graders/llm_judge.grade()` asked for it with no course,

both of which fall back to `app_settings.course_name()`: ONE instance-wide setting, set
by whoever selected a course last. Two people on one instance is all it takes. A document
generated for 'Responsive' while the instance pointed at 'Operating Systems' was written
under OS's subject-matter rules — "use 'cluster' instead of 'block'" — and never saw
Responsive's own authored brief at all; then the judge verified it against the same wrong
set and could report a blocking issue for breaking another course's rule. Nothing about
the output looks wrong. It is simply the wrong course's document.

The split this suite pins down:
  · a GLOBAL rule (house style) applies to every course, including one created tomorrow;
  · a COURSE rule applies only within its own course;
  · a course's SKILLS are its own, and reach both the writer and the judge as a brief
    labelled as authored — not as something inferred from corrections.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_course_isolation_")
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


from src import app_settings, db, generator, learning, skills          # noqa: E402

OS_C, WEB = "Operating Systems", "Responsive"
ALICE = "alice@nxtwave.co.in"
db.init()

# The instance-wide "active course" points at OS — the state left behind by whoever
# selected a course last. Every assertion below is about a run for WEB in spite of it.
app_settings.save(course_name=OS_C)

learning.add_rule("Never repeat a paragraph's point in the bullet beside it.",
                  source="regeneration", scope=learning.GLOBAL, course=OS_C)
learning.add_rule("Use 'cluster' instead of 'block' throughout.",
                  source="regeneration", scope=learning.COURSE, course=OS_C)
learning.add_rule("Name every breakpoint in pixels.",
                  source="regeneration", scope=learning.COURSE, course=WEB)

for c, text in ((OS_C, "Trace every scheduling example by hand."),
                (WEB, "Show the CSS before the rule it demonstrates.")):
    sid = db.add_skill(c, text, kind="content", source="user", created_by=ALICE)
    db.approve_skill(sid, ALICE)

print("\n== the rules block for a course is that course's ==")
web = learning.learned_rules_block(WEB)
check("the house-style rule is in it", "repeat a paragraph's point" in web)
check("this course's own rule is in it", "breakpoint in pixels" in web)
check("the OTHER course's subject-matter rule is NOT",
      "cluster" not in web, web)
check("this course's brief is in it", "Show the CSS" in web)
check("the other course's brief is NOT", "scheduling example" not in web, web)

print("\n== …and the generator asks for it BY COURSE ==")
check("_learned(course) is scoped to that course",
      "breakpoint in pixels" in generator._learned(WEB)
      and "cluster" not in generator._learned(WEB))
check("_learned() with no course still falls back to the active one — the old "
      "behaviour, kept for callers outside a run",
      "cluster" in generator._learned())
# The signatures are the contract: every generator entry point must be able to say which
# course it is writing for, or the fallback is all it can ever use.
import inspect                                                        # noqa: E402
for fn in (generator.generate, generator.generate_chunk, generator.generate_patch,
           generator.repair_patch, generator.revise):
    check(f"generator.{fn.__name__} takes a course",
          "course" in inspect.signature(fn).parameters)

print("\n== a NEW course inherits the house style and nothing else ==")
fresh = learning.learned_rules_block("A Course Created Today")
check("the house-style rule applies to it", "repeat a paragraph's point" in fresh)
check("no other course's subject matter does",
      "cluster" not in fresh and "breakpoint" not in fresh, fresh)
check("and it has no brief of its own yet", "Show the CSS" not in fresh)

print("\n== the brief and the learned rules are separable, and labelled apart ==")
only_rules = learning.rules_block(WEB)
check("rules_block carries the rules", "breakpoint in pixels" in only_rules)
check("…and NOT the brief", "Show the CSS" not in only_rules, only_rules)
check("the brief says who wrote it",
      "authored by the person who owns this course" in skills.block(WEB).lower(),
      skills.block(WEB)[:200])
# The judge preamble calls its rules "learned from corrections a human made". A course
# with a brief and no corrections yet would have had its author's own instructions
# introduced that way.
brief_only = learning.learned_rules_block("A Course Created Today")
db.approve_skill(db.add_skill("A Course Created Today", "Keep every example runnable.",
                              kind="content", source="user", created_by=ALICE), ALICE)
brief_only = learning.learned_rules_block("A Course Created Today")
check("a course with a brief and no learned rules of its own still gets its brief",
      "Keep every example runnable" in brief_only)
# The two halves must not be introduced by one heading. The brief comes FIRST, under its
# own; the reviewer heading introduces only what follows it — which is the house-style
# rule, and nothing of this course's author's.
check("…and the learned-rules heading comes AFTER it, introducing only the rules",
      brief_only.index("Keep every example runnable")
      < brief_only.index("RULES LEARNED"), brief_only)
check("…so nothing the author wrote sits under a heading calling it inferred",
      "Keep every example runnable"
      not in brief_only[brief_only.index("RULES LEARNED"):], brief_only)

print("\n== a stored rule with no instruction in it is dropped ==")
learning.add_rule("SCOPE: course", source="regeneration", scope=learning.COURSE,
                  course=WEB, raw="Missing: Partitions & Volumes and VFS")
check("it is in the store", any(r["text"] == "SCOPE: course" for r in learning.rules()))
check("but it is NEVER injected",
      "SCOPE: course" not in learning.learned_rules_block(WEB))
check("the sweep removes it", learning.drop_contentless() == 1)
check("…and it is gone", not any(r["text"] == "SCOPE: course" for r in learning.rules()))
check("…while the real rules stay",
      len([r for r in learning.rules() if "breakpoint" in r["text"]]) == 1)
check("running the sweep again does nothing", learning.drop_contentless() == 0)

print("\n== the course's OWN brief outranks a rule learned somewhere else ==")
# The conflict this settles, observed live. A note on an Operating Systems session
# ("working examples are not needed for this topic") was generalised to "Remove working
# code examples; rely on pseudocode" and classified as house style — so it was injected
# into a Responsive Web Design course whose author had written a brief asking for code
# snippets and syntax throughout. Both blocks claimed precedence, the learned one called
# itself "highest priority", and the course's own instructions lost to a generalisation
# drawn from a different subject. That is the reported symptom: "the skills I added are
# not reflected in the generated doc".
blk = learning.learned_rules_block(WEB)
check("the brief comes first", blk.index("HOW '") < blk.index("RULES LEARNED"), "")
check("…and the rules block defers to it explicitly",
      "THE COURSE BRIEF ABOVE OUTRANKS EVERYTHING HERE" in blk, blk[:0])
check("…telling the writer to follow the brief, not split the difference",
      "Do not try to satisfy both by half-doing each" in blk)
check("the learned block no longer calls itself the highest priority",
      "highest priority" not in blk, blk[:0])
check("a rule learned on ANOTHER course says so",
      "learned on 'Operating Systems', not this course" in blk, blk[:0])
check("…while this course's own rule carries no such caveat",
      "Name every breakpoint in pixels.  [" not in blk
      or "breakpoint in pixels.  [learned on" not in blk, blk[:0])

print("\n== a misclassified rule can be MOVED, not only destroyed ==")
# The house/course call is made by a model and it misjudges — the live store has a note
# about one topic ("working examples are not needed for this topic") standing as house
# style over every course. DELETE was the only lever, and it is the wrong one: it also
# takes the rule away from the course that did ask for it.
import server                                                          # noqa: E402
from fastapi import HTTPException                                      # noqa: E402

ADMIN = {"email": ALICE, "is_admin": True}


def rescope(i, scope, course=None):
    """(status, payload) from the endpoint, called directly — no HTTP needed to test
    what it does with the store."""
    try:
        return 200, server.set_learned_rule_scope(
            i, server.RuleScopeBody(scope=scope, course=course), user=ADMIN)
    except HTTPException as e:
        return e.status_code, e.detail


idx = next(i for i, r in enumerate(learning.rules())
           if r["text"].startswith("Never repeat a paragraph"))
st, _ = rescope(idx, "course", OS_C)
check("narrowing a house rule to one course -> 200", st == 200, str(st))
check("…it now applies to that course",
      "repeat a paragraph's point" in learning.learned_rules_block(OS_C))
check("…and to no other",
      "repeat a paragraph's point" not in learning.learned_rules_block(WEB))
check("…the wording is untouched",
      any(x["text"] == "Never repeat a paragraph's point in the bullet beside it."
          for x in learning.rules()))
st, _ = rescope(idx, "global")
check("promoting it back -> 200", st == 200, str(st))
check("…and it is house style again for every course",
      "repeat a paragraph's point" in learning.learned_rules_block(WEB)
      and "repeat a paragraph's point" in learning.learned_rules_block("Anything"))
check("a scope that is neither -> 400", rescope(idx, "sometimes")[0] == 400)
check("a rule that does not exist -> 404", rescope(999, "global")[0] == 404)
# A course rule with nowhere to live would vanish from every prompt without a word.
learning.add_rule("An orphan rule.", source="judge", scope=learning.GLOBAL, course="")
orphan = next(i for i, r in enumerate(learning.rules())
              if r["text"] == "An orphan rule.")
data = learning._load(); data["rules"][orphan]["course"] = ""; learning._save(data)
check("narrowing a rule with no course, and none given -> 400",
      rescope(orphan, "course")[0] == 400)

print("\n== the judge is given THIS course's brief and rules ==")
import graders.llm_judge as judge                                     # noqa: E402


class _Session:
    number = 3
    name = "Flexbox"
    key_takeaways = ["Flex containers", "Flex items"]


captured = {}


def _fake_complete(**kw):
    captured.update(kw)
    return ('{"scores": {}, "total": 90, "blocking_issues": [], '
            '"summary": "ok", "verdict": "pass"}')


judge.llm.complete = _fake_complete
try:
    judge.grade({"sections": []}, _Session(), {"estimated_minutes": 30,
                                               "max_minutes": 40,
                                               "within_budget": True},
                course=WEB)
except Exception as e:
    print(f"  (judge returned {e!r} — only the prompt matters here)")
prompt = str(captured.get("user") or "")
check("the judge sees this course's brief", "Show the CSS" in prompt)
check("…under a heading that says it was AUTHORED, not inferred",
      "THE COURSE'S OWN BRIEF" in prompt, prompt[:0])
check("the judge sees this course's rule", "breakpoint in pixels" in prompt)
check("…and NOT the other course's subject matter", "cluster" not in prompt)
check("…nor the other course's brief", "scheduling example" not in prompt)

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
