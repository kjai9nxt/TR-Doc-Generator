"""ASK THE AGENT WHY — an open-ended conversation about a chunk it just wrote.

WHY THIS EXISTS. The reviewer can already reject a chunk and say why (guided
regeneration), and that is the right lever once they have decided something is wrong.
It is the wrong one while they are still deciding. A reviewer looking at three
classification types has no way to find out whether those three are what the source
material carried, what the curriculum asked for, what the page budget allowed, or simply
what the model chose — so the only move available is to reject and re-roll, and a
document can go several rounds over a disagreement that was never a disagreement.

This closes that gap: ANY question, in plain words, answered before the reviewer has to
commit to an opinion. If the answer convinces them, they approve. If it does not, they
regenerate exactly as before — this adds no new way to change the document, on purpose.

THE FAILURE THIS MODULE IS BUILT AGAINST. Ask a language model why it wrote something
and it will tell you, fluently, at length, and without knowing. Post-hoc rationalisation
is the single most dangerous thing a feature like this can produce, because a confident
invented reason is worse than no answer at all: the reviewer believes the document had a
basis it never had, approves it, and the two of them are now further apart than before
they spoke.

So the answer is not built from the model's memory of writing. It is built from an
EVIDENCE PACK — the actual inputs that chunk was generated from, reassembled here:

    · the curriculum takeaway the chunk was written to answer
    · the prior-session deck slides retrieved for THIS QUESTION (quotable, with
      session and slide numbers)
    · how far the prerequisites took the learner on it
    · the course's authored brief and the rules learned from earlier corrections
    · the standing review instructions in force at the time
    · the slide and page budget the chunk was allowed
    · and — stated explicitly, because it is the most common source of surprise —
      what the writer did NOT have

Everything in that list is a fact the reviewer can check. The model's job is to explain
which of them account for what it wrote, quote them, and SAY SO WHEN NONE OF THEM DO.
"A writing judgement, nothing required it" is a complete and useful answer; an invented
one is not.

NOTHING HERE IS QUESTION-SPECIFIC. There is no list of supported questions, no intent
classifier, no template per kind of query. The pack is assembled the same way whatever
is asked — the only thing the question steers is retrieval, so asking about a topic
pulls the deck slides about that topic. A question the pack cannot answer gets said so,
and the web fills in from there.
"""
from __future__ import annotations

import re

from . import config, llm

# The conversation is checkpointed with the run, so it cannot grow without bound.
# Generous: a reviewer who asks fifteen questions about one chunk is using the feature
# exactly as intended, and the oldest turns are the ones they no longer need.
MAX_TURNS_KEPT = 60
# How much earlier conversation to re-send. The reviewer's follow-ups ("and why not the
# fourth one?") only make sense against what was just said.
HISTORY_TURNS = 8

_SUGGEST = re.compile(r"^\s*SUGGESTED-FEEDBACK:\s*(.+?)\s*$", re.I | re.M)
# A conversation sometimes settles something bigger than the section it started on —
# "this course should always show the trade-off table", not "add one here". Those are
# different objects and must not be conflated: pasting a one-off fix into the course's
# standing brief would bind every future document to a detail of one session, and the
# live rule store already shows what that costs (a note about one topic standing as
# house style over every course). So the model marks them separately, and only the
# standing one is ever offered as a skill.
_SUGGEST_RULE = re.compile(r"^\s*SUGGESTED-RULE:\s*(.+?)\s*$", re.I | re.M)


SYSTEM = """You are the agent that WROTE the teaching-reference document under review, \
answering its reviewer's questions about it.

Your one job is to make the reviewer and you understand each other. They are deciding \
whether to approve this section or send it back, and a wrong answer from you costs them \
either a document they should have questioned or a regeneration they did not need.

WHAT YOU MAY ANSWER FROM — and nothing else:
  1. THE EVIDENCE PACK below. These are the actual inputs this section was generated \
from. Quote them and name them: [S12 · Slide 4], [takeaway 3], [course brief], \
[learned rule], [standing note], [budget].
  2. THE SECTION ITSELF, which is quoted in full.
  3. THE WEB, when you have been given search — clearly marked as such, with the source \
named.

THE RULE THAT MATTERS MOST: YOU DO NOT REMEMBER WRITING THIS. You are reconstructing \
the reasons from the inputs, not recalling them. So:
  · When an input accounts for what the document says, say which one and quote it. That \
is a real answer and it is what the reviewer came for.
  · When NOTHING in the pack accounts for it, SAY THAT PLAINLY: "nothing in the source \
material or the rules required this — it was a writing judgement." Then say what the \
alternative would have been and what would make it the better choice. An honest \
"nothing required it" is a complete answer and the reviewer can act on it.
  · NEVER invent a reason. Never dress a guess as a source. Never say the deck said \
something unless you can quote the slide. If you are reconstructing rather than \
quoting, use the words "most likely" and mean them.
  · If the pack simply does not contain what is being asked about, say so in the first \
sentence, then answer from the web if you have it, or say you cannot.

ON DISAGREEMENT. The reviewer may be right. If the question exposes something genuinely \
missing, wrong, or worse than the alternative, say so directly — do not defend the \
document because you produced it. If it is right as written, explain why clearly enough \
that they can disagree with the reasoning rather than just the result. Either way they \
decide, not you: you cannot change the document from here, and you should not imply you \
can.

STYLE. Answer the question that was asked, first sentence. Short paragraphs, plain \
words, no preamble, no "great question", no restating their question back at them. \
Markdown. Usually under 200 words — longer only when the evidence genuinely needs \
laying out. Never a bulleted summary of things they can already see.

If — and only if — you conclude THIS DOCUMENT should actually change, end with:
SUGGESTED-FEEDBACK: <the instruction they could paste into the regenerate box>

If — and only if — the conversation has settled something that should hold for EVERY \
document in this course from now on, and not just this one, add:
SUGGESTED-RULE: <one standing instruction, in the imperative, about how this course is \
always written>

These are different things and most answers warrant neither. A fix for this section is \
NOT a rule — "add indexed allocation here" belongs in the feedback line and would be \
actively harmful as a course rule, because it would bind every future document to a \
detail of one session. Only offer a rule when the preference would apply just as well \
to a session about something else entirely. When you genuinely cannot tell which it is, \
leave both off.

BUT: WHEN THE REVIEWER HAS ALREADY TOLD YOU WHICH, DO NOT ASK THEM AGAIN. If they say \
"for every session", "always", "throughout the course", "as a general rule" — that is \
the answer, and the line goes out. Never reply asking whether you may suggest \
something: offering costs them nothing, because neither line does anything on its own. \
The feedback line only fills in a box they still have to press, and the rule line only \
becomes a DRAFT they still have to approve and can reword first. Asking permission to \
make a suggestion wastes the exchange and leaves them to retype what they just said. \
Do not offer to apply either one yourself — you cannot, and they would not want you to."""


def _chunk_takeaway(state: dict, index: int) -> str:
    """The curriculum line this chunk was written to answer, if it is one.

    Chunk 0 is the opening (recap + agenda, copied verbatim from the curriculum); chunk
    N covers key takeaway N. Getting this wrong would attribute the document to the
    wrong line of the curriculum, which is exactly the confusion the feature exists to
    remove, so it is derived rather than guessed.
    """
    cur = state.get("cur")
    kts = list(getattr(cur, "key_takeaways", None) or [])
    if index <= 0 or index > len(kts):
        return ""
    return str(kts[index - 1])


def _fragment_text(state: dict, index: int) -> str:
    chunks = state.get("chunks") or []
    if not (0 <= index < len(chunks)):
        return ""
    return str((chunks[index] or {}).get("markdown") or "")


# index == WHOLE_DOC asks about the document rather than one of its sections.
#
# The two are genuinely different questions and cannot share a pack. "Why is this
# phrased like that" is answered by one section and its inputs; "why is memory-mapped
# I/O in section 3 and not section 5" cannot be answered from section 3 at all — the
# reason lives in the SHAPE of the whole document, in which curriculum line owns which
# material. A section-scoped pack has no way to see that, so the honest answer from one
# would always have been a guess.
WHOLE_DOC = -1


def evidence_manifest(state: dict, index: int, question: str) -> list[dict]:
    """WHAT WAS ACTUALLY CONSULTED, as a list the panel can show.

    Not the model's account of what it looked at — the model is the last thing that
    should be trusted on that. This is assembled by the same code that builds the pack,
    from the same lookups, so every row is a fact about the request rather than a claim
    inside the answer. A reviewer can check each one: the deck slides are named by
    session and slide, the brief and the rules either were in force or were not.
    """
    course = state.get("course") or ""
    cur = state.get("cur")
    session_no = int(state.get("session_no") or 0)
    whole = index == WHOLE_DOC
    takeaway = "" if whole else _chunk_takeaway(state, index)
    out: list[dict] = []
    if whole:
        out.append({"kind": "document",
                    "label": f"all {len(state.get('chunks') or [])} sections of this document"})
    else:
        out.append({"kind": "section", "label": f"section {index + 1} as written"})
    if takeaway:
        out.append({"kind": "curriculum", "label": f"the curriculum line: \"{takeaway}\""})
    try:
        from . import pptx_ingest
        hits = pptx_ingest.retrieve(course, question, session_no, top_k=6)
        for h in hits:
            out.append({"kind": "deck",
                        "label": f"S{h['session_no']} · Slide {h['slide']} — {h['title']}"})
        if not hits:
            out.append({"kind": "deck-none",
                        "label": "no earlier deck slide in this course matches the question"})
    except Exception:
        pass
    try:
        from . import prereqs as _prereqs
        if _prereqs.detail_block(course, f"{question} {takeaway}".strip()).strip():
            out.append({"kind": "prereq",
                        "label": "what the prerequisites already taught on this"})
    except Exception:
        pass
    try:
        from . import skills as _skills
        if course and _skills.block(course, session_no).strip():
            out.append({"kind": "brief", "label": "this course's authored brief"})
    except Exception:
        pass
    try:
        from . import learning
        if course and learning.rules_block(course).strip():
            out.append({"kind": "rules",
                        "label": "rules learned from your earlier corrections"})
    except Exception:
        pass
    if [n for n in (state.get("standing_notes") or []) if isinstance(n, dict)]:
        out.append({"kind": "standing", "label": "your standing review instructions"})
    if state.get("budgets"):
        out.append({"kind": "budget", "label": "the page and slide budget it was written to"})
    return out


# Markdown links the model put in its answer. With web search on, these ARE the sources
# it read — it is asked to name them inline — so the panel can list them as such instead
# of leaving the reviewer to scrape them out of the prose.
_MD_LINK = re.compile(r"\[([^\]]{1,120})\]\((https?://[^\s)]+)\)")


def sources_in(text: str) -> list[dict]:
    """[{title, url}] for every distinct web source cited, in the order they appear."""
    out, seen = [], set()
    for title, url in _MD_LINK.findall(text or ""):
        if url in seen:
            continue
        seen.add(url)
        out.append({"title": " ".join(title.split()), "url": url})
    return out


def evidence_pack(state: dict, index: int, question: str) -> str:
    """The inputs a section — or the whole document — was generated from, for THIS
    question.

    Retrieval is driven by the QUESTION rather than by the chunk, so asking about any
    topic pulls the deck slides about that topic. Everything else is what was in force
    when the writing happened.

    Every lookup is individually guarded. A reviewer asking a question must get an
    answer from whatever evidence is available, not a failure because one optional
    source (a deck store that has not synced, a course with no brief) is missing.
    """
    course = state.get("course") or ""
    cur = state.get("cur")
    session_no = int(state.get("session_no") or 0)
    whole = index == WHOLE_DOC
    takeaway = "" if whole else _chunk_takeaway(state, index)
    labels = state.get("labels") or []
    label = labels[index] if (not whole and 0 <= index < len(labels)) else ""

    parts: list[str] = []
    if whole:
        parts.append(
            f"THE WHOLE DOCUMENT\n"
            f"  course: {course}\n"
            f"  session {session_no}: {state.get('session_title') or ''}\n"
            f"  {len(state.get('chunks') or [])} sections\n"
            f"  The reviewer is asking about the document as a whole — most often about "
            f"where something sits, why one section carries a topic rather than "
            f"another, or whether the whole thing hangs together. The division is NOT a "
            f"choice the writer made freely: there is one section per key takeaway, in "
            f"the curriculum's own order, and the opening is copied verbatim. So "
            f"\"why is this here and not there\" is usually answered by which "
            f"curriculum line owns the material.\n")
        chunks = state.get("chunks") or []
        body = []
        for i, c in enumerate(chunks):
            lab = labels[i] if i < len(labels) else f"section {i + 1}"
            kt = _chunk_takeaway(state, i)
            body.append(f"--- SECTION {i + 1}: {lab}\n"
                        + (f"    (covers curriculum line: \"{kt}\")\n" if kt else
                           "    (opening — recap and agenda, copied verbatim)\n")
                        + str((c or {}).get("markdown") or "(empty)"))
        parts.append("THE DOCUMENT AS WRITTEN, section by section:\n"
                     + "\n\n".join(body))
    else:
        parts.append(
            f"THIS SECTION\n"
            f"  course: {course}\n"
            f"  session {session_no}: {state.get('session_title') or ''}\n"
            f"  section {index + 1} of {state.get('total') or '?'} — {label}\n"
            + (f"  the curriculum line it was written to cover: \"{takeaway}\"\n"
               if takeaway else
               "  this is the OPENING section: its recap and agenda are copied VERBATIM "
               "from the curriculum and are not written by the model at all.\n"))

        parts.append("THE SECTION AS WRITTEN (what the reviewer is looking at):\n"
                     + (_fragment_text(state, index) or "(empty)"))

    # What the whole session was asked to cover — a question about why something is HERE
    # is often really a question about which section owns it.
    kts = list(getattr(cur, "key_takeaways", None) or [])
    if kts:
        parts.append("EVERY KEY TAKEAWAY OF THIS SESSION, in order (the document is "
                     "divided one section per line; a topic belonging to another line "
                     "is covered there, not here):\n"
                     + "\n".join(f"  {i + 1}. {k}" for i, k in enumerate(kts)))

    # Prior decks, retrieved against the QUESTION FIRST.
    #
    # The obvious implementation — search on question + takeaway + session name — is
    # subtly dishonest, and the failure is the exact one this module exists to prevent.
    # Those extra terms always match something, so a question about a topic the decks
    # have never covered still comes back with slides, presented as "the material most
    # relevant to what is being asked". The model then cites one as the source of a
    # choice it had nothing to do with. Asking on the question alone means an empty
    # result is a real answer — "that is not in your decks" — and the broader search is
    # a clearly-labelled fallback rather than a silent substitute.
    try:
        from . import pptx_ingest
        hits = pptx_ingest.retrieve(course, question, session_no, top_k=6)
        if hits:
            parts.append(
                "SOURCE MATERIAL — the slides from THIS COURSE's earlier decks that "
                "match WHAT IS BEING ASKED. Quote these by session and slide number. "
                "The learner has already been taught all of it, which is why the "
                "document does not re-explain it:\n"
                + "\n".join(f"  [S{h['session_no']} · Slide {h['slide']}] "
                            f"{h['title']}: {h['excerpt']}" for h in hits))
        else:
            wider = pptx_ingest.retrieve(
                course, f"{takeaway} {getattr(cur, 'name', '')}".strip(),
                session_no, top_k=4)
            parts.append(
                "SOURCE MATERIAL: NOTHING in this course's earlier decks matches what "
                "is being asked. Say so — if the reviewer wants to know where something "
                "came from, the honest answer is that it did not come from the decks, "
                "and most likely came from the curriculum line plus the model's own "
                "knowledge of the subject.\n"
                + ("Below is the closest material to THIS SECTION generally. It is "
                   "context, NOT an answer to the question, and must not be cited as "
                   "the source of anything the question is about:\n"
                   + "\n".join(f"  [S{h['session_no']} · Slide {h['slide']}] "
                               f"{h['title']}: {h['excerpt']}" for h in wider)
                   if wider else ""))
    except Exception:
        pass

    # How far the prerequisites took the learner — the other half of "why isn't this
    # explained here".
    try:
        from . import prereqs as _prereqs
        detail = _prereqs.detail_block(course, f"{question} {takeaway}".strip())
        if detail.strip():
            parts.append("WHAT THE LEARNER ALREADY KNEW BEFORE SESSION 1 (from the "
                         "prerequisite courses). Anything here may be used by name "
                         "without explanation:\n" + detail.strip())
    except Exception:
        pass

    # The rules the chunk was written under. A reviewer asking "why is it phrased like
    # that" is very often looking at their own brief, or at a rule the agent learned
    # from their own earlier correction — and neither is visible from the document.
    try:
        from . import skills as _skills
        # THE SESSION'S BRIEF TOO. This answers "why is it written like that", and a
        # reviewer looking at a chunk written under a session skill has to be shown the
        # session skill — otherwise the honest answer is missing from the evidence.
        brief = _skills.block(course, session_no) if course else ""
        if brief.strip():
            parts.append("THE COURSE'S OWN BRIEF — written by the course owner and in "
                         "force when this was generated:\n" + brief.strip())
    except Exception:
        pass
    try:
        from . import learning
        rules = learning.rules_block(course) if course else ""
        if rules.strip():
            parts.append("RULES LEARNED FROM THIS REVIEWER'S EARLIER CORRECTIONS, in "
                         "force when this was generated:\n" + rules.strip())
    except Exception:
        pass

    notes = [n for n in (state.get("standing_notes") or []) if isinstance(n, dict)]
    if whole:
        standing = [f"from section {int(n.get('from_index', 0)) + 2} onward: "
                    f"{str(n.get('reason') or '').strip()}"
                    for n in notes if str(n.get("reason") or "").strip()]
        heading = ("STANDING REVIEW INSTRUCTIONS the reviewer gave during this review. "
                   "Each applies only from where it was given onward, so an earlier "
                   "section legitimately does not follow one given later:\n")
    else:
        standing = [str(n.get("reason") or "").strip() for n in notes
                    if index > int(n.get("from_index", 0))
                    and str(n.get("reason") or "").strip()]
        heading = ("STANDING REVIEW INSTRUCTIONS the reviewer gave earlier in this "
                   "same document, which this section was written under:\n")
    standing = list(dict.fromkeys(standing))
    if standing:
        parts.append(heading + "\n".join(f"  - {t}" for t in standing))

    b = state.get("budgets") or {}
    if b:
        parts.append(
            "THE BUDGET. These are the ceilings for THIS ONE DOCUMENT — this single "
            "session's teaching reference, not the course and not a set of documents. "
            "Every section of it shares them, so length questions usually end here:\n"
            f"  {b}\n"
            + ("  The 40-minute recording limit was ON.\n"
               if state.get("enforce_time", True)
               else "  The 40-minute recording limit was OFF (depth mode): the document "
                    "was deliberately written fuller.\n"))

    # THE MOST COMMON SOURCE OF SURPRISE, and it can only be answered by saying it.
    parts.append(
        "WHAT THE WRITER DID NOT HAVE — state this plainly if it is the answer:\n"
        "  · This session's OWN slide deck is not an input. The document is written "
        "from the curriculum's key takeaways, the earlier sessions' decks, the "
        "prerequisites and the rules above. So \"why doesn't this match my deck for "
        "this session\" has one honest answer: that deck was never given to the writer.\n"
        "  · No web access during writing. Anything current or version-specific came "
        "from the model's own knowledge, which has a cutoff, and is worth checking.\n"
        "  · No later section of this document. Each section is written in order, "
        "seeing only the ones already approved before it.")
    return "\n\n".join(parts)


def _history_block(chat: list, index: int) -> str:
    """Earlier turns about THIS chunk, so a follow-up question makes sense."""
    turns = [m for m in (chat or [])
             if isinstance(m, dict) and m.get("index") == index and m.get("text")]
    turns = turns[-HISTORY_TURNS:]
    if not turns:
        return ""
    lines = []
    for m in turns:
        who = "REVIEWER" if m.get("role") == "user" else "YOU"
        lines.append(f"{who}: {str(m['text']).strip()}")
    return ("EARLIER IN THIS CONVERSATION, about this same section:\n"
            + "\n\n".join(lines))


def ask(state: dict, index: int, question: str, *, use_web: bool = True,
        on_stage=None) -> dict:
    """Answer one question. Returns {text, suggested_feedback, suggested_rule, web,
    consulted, sources}.

    `on_stage(name, detail)` is called at each REAL transition, so the panel can say what
    is happening instead of showing an unexplained spinner. Only genuine transitions are
    reported: the pack being assembled, the retrieval landing, the model call starting
    (and whether it has web search), the answer arriving. Nothing here invents a stage it
    cannot observe — a fabricated "reading geeksforgeeks.org…" would be theatre, and the
    one thing this feature cannot afford is to look like it knows more than it does.

    Raises whatever llm.complete raises — the caller records it as a failed turn rather
    than losing the reviewer's question.
    """
    def stage(name, detail=""):
        if on_stage:
            try:
                on_stage(name, detail)
            except Exception:
                pass

    question = " ".join((question or "").split())
    if not question:
        raise ValueError("Ask something.")
    m = config.harness()["model"]
    model = m.get("judge") or m["generator"]
    web_note = ""
    # Web search is the same mechanism the judge already uses for market and recency
    # checks: OpenRouter's ":online" variant, on the key that is already configured.
    if use_web and m.get("provider", "openrouter").lower() == "openrouter":
        if not model.endswith(":online"):
            model += ":online"
        web_note = (
            "\n\nYOU HAVE WEB SEARCH. Use it when the question turns on a fact rather "
            "than on a choice — whether a classification is standard or one of "
            "several, whether a figure is current, whether mainstream references "
            "(GeeksforGeeks, TutorialsPoint, Scaler, official docs) teach it the same "
            "way.\n"
            "Report the DELTA and name your sources: what the document says, what the "
            "references say, and where they differ. A difference is not automatically "
            "a defect — the document is bounded by the curriculum and the page budget, "
            "and leaving something out on purpose is legitimate — but the reviewer is "
            "entitled to know it exists and to decide.\n"
            "Keep web-sourced claims clearly separate from what the evidence pack says. "
            "Never let a search result become a claim about why the document was "
            "written as it was.")
    else:
        web_note = ("\n\nYou have NO web access on this turn. If the question can only "
                    "be settled by checking a current external fact, say so rather "
                    "than answering from memory.")

    stage("reading", "reading what this section was written from")
    pack = evidence_pack(state, index, question)
    consulted = evidence_manifest(state, index, question)
    n_deck = len([c for c in consulted if c["kind"] == "deck"])
    stage("gathered",
          (f"found {n_deck} matching deck slide(s)" if n_deck
           else "no deck slide matches this question")
          + f" · {len(consulted)} source(s) of evidence")
    stage("asking",
          "searching the web and weighing it against the document"
          if (use_web and web_note and "NO web access" not in web_note)
          else "reasoning over the document's own inputs")
    history = _history_block(state.get("chat") or [], index)
    user = "\n\n".join(p for p in (
        "EVIDENCE PACK\n=============\n" + pack,
        history,
        f"THE REVIEWER ASKS:\n{question}",
        "Answer them now.",
    ) if p)

    raw = llm.complete(
        system=SYSTEM + web_note,
        user=user,
        model=model,
        max_tokens=int(m.get("judge_max_tokens") or 4000),
        temperature=0.2,
        label="doc_chat",
    )
    text = (raw or "").strip()
    suggested = suggested_rule = ""
    hit = _SUGGEST.search(text)
    if hit:
        suggested = hit.group(1).strip()
        text = _SUGGEST.sub("", text).strip()
    hit = _SUGGEST_RULE.search(text)
    if hit:
        suggested_rule = hit.group(1).strip()
        text = _SUGGEST_RULE.sub("", text).strip()
    if not text:
        raise RuntimeError("The model returned an empty answer.")
    srcs = sources_in(text)
    stage("done", f"cited {len(srcs)} web source(s)" if srcs else "answered from the document")
    return {"text": text, "suggested_feedback": suggested,
            "suggested_rule": suggested_rule, "web": bool(web_note and use_web),
            # WHAT IT LOOKED AT (assembled by code) and WHAT IT READ ON THE WEB (parsed
            # out of its own citations). Kept apart: the first is verifiable, the second
            # is the model's claim about where a fact came from.
            "consulted": consulted, "sources": srcs}
