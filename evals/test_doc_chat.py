"""ASKING THE AGENT WHY — the evidence a question is answered from, and the limits.

    python -m evals.test_doc_chat          # no API key, no network, ~2 seconds

WHY THIS EXISTS. The reviewer could already reject a section with a reason. They could
not ASK about one. So the only move available while they were still working out whether
something was wrong was to reject it and re-roll — and a disagreement that turned out not
to be a disagreement cost a full regeneration each time.

The whole feature turns on one property, and it is the property this suite exists to
hold: THE ANSWER IS BUILT FROM THE INPUTS, NOT FROM THE MODEL'S MEMORY OF WRITING. A
model asked why it wrote something will answer fluently whether or not it knows, and a
confident invented reason is worse than no answer — the reviewer approves a document
believing it had a basis it never had, and the two are further apart than before they
spoke.

So what is asserted here is mostly about the PACK: that the curriculum line, the deck
slides, the prerequisites, the brief, the learned rules, the standing notes and the
budget all reach the model, that retrieval follows the QUESTION rather than a fixed
query, and that the things the writer never had are stated rather than left to be
guessed at. Plus the two safety properties: a question cannot change the document, and a
failed answer never costs the reviewer their question.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = tempfile.mkdtemp(prefix="tr_doc_chat_")
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


from src import db, doc_chat, learning, pptx_ingest, skills            # noqa: E402
from src.course_loader import Session                                  # noqa: E402

COURSE = "Operating Systems"
ALICE = "alice@nxtwave.co.in"
db.init()

# A course with real material behind it: two earlier decks, a brief, a learned rule.
pptx_ingest.put_deck(COURSE, 1, {
    "session_no": 1, "deck_title": "Files", "n_slides": 2,
    "slides": [
        {"n": 1, "title": "Contiguous Allocation",
         "body": "A file occupies consecutive blocks. Fast sequential reads."},
        {"n": 2, "title": "Linked Allocation",
         "body": "Each block points at the next. No external fragmentation."},
    ]})
pptx_ingest.put_deck(COURSE, 2, {
    "session_no": 2, "deck_title": "Scheduling", "n_slides": 1,
    "slides": [{"n": 1, "title": "Round Robin",
                "body": "Each process gets a fixed time quantum in turn."}]})
sid = db.add_skill(COURSE, "Trace every scheduling example by hand.",
                   kind="content", source="user", created_by=ALICE)
db.approve_skill(sid, ALICE)
learning.add_rule("Never repeat a paragraph's point in the bullet beside it.",
                  source="regeneration", scope=learning.GLOBAL, course=COURSE)

CUR = Session(number=5, name="File Allocation", module="Storage", topic="Files",
              key_takeaways=["The three file allocation methods and their trade-offs",
                             "How the FAT locates a file's blocks"])

STATE = {
    "course": COURSE, "session_no": 5, "session_title": "File Allocation",
    "cur": CUR, "total": 3, "enforce_time": True,
    "budgets": {"max_pages": 26, "max_slides": 21},
    "labels": ["Opening (recap + agenda)",
               "Key takeaway 1: The three file allocation methods",
               "Key takeaway 2: How the FAT locates a file's blocks"],
    "chunks": [
        {"markdown": "## Recap\nLast time: scheduling."},
        {"markdown": "## Allocation\nContiguous, linked and indexed allocation."},
        {"markdown": "## FAT\nThe table chains block numbers."},
    ],
    "standing_notes": [{"from_index": 0, "reason": "Keep headings to three words."}],
    "chat": [],
}

print("\n== the pack carries what the section was actually written from ==")
pack = doc_chat.evidence_pack(STATE, 1, "why only three allocation methods?")
check("the curriculum line this section answers",
      "The three file allocation methods and their trade-offs" in pack)
check("the section as written, so the answer is about what is on screen",
      "Contiguous, linked and indexed allocation." in pack)
check("every takeaway of the session — 'why is this here not there' needs them all",
      "How the FAT locates a file's blocks" in pack)
check("the course's own brief", "Trace every scheduling example by hand." in pack)
check("the rules learned from this reviewer's earlier corrections",
      "Never repeat a paragraph's point" in pack)
check("the standing instruction in force for this section",
      "Keep headings to three words." in pack)
check("the budget it was written to", "max_pages" in pack)
check("…and whether the recording limit was on", "40-minute recording limit was ON" in pack)

print("\n== what the writer did NOT have is stated, not left to be guessed ==")
check("that this session's own deck was never an input",
      "deck was never given to the writer" in pack, pack[-600:])
check("that there was no web access while writing", "No web access during writing" in pack)
check("that later sections were not visible", "No later section" in pack)

print("\n== retrieval follows the QUESTION, not a fixed query ==")
# The point of an open-ended feature: whatever is asked about is what gets looked up.
alloc = doc_chat.evidence_pack(STATE, 1, "was contiguous allocation covered before?")
sched = doc_chat.evidence_pack(STATE, 1, "did we already teach round robin scheduling?")
check("a question about allocation pulls the allocation slides",
      "Contiguous Allocation" in alloc)
check("a question about scheduling pulls the scheduling slide",
      "Round Robin" in sched)
check("…and they are genuinely different packs for the same section", alloc != sched)
check("deck hits are citable by session and slide", "[S1 · Slide 1]" in alloc, alloc[:0])
# The dishonest version of this searched on question + takeaway + session name, which
# always matches something — so a question about material the decks never covered still
# came back with slides labelled "most relevant to what is being asked", and the model
# could cite one as the source of a choice it had nothing to do with.
# No term here appears in either deck — "quantum" would have matched the
# scheduling slide's time quantum, which is a real hit and not a nonsense one.
none_pack = doc_chat.evidence_pack(STATE, 1, "photosynthesis chlorophyll stomata")
check("a question matching no deck says so rather than implying silence",
      "NOTHING in this course's earlier decks matches" in none_pack, none_pack[-700:])
check("…and the fallback context is marked as NOT an answer",
      "must not be cited as the source" in none_pack, none_pack[-700:])
check("…while a question that DOES match is not given that caveat",
      "NOTHING in this course's earlier decks matches" not in alloc)

print("\n== the opening section is described as the copy it is ==")
opening = doc_chat.evidence_pack(STATE, 0, "why this recap?")
check("it is not attributed to a takeaway it does not answer",
      "OPENING section" in opening and "copied VERBATIM" in opening)

print("\n== the system prompt forbids the failure this feature could cause ==")
sysp = doc_chat.SYSTEM
check("it says the model does not remember writing",
      "YOU DO NOT REMEMBER WRITING THIS" in sysp)
check("it requires 'nothing required it' as an available answer",
      "writing judgement" in sysp)
check("it forbids inventing a reason", "NEVER invent a reason" in sysp)
check("it requires quoting the source that is claimed",
      "unless you can quote the slide" in sysp)
check("it tells the model to concede when the reviewer is right",
      "The reviewer may be right" in sysp)
check("it states the model cannot change the document",
      "cannot change the document from here" in sysp)

print("\n== a conversation is threaded per section ==")
STATE["chat"] = [
    {"id": "a", "index": 1, "role": "user", "text": "why three?"},
    {"id": "b", "index": 1, "role": "agent", "text": "Because the deck lists three."},
    {"id": "c", "index": 2, "role": "user", "text": "why is FAT here?"},
]
h1 = doc_chat._history_block(STATE["chat"], 1)
check("a follow-up sees the earlier exchange about ITS section", "why three?" in h1)
check("…and not another section's", "why is FAT here?" not in h1, h1)
check("the roles are named for the model", "REVIEWER:" in h1 and "YOU:" in h1)

print("\n== the answer call ==")
seen = {}


def fake_complete(**kw):
    seen.update(kw)
    return ("The deck for session 1 lists exactly these three [S1 · Slide 1].\n"
            "SUGGESTED-FEEDBACK: add indexed allocation's trade-off table")


doc_chat.llm.complete = fake_complete
out = doc_chat.ask(STATE, 1, "why only three?", use_web=True)
check("the answer text comes back", "lists exactly these three" in out["text"])
check("the machine-readable suggestion is split off, not left in the prose",
      "SUGGESTED-FEEDBACK" not in out["text"], out["text"])
check("…and carried separately for the regenerate box",
      out["suggested_feedback"] == "add indexed allocation's trade-off table")
check("the question reaches the model", "why only three?" in seen["user"])
check("the pack reaches the model", "EVIDENCE PACK" in seen["user"])
check("web search is asked for when requested", seen["model"].endswith(":online"))
check("…and the model is told what to do with it", "Report the DELTA" in seen["system"])
out2 = doc_chat.ask(STATE, 1, "why only three?", use_web=False)
check("with the web off, the model is not given the online variant",
      not seen["model"].endswith(":online"))
check("…and is told to say so rather than answer from memory",
      "NO web access on this turn" in seen["system"])
doc_chat.llm.complete = lambda **kw: "The deck lists three. Nothing needs changing."
check("an answer with no suggestion does not invent one",
      doc_chat.ask(STATE, 1, "why three?", use_web=False)["suggested_feedback"] == "")

print("\n== an empty question is refused before any call is made ==")
called = []
doc_chat.llm.complete = lambda **kw: called.append(1)
try:
    doc_chat.ask(STATE, 1, "   ")
    check("it raises", False)
except ValueError:
    check("it raises rather than spending a call", not called)

print("\n== a question about the WHOLE DOCUMENT is a different question ==")
# "Why is this in section 3 and not section 5" cannot be answered from section 3. It is
# about which curriculum line owns the material, and only the whole-document pack can
# see that.
doc = doc_chat.evidence_pack(STATE, doc_chat.WHOLE_DOC, "why is FAT in its own section?")
check("it is framed as the document, not a section", "THE WHOLE DOCUMENT" in doc)
check("every section is present, in order",
      doc.index("SECTION 1") < doc.index("SECTION 2") < doc.index("SECTION 3"), "")
check("…each with the curriculum line that owns it",
      'covers curriculum line: "How the FAT locates' in doc, doc[:0])
check("…and the opening named as the copy it is",
      "opening — recap and agenda, copied verbatim" in doc)
check("the content of every section is there, not just its label",
      "The table chains block numbers." in doc and "Contiguous, linked" in doc)
check("it explains that the division is NOT a free choice",
      "one section per key takeaway" in doc)
check("the course's rules still apply at document level",
      "Trace every scheduling example by hand." in doc)
check("a section-scoped pack does NOT carry the other sections' text",
      "The table chains block numbers."
      not in doc_chat.evidence_pack(STATE, 1, "why three?"))

print("\n== standing notes are scoped honestly in both views ==")
# A note given at section 2 does not bind section 1, and a document-level answer that
# forgot that would report a section as breaking an instruction it predates.
check("the document view shows each note's range",
      "from section 2 onward: Keep headings to three words." in doc, doc[:0])
check("a section view shows only the notes that bound IT",
      "Keep headings to three words." in doc_chat.evidence_pack(STATE, 1, "x"))
check("…and not one given after it",
      "Keep headings to three words."
      not in doc_chat.evidence_pack(STATE, 0, "x"))

print("\n== a course rule and a one-off fix are kept apart ==")
check("the prompt names both channels",
      "SUGGESTED-FEEDBACK:" in doc_chat.SYSTEM and "SUGGESTED-RULE:" in doc_chat.SYSTEM)
check("…and says a section fix is NOT a course rule",
      "actively harmful as a course rule" in doc_chat.SYSTEM)
check("…and tells it to abstain when it genuinely cannot tell",
      "leave both off" in doc_chat.SYSTEM)
# The opposite failure, seen live: told "that should be true for every session, not only
# this one", the model replied asking whether it might suggest a rule — leaving the
# reviewer to retype what they had just said. Offering costs nothing; both lines are
# proposals the reviewer still has to act on.
check("…but forbids asking permission to suggest",
      "DO NOT ASK THEM AGAIN" in doc_chat.SYSTEM
      and "Never reply asking whether you may suggest" in doc_chat.SYSTEM)
check("…and says why offering is free: neither line acts on its own",
      "still have to approve" in doc_chat.SYSTEM)

doc_chat.llm.complete = lambda **kw: (
    "Your deck stops at linked allocation.\n"
    "SUGGESTED-FEEDBACK: add indexed allocation to this section\n"
    "SUGGESTED-RULE: Show every allocation method's trade-offs in a table.")
both = doc_chat.ask(STATE, 1, "why?", use_web=False)
check("the one-off fix is parsed out",
      both["suggested_feedback"] == "add indexed allocation to this section")
check("the standing rule is parsed out separately",
      both["suggested_rule"] == "Show every allocation method's trade-offs in a table.")
check("neither marker is left in the prose the reviewer reads",
      "SUGGESTED-" not in both["text"], both["text"])
check("…and the answer itself survives intact",
      "Your deck stops at linked allocation." in both["text"])
doc_chat.llm.complete = lambda **kw: "Nothing to change here."
none = doc_chat.ask(STATE, 1, "why?", use_web=False)
check("an answer with neither invents neither",
      none["suggested_feedback"] == "" and none["suggested_rule"] == "")

print("\n== the endpoint: a question can never cost the reviewer anything ==")
import threading                                                       # noqa: E402
import server                                                          # noqa: E402
from fastapi import HTTPException                                      # noqa: E402

GID = "chatcheck01"
server.GUIDED[GID] = dict(STATE, status="reviewing", user_email=ALICE,
                          approved_chunks=[0], chat=[], use_judge=False,
                          index=3, prev=None, nxt=None, base_context="",
                          regen_index=None, logs=[], result=None, error=None)
server._guided_save = lambda gid: None          # no DB round-trip in a unit test
ADMIN = {"email": ALICE, "is_admin": True}

# Run the answering thread INLINE so the test is deterministic.
_threads = []
real_thread = threading.Thread


class _Inline:
    def __init__(self, target=None, args=(), **kw):
        self._t, self._a = target, args

    def start(self):
        self._t(*self._a)


server.threading.Thread = _Inline
doc_chat.llm.complete = lambda **kw: "Because [S1 · Slide 1] says so."

before_chunks = [c["markdown"] for c in server.GUIDED[GID]["chunks"]]
before_approved = list(server.GUIDED[GID]["approved_chunks"])
view = server.guided_ask(GID, server.AskBody(index=1, question="why three?",
                                             use_web=False), user=ADMIN)
check("the reviewer's question is on the record", any(
    m["role"] == "user" and m["text"] == "why three?" for m in view["chat"]))
check("…and the answer lands beside it", any(
    m["role"] == "agent" and "S1 · Slide 1" in m["text"]
    for m in server.GUIDED[GID]["chat"]))
check("THE DOCUMENT IS UNTOUCHED — this is the whole safety property",
      [c["markdown"] for c in server.GUIDED[GID]["chunks"]] == before_chunks)
check("…and so are the approvals: asking never un-ticks a section",
      server.GUIDED[GID]["approved_chunks"] == before_approved)
check("the run is still in review, not pushed into another state",
      server.GUIDED[GID]["status"] == "reviewing")
check("the pending flag is cleared once the answer is in",
      server.GUIDED[GID]["chat_pending"] is False)

# A failed answer must never swallow the question.
doc_chat.llm.complete = lambda **kw: (_ for _ in ()).throw(RuntimeError("502 upstream"))
server.guided_ask(GID, server.AskBody(index=1, question="and the fourth?",
                                      use_web=False), user=ADMIN)
chat = server.GUIDED[GID]["chat"]
check("a failed answer keeps the question the reviewer typed",
      any(m["role"] == "user" and m["text"] == "and the fourth?" for m in chat))
check("…and says what went wrong instead of vanishing",
      any(m.get("failed") and "502 upstream" in m["text"] for m in chat))
check("…and says the document was not touched",
      any(m.get("failed") and "Nothing about the document changed" in m["text"]
          for m in chat))
check("…and the panel is usable again", not server.GUIDED[GID].get("chat_pending"))

doc_chat.llm.complete = lambda **kw: "ok"
try:
    server.guided_ask(GID, server.AskBody(index=99, question="hi"), user=ADMIN)
    check("a section that is not in the run is refused", False)
except HTTPException as e:
    check("a section that is not in the run is refused", e.status_code == 400)
try:
    server.guided_ask(GID, server.AskBody(index=1, question="   "), user=ADMIN)
    check("an empty question is refused", False)
except HTTPException as e:
    check("an empty question is refused", e.status_code == 400)

print("\n== the answer reports what it consulted, and what it read on the web ==")
# Two different kinds of claim, kept apart on purpose. What was CONSULTED is assembled
# by the same code that builds the pack, so every row is checkable. What was READ ON THE
# WEB is parsed from the model's own citations — it is the model's claim about where a
# fact came from, and it is listed so the reviewer can click it rather than take it.
stages = []
doc_chat.llm.complete = lambda **kw: (
    "Three methods are standard, see [colostate](https://cs.colostate.edu/x) "
    "and [ucsd](https://cseweb.ucsd.edu/y) and [colostate](https://cs.colostate.edu/x).")
out = doc_chat.ask(STATE, 1, "was contiguous allocation covered before?",
                   use_web=True, on_stage=lambda n, d: stages.append(n))
check("the stages reported are the real transitions, in order",
      stages == ["reading", "gathered", "asking", "done"], str(stages))
kinds = [c["kind"] for c in out["consulted"]]
check("the section it was asked about is listed", "section" in kinds, str(kinds))
check("the curriculum line is listed", "curriculum" in kinds, str(kinds))
check("the matching deck slides are listed by session and slide",
      any(c["kind"] == "deck" and "S1 · Slide 1" in c["label"] for c in out["consulted"]),
      str([c["label"] for c in out["consulted"]]))
check("the course's brief is listed as in force", "brief" in kinds, str(kinds))
check("the learned rules too", "rules" in kinds, str(kinds))
check("web sources are pulled out of the answer", len(out["sources"]) == 2,
      str(out["sources"]))
check("…de-duplicated, so one page cited twice is listed once",
      [x["url"] for x in out["sources"]]
      == ["https://cs.colostate.edu/x", "https://cseweb.ucsd.edu/y"], str(out["sources"]))
doc_chat.llm.complete = lambda **kw: "No web needed here."
off = doc_chat.ask(STATE, 1, "why?", use_web=False)
check("an answer citing nothing lists no sources", off["sources"] == [])
check("…but still says what it looked at", len(off["consulted"]) > 0)

print("\n== the endpoint accepts a whole-document question too ==")
doc_chat.llm.complete = lambda **kw: "Because takeaway 2 owns the FAT."
v = server.guided_ask(GID, server.AskBody(index=-1, question="why is FAT separate?",
                                          use_web=False), user=ADMIN)
check("a document-level question is accepted", any(
    m["index"] == -1 and m["role"] == "agent" for m in server.GUIDED[GID]["chat"]))
check("…and threaded apart from the section conversations",
      not any(m["index"] == -1 for m in server.GUIDED[GID]["chat"] if m["role"] == "user"
              and m["text"] == "why three?"))
check("the view carries the run's own course, for anything filed from inside it",
      v.get("course") == COURSE, str(v.get("course")))
try:
    server.guided_ask(GID, server.AskBody(index=-2, question="hi"), user=ADMIN)
    check("an index that is neither a section nor the document is refused", False)
except HTTPException as e:
    check("an index that is neither a section nor the document is refused",
          e.status_code == 400)

print("\n== the conversation survives a restart, like the approval ticks ==")
check("chat is checkpointed with the run", "chat" in server._GUIDED_PERSIST_KEYS)
snap = server._guided_snapshot(server.GUIDED[GID])
check("…and the snapshot carries the turns", len(snap.get("chat") or []) >= 3)
check("…and is JSON-safe", __import__("json").dumps(snap["chat"]) is not None)

server.threading.Thread = real_thread

print(f"\n{OK} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
