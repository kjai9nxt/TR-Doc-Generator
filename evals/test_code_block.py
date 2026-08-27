"""The `code` content block: can a TR doc show code at all, and is it measured?

    python -m evals.test_code_block        # no API key needed, ~2 seconds

WHY THIS EXISTS. A slide's content could be `text`, `bullets` or `table` and nothing
else — in the format spec, in docx_writer, and in the guardrails. A course about code
therefore could not put code on a slide, and no amount of prompting or per-course
instruction changes that: the schema had nowhere to put it.

Adding the block is the easy half. The half that matters is that everything measuring a
document keeps working:

  · PAGES. Code is monospace, one line per line, and does not wrap like prose. Measured
    as prose it is wildly wrong in both directions — a 20-line snippet of short lines
    reads as one short paragraph — and the page ceiling is a hard gate.
  · RECORDING TIME. An instructor does not narrate code at 130 words a minute; they read
    a line and explain it. Counting the characters as spoken words inflates the estimate
    until a legitimate code slide cannot fit the budget.
  · THE PROSE/BULLET MIX GATE. 60% of slides must carry a prose `text` block. A code
    slide's framing prose is its WALKTHROUGH, so a code-heavy document would otherwise
    fail a gate about writing style for being about code.

Everything here is deterministic — no API key, no network.
"""
import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK = FAIL = 0


def check(name, cond, extra=""):
    global OK, FAIL
    if cond:
        OK += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        print(f"  FAIL {name} {extra}")


from src import config, course_loader, docx_writer                # noqa: E402
from guardrails import guardrails                                 # noqa: E402
from graders import page_grader, time_grader                      # noqa: E402

GOLDEN = json.loads((ROOT / "evals/golden/session_15_golden.json").read_text())
sessions = course_loader.load_sessions(ROOT / "Final CN Structure.xlsx")
cur = course_loader.get_session(15, sessions)

CODE = "\n".join([
    "function Counter() {",
    "  const [count, setCount] = useState(0)",
    "  return <button onClick={() => setCount(count + 1)}>{count}</button>",
    "}",
])
CODE_BLOCK = {
    "type": "code", "language": "jsx", "code": CODE,
    "walkthrough": [
        {"lines": "2", "text": "useState returns the current value and its setter."},
        {"lines": "3", "text": "The click handler passes the next value to the setter."},
    ],
}


def with_code(slide_idx: int = 0, block: dict | None = None) -> dict:
    d = copy.deepcopy(GOLDEN)
    sl = d["sections"][0]["slides"][slide_idx]
    sl["content"] = [dict(block or CODE_BLOCK)]
    return d


print("\n== the spec allows it ==")
spec = (ROOT / "harness/format_spec.md").read_text()
check("the format spec documents a code block", '"type": "code"' in spec, spec[:0])
check("…with a language", '"language"' in spec)
check("…and a line-by-line walkthrough", "walkthrough" in spec)

print("\n== it renders, in both outputs ==")
md = docx_writer.chunk_to_markdown("section", {"section": {
    "name": "Hooks", "index": 1,
    "slides": [{"n": 1, "title": "Counter", "heading": "h", "subheading": "s",
                "content": [CODE_BLOCK], "visual_guidance": "v",
                "speaker_notes": "n."}]}})
check("the markdown fences the code", "```jsx" in md, md[:200])
check("…keeping every line", all(line in md for line in CODE.splitlines()), md[:300])
check("…and shows the walkthrough", "useState returns the current value" in md, md[:400])
check("…naming the lines it explains", "2" in md and "3" in md)
import tempfile                                                   # noqa: E402
out = Path(tempfile.mkdtemp()) / "code.docx"
docx_writer.write_docx(with_code(), out)
check("the .docx is written", out.exists() and out.stat().st_size > 0)

print("\n== PAGES: code is measured as code, not as a paragraph ==")
# The same characters as prose vs as code must not cost the same. Twenty short lines of
# code occupy twenty lines on the page; as one prose paragraph they occupy two.
LONG = "\n".join(f"  const value{i} = compute({i})" for i in range(20))
as_code = with_code(block={"type": "code", "language": "js", "code": LONG,
                           "walkthrough": [{"lines": "1", "text": "Set up the values."}]})
as_prose = with_code(block={"type": "text", "text": LONG.replace("\n", " ")})
p_code = page_grader.estimate(as_code)["estimated_pages"]
p_prose = page_grader.estimate(as_prose)["estimated_pages"]
check("twenty lines of code cost more page than the same text as one paragraph",
      p_code > p_prose, f"code={p_code} prose={p_prose}")
base = page_grader.estimate(GOLDEN)["estimated_pages"]
check("…and a code slide costs more than the slide it replaced",
      page_grader.estimate(with_code())["estimated_pages"] >= base,
      f"with_code={page_grader.estimate(with_code())['estimated_pages']} base={base}")
check("the page breakdown names the code",
      "code" in page_grader.estimate(as_code)["pages_by_part"],
      str(page_grader.estimate(as_code)["pages_by_part"]))

print("\n== TIME: a line of code costs a beat, not its word count ==")
# What the per-line model is FOR. Explaining `const x = f(1)` and explaining
# `const accountBalanceAfterFees = computeBalance(account, feeSchedule)` take about the
# same breath — one line, one beat. Counting the identifiers as spoken words makes the
# second cost three times the first, so a doc's recording estimate would swing on how
# verbosely its variables were named, which is not a fact about how long it takes to
# teach. The walkthrough IS counted as ordinary prose, because it is prose.
TERSE = "\n".join(f"const a{i} = f({i})" for i in range(10))
VERBOSE = "\n".join(
    f"const accountBalanceAfterFees{i} = computeRunningBalance(account, feeSchedule, {i})"
    for i in range(10))
walk = [{"lines": "1-10", "text": "Each line computes one running balance."}]
e_terse = time_grader.estimate(with_code(block={"type": "code", "language": "js",
                                                "code": TERSE, "walkthrough": walk}))
e_verbose = time_grader.estimate(with_code(block={"type": "code", "language": "js",
                                                  "code": VERBOSE, "walkthrough": walk}))
check("two snippets of the same length cost the same to narrate",
      e_terse["spoken_words"] == e_verbose["spoken_words"],
      f"terse={e_terse['spoken_words']} verbose={e_verbose['spoken_words']}")
# NOTE the honest scope of this. Graded as prose the two would ALSO come out close,
# because the word count splits on whitespace and a long identifier is still one token —
# so the per-line allowance is not fixing an inflation bug in the word model. It is a
# modelling choice: a line is a beat. The place code was genuinely mismeasured is PAGES,
# where it is character-width and wrapping that matter, and that is asserted above.
check("a longer snippet costs more than a shorter one",
      time_grader.estimate(with_code(block={
          "type": "code", "language": "js", "code": TERSE, "walkthrough": []}))["spoken_words"]
      > time_grader.estimate(with_code(block={
          "type": "code", "language": "js", "code": "const a = 1",
          "walkthrough": []}))["spoken_words"],
      "ten lines must cost more than one")
bare = time_grader.estimate(with_code(block={"type": "code", "language": "js",
                                             "code": "x = 1", "walkthrough": []}))
walked = time_grader.estimate(with_code())
check("…and the walkthrough is spoken on top of the code",
      walked["spoken_words"] > bare["spoken_words"],
      f"walked={walked['spoken_words']} bare={bare['spoken_words']}")
check("blank lines in a snippet are not narrated",
      time_grader.estimate(with_code(block={
          "type": "code", "language": "js", "code": "a = 1\n\n\n\nb = 2",
          "walkthrough": []}))["spoken_words"]
      == time_grader.estimate(with_code(block={
          "type": "code", "language": "js", "code": "a = 1\nb = 2",
          "walkthrough": []}))["spoken_words"],
      "blank lines cost nothing to say")

print("\n== the prose/bullet mix gate understands a code slide ==")
# 60% of slides must carry prose. A code slide's framing prose is its walkthrough, so a
# code-heavy doc must not fail a gate about writing style for being about code.
# EVERY slide, not just the first section's: the gate is a share of the whole document,
# and stripping two of eight slides leaves it comfortably above the 60% bar.
allcode = copy.deepcopy(GOLDEN)
for sec in allcode["sections"]:
    for sl in sec["slides"]:
        sl["content"] = [dict(CODE_BLOCK)]
r = guardrails.check(allcode, cur, False, False)
check("a walkthrough counts as the slide's prose",
      not any("carry a prose" in f for f in r.failures),
      "; ".join(f for f in r.failures if "prose" in f)[:200])
nowalk = copy.deepcopy(allcode)
for sec in nowalk["sections"]:
    for sl in sec["slides"]:
        sl["content"] = [{"type": "code", "language": "js", "code": "x = 1",
                          "walkthrough": []}]
r2 = guardrails.check(nowalk, cur, False, False)
check("…but bare code with no walkthrough does NOT",
      any("carry a prose" in f for f in r2.failures),
      "; ".join(r2.failures)[:200])

print("\n== a code block has to be well formed ==")
bad = with_code(block={"type": "code", "walkthrough": [], "code": ""})
check("empty code fails",
      any("code" in f.lower() and "empty" in f.lower()
          for f in guardrails.check(bad, cur, False, False).failures),
      "; ".join(guardrails.check(bad, cur, False, False).failures)[:200])
nolang = with_code(block={"type": "code", "code": "x = 1", "walkthrough": []})
check("a code block with no language fails",
      any("language" in f.lower()
          for f in guardrails.check(nolang, cur, False, False).failures),
      "; ".join(guardrails.check(nolang, cur, False, False).failures)[:200])
badref = with_code(block={"type": "code", "language": "js", "code": "x = 1",
                          "walkthrough": [{"lines": "7", "text": "nope"}]})
check("a walkthrough pointing at a line that is not there fails",
      any("walkthrough" in f.lower()
          for f in guardrails.check(badref, cur, False, False).failures),
      "; ".join(guardrails.check(badref, cur, False, False).failures)[:200])

print("\n== code does not trip the gates written for prose ==")
# The word/sentence caps are about slide text, not about a snippet that happens to be
# long; and a `for (int i = 0; i < n; i++)` is not a second-person address.
longcode = with_code(block={
    "type": "code", "language": "js",
    "code": "\n".join(f"const reallyQuiteLongVariableName{i} = compute({i})"
                      for i in range(12)),
    "walkthrough": [{"lines": "1-12", "text": "Each line computes one value."}]})
r3 = guardrails.check(longcode, cur, False, False)
check("a long snippet does not fail the text-block word cap",
      not any("this is slide text, not prose" in f for f in r3.failures),
      "; ".join(r3.failures)[:200])
youcode = with_code(block={
    "type": "code", "language": "js", "code": "// you can call this twice\nrun()",
    "walkthrough": [{"lines": "2", "text": "The function runs once per click."}]})
check("second person inside a code comment is not a banned-phrase failure",
      not any("second person" in f for f in
              guardrails.check(youcode, cur, False, False).failures),
      "; ".join(guardrails.check(youcode, cur, False, False).failures)[:200])
youwalk = with_code(block={
    "type": "code", "language": "js", "code": "run()",
    "walkthrough": [{"lines": "1", "text": "You should call this once per click."}]})
check("…but second person in the WALKTHROUGH is, because a learner reads it",
      any("second person" in f for f in
          guardrails.check(youwalk, cur, False, False).failures),
      "; ".join(guardrails.check(youwalk, cur, False, False).failures)[:200])

print("\n== the golden document is unaffected ==")
check("a doc with no code block grades exactly as before",
      guardrails.check(GOLDEN, cur, False, False).passed
      or not any("code" in f.lower() for f in
                 guardrails.check(GOLDEN, cur, False, False).failures),
      "; ".join(guardrails.check(GOLDEN, cur, False, False).failures)[:200])

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
