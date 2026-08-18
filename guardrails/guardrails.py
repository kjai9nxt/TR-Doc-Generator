"""Deterministic hard gates. No LLM. These run on the generator's JSON output
BEFORE any doc is accepted. Any FAIL blocks acceptance and feeds the reason
into the revision pass.
"""
from __future__ import annotations
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402


@dataclass
class GuardrailResult:
    passed: bool
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self):
        return {"passed": self.passed, "failures": self.failures, "warnings": self.warnings}


def _slides(doc: dict) -> list[dict]:
    return [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]


# --------------------------------------------------------------------------- #
# Helpers for the content/voice/formatting gates.
#
# These are DETERMINISTIC on purpose. Every one of them was already stated in the
# prompt and still came back wrong in real output — prose paragraphs as slide
# content, "you", "as we saw earlier", 5-sentence speaker notes, a paraphrased
# agenda. A rule that is only in the prompt is a rule the model can talk itself out
# of; a guardrail failure feeds straight into the revision pass.
# --------------------------------------------------------------------------- #
def _text_blocks(slide: dict) -> list[str]:
    """The `text`-type content blocks of one slide (the prose-risk ones)."""
    out = []
    for b in slide.get("content") or []:
        if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
            out.append(str(b["text"]))
    return out


def _bullet_lists(slide: dict) -> list[list[str]]:
    return [[str(i) for i in (b.get("items") or [])]
            for b in (slide.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "bullets"]


def _tables(slide: dict) -> list[dict]:
    return [b for b in (slide.get("content") or [])
            if isinstance(b, dict) and b.get("type") == "table"]


def _slide_visible_text(slide: dict) -> list[tuple[str, str]]:
    """(field_label, text) for everything a learner READS on the slide.

    `speaker_notes` and `visual_guidance` are excluded — notes are spoken and may
    keep continuity language, and visual guidance is a note to the deck builder.
    """
    out: list[tuple[str, str]] = []
    for fld in ("title", "heading", "subheading", "analogy"):
        if slide.get(fld):
            out.append((fld, str(slide[fld])))
    for i, t in enumerate(_text_blocks(slide)):
        out.append((f"content text block {i + 1}", t))
    for li, items in enumerate(_bullet_lists(slide)):
        for bi, it in enumerate(items):
            out.append((f"content bullet {li + 1}.{bi + 1}", it))
    for ti, tb in enumerate(_tables(slide)):
        for row in tb.get("rows") or []:
            for cell in row:
                out.append((f"content table {ti + 1} cell", str(cell)))
    return out


# Abbreviations that end in a period but do NOT end a sentence. Without these a
# note mentioning "RFC 4960, i.e. the SCTP spec." would be miscounted and the run
# would fail a cap it actually met.
_ABBREV = re.compile(
    r"\b(?:e\.g|i\.e|etc|vs|Fig|Eq|approx|No|Dr|Mr|Mrs|Ms|Prof|Inc|Ltd|St)\."
    r"|(?<![A-Za-z])[A-Z]\.")          # a standalone initial, not the tail of "TCP."


def _sentence_count(text: str) -> int:
    """Sentences in `text`, tolerant of abbreviations and decimals.

    Worth the care: a note reading "Flag the 3-way handshake, i.e. SYN/SYN-ACK/ACK.
    Interviewers ask for the state names." is two sentences, and a naive split on
    periods would call it three and fail a cap the note actually met — which would
    send the run into a revision loop it can never satisfy.
    """
    t = str(text or "").strip()
    if not t:
        return 0
    t = _ABBREV.sub(lambda m: m.group(0).replace(".", "\x00"), t)
    t = re.sub(r"(\d)\.(\d)", "\\1\x00\\2", t)         # 1.5 ms, 40.0 min
    parts = [p for p in re.split(r"[.!?]+(?:\s|$)", t) if p.strip()]
    return max(1, len(parts))


def _word_count(text: str) -> int:
    return len(str(text or "").split())


def _norm_line(text: str) -> str:
    """Normalise an agenda / takeaway line for comparison: strip any leading
    numbering ("1.", "1)", "- ") and collapse whitespace/case."""
    t = re.sub(r"^\s*(?:\d+\s*[.)\-:]|[-*•])\s*", "", str(text or ""))
    return re.sub(r"\s+", " ", t).strip().lower().rstrip(".")


# A concrete figure: a decimal or hex number, optionally with a unit. This is what
# separates a real worked example ("base 0x00400000, page 4 KB, offset 1 234") from a
# hand-wave ("the base address plus the offset gives the physical address").
_NUMERIC = re.compile(r"0x[0-9a-f]+|\b\d[\d,._]*\b", re.I)


def _slide_text_blob(slide: dict) -> str:
    """All of one slide's teaching text, for content-level checks."""
    parts = [str(slide.get(f) or "") for f in ("title", "heading", "subheading",
                                               "analogy", "speaker_notes")]
    parts += _text_blocks(slide)
    for items in _bullet_lists(slide):
        parts += items
    for tb in _tables(slide):
        parts += [str(c) for row in (tb.get("rows") or []) for c in row]
        parts += [str(c) for c in (tb.get("columns") or [])]
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# Sub-topics named INSIDE a key-takeaway line.
#
# The curriculum writes a takeaway as "Topic: sub-topic; sub-topic, sub-topic" —
#   "2. Number Systems: Decimal notation & radix / base, Binary notation; counting
#    in binary"
# Everything after the colon is a promise the session makes to the learner, item by
# item. Coverage was checked only against the model's OWN enumeration of sub-concepts,
# which cannot catch a promise dropped before the enumeration was written — so a
# sub-topic named in the sheet could go untaught with every gate green.
# --------------------------------------------------------------------------- #
_STOPWORDS = {
    "a", "an", "the", "and", "or", "of", "in", "on", "to", "for", "with", "vs",
    "versus", "how", "what", "why", "when", "its", "it", "is", "are", "as", "by",
    "from", "into", "at", "we", "this", "that", "their", "them", "using", "use",
    "basics", "introduction", "overview", "concepts", "concept", "types", "type",
}


def _content_tokens(text: str) -> set[str]:
    """Distinctive words of a phrase — lowercased, de-punctuated, stopwords dropped.
    Short tokens are kept only if they look like an acronym or identifier (MSB, I/O,
    CPU), which are exactly the terms a curriculum line leans on."""
    raw = re.findall(r"[A-Za-z][A-Za-z0-9+#/_-]*", str(text or ""))
    out = set()
    for w in raw:
        low = w.lower()
        if low in _STOPWORDS:
            continue
        if len(low) <= 2 and not w.isupper():
            continue
        out.add(low)
    return out


def _singular(tok: str) -> str:
    """Crude de-pluralisation, enough to match 'buses'/'bus', 'controllers'/'controller'
    without dragging in a stemmer."""
    for suf, cut in (("ies", 3), ("sses", 2), ("ses", 2), ("s", 1)):
        if len(tok) > cut + 2 and tok.endswith(suf):
            return tok[:-cut] + ("y" if suf == "ies" else "")
    return tok


def _norm_tokens(text: str) -> set[str]:
    return {_singular(t) for t in _content_tokens(text)}


# An acronym or initialism inside a curriculum line — DMA, MSB, LSB, I/O, UTF-8, RGB.
# These are the load-bearing words of a sub-topic: everything around them ("direct",
# "memory", "access") is vocabulary the rest of the section uses anyway, so a plain
# proportion-of-words match scores a DROPPED sub-topic as covered. Measured: removing
# every mention of DMA from an I/O section still matched 2 of 3 words and passed.
_ACRONYM = re.compile(r"\b(?=[A-Z0-9/&+.-]*[A-Z][A-Z0-9/&+.-]*[A-Z])[A-Z][A-Za-z0-9/&+.-]*\b")


def _mandatory_tokens(phrase: str) -> set[str]:
    """The tokens of `phrase` that must appear verbatim for it to count as taught."""
    out = set()
    for m in _ACRONYM.findall(str(phrase or "")):
        for part in re.split(r"[/&+.-]", m):
            if len(part) >= 2:
                out.add(_singular(part.lower()))
    return out


def _covers(phrase: str, blob_tokens: set[str], threshold: float) -> bool:
    """Is `phrase` taught by text whose vocabulary is `blob_tokens`?

    Two conditions, because either alone is wrong: enough of the phrase's words are
    present (a sub-topic may legitimately be taught in other words), AND every acronym
    it names is present (those cannot be paraphrased away — if the section never says
    "DMA", it did not teach direct memory access).
    """
    toks = _norm_tokens(phrase)
    if not toks:
        return True
    if not _mandatory_tokens(phrase) <= blob_tokens:
        return False
    return len(toks & blob_tokens) / len(toks) >= threshold


def takeaway_subtopics(line: str) -> list[str]:
    """The sub-topics a takeaway line names, in order.

    "1. Data Representation & Binary Basics: How computers see information; binary
     (1s and 0s), Bit & byte; most- and least-significant bit (MSB / LSB)"
      -> ["How computers see information", "binary (1s and 0s)", "Bit & byte",
          "most- and least-significant bit (MSB / LSB)"]

    Only the part AFTER the first colon is split — the part before it is the topic
    name, and it is already enforced verbatim as the section name. A line with no
    colon names no sub-topics, and this returns []: the takeaway is then covered by
    the coverage_map rules alone, exactly as before.
    """
    text = re.sub(r"^\s*(?:\d+\s*[.)\-:]|[-*•])\s*", "", str(line or "")).strip()
    if ":" not in text:
        return []
    after = text.split(":", 1)[1]
    parts = [p.strip(" .;,&-–—") for p in re.split(r"[;,]", after)]
    # A fragment with no distinctive word of its own ("and so on", "etc") is not a
    # promise anyone can check, so it is not treated as one.
    return [p for p in parts if p and _content_tokens(p)]


def _phrase_hits(text: str, phrases: list[str]) -> list[str]:
    """Which of `phrases` occur in `text`, matched on word boundaries so 'no' does
    not fire inside 'node' and 'you' does not fire inside 'your'."""
    low = str(text or "").lower()
    hits = []
    for p in phrases:
        if re.search(r"(?<![a-z])" + re.escape(p.lower()) + r"(?![a-z])", low):
            hits.append(p)
    return hits


def check(doc: dict, session, is_first: bool, is_last: bool,
          *, rich: bool = False, budgets: dict | None = None) -> GuardrailResult:
    """`budgets` (src.budgets.for_session) is the slide/page allowance THIS document
    is held to — a course may set its own, and a single session may override that.
    Omitted, the harness numbers apply exactly as before."""
    h = config.harness()
    con = h["constraints"]
    gates = h["gates"]
    fails: list[str] = []
    warns: list[str] = []

    # --- required top-level fields ---
    if not doc.get("session_title"):
        fails.append("Missing session_title.")
    if not doc.get("agenda"):
        fails.append("Missing agenda.")
    if not doc.get("sections"):
        fails.append("Missing sections.")
    if not doc.get("key_takeaways"):
        fails.append("Missing key_takeaways.")
    if not doc.get("closing"):
        fails.append("Missing closing.")

    # --- recap rule ---
    if is_first and doc.get("recap"):
        warns.append("Recap present on the first session — should be omitted.")
    if not is_first and not doc.get("recap"):
        fails.append("Recap missing (required for non-first sessions).")

    # --- upcoming session rule ---
    if not is_last and not doc.get("upcoming_session"):
        fails.append("upcoming_session missing (not the final session).")

    # --- agenda <= key takeaways ---
    n_kt = session.key_takeaways_count
    if len(doc.get("agenda", [])) > n_kt:
        fails.append(f"Agenda has {len(doc['agenda'])} bullets > {n_kt} key takeaways.")

    # --- coverage: every takeaway represented somewhere ---
    doc_kt = doc.get("key_takeaways", [])
    if len(doc_kt) < n_kt:
        warns.append(f"Doc lists {len(doc_kt)} takeaways vs {n_kt} in the structure.")

    # --- slide count ---
    slides = _slides(doc)
    # Depth mode (40-min limit off) allows more slides for worked examples etc.
    slide_max = int((budgets or {}).get("max_slides")
                    or (con["slides"].get("max_rich", con["slides"]["max"]) if rich
                        else con["slides"]["max"]))
    slide_min = int((budgets or {}).get("min_slides") or con["slides"]["min"])
    if len(slides) < slide_min:
        fails.append(f"Only {len(slides)} slides (min {slide_min}) — "
                     f"split content across more slides, don't cram.")
    if len(slides) > slide_max:
        # This message is not just for the reviewer: it goes into `issues`, which is what
        # the revision pass is told to fix. It used to read "split content, don't cram" —
        # the advice for being UNDER the minimum — so a doc with too many slides was
        # asked to make more of them. Say where the excess is and what to merge.
        over = len(slides) - slide_max
        heavy = sorted(((len(sec.get("slides") or []), sec.get("name") or f"section {i + 1}")
                        for i, sec in enumerate(doc.get("sections") or [])), reverse=True)[:3]
        worst = ", ".join(f"{name} ({n} slides)" for n, name in heavy)
        fails.append(
            f"{len(slides)} slides (max {slide_max}) — {over} too many. MERGE, do not "
            f"split: put closely-related sub-concepts on one slide (a shared bullet list "
            f"or a comparison table covering several at once) and drop analogies outside "
            f"concept_intro slides and worked examples the topic does not need. Never drop "
            f"a sub-concept. Longest sections: {worst}.")

    # --- per-slide required fields ---
    # Five fields are unconditional. `analogy` is NOT: it is required on a first
    # introduction and forbidden everywhere else (see the role/analogy gate below) —
    # the old "all six on every slide" rule is what produced an analogy on every
    # advantages, reasoning and worked-example slide.
    for s in slides:
        tag = f"Slide {s.get('n', '?')}"
        for req in ("heading", "subheading", "content",
                    "visual_guidance", "speaker_notes"):
            if not s.get(req) or not str(s.get(req)).strip():
                fails.append(f"{tag}: missing '{req}' (required on every slide).")

    # --- slide role: declared, valid, and honestly distributed ------------------
    # The role is what makes the analogy and worked-example rules checkable at all.
    role_cfg = con.get("slide_roles", {})
    valid_roles = list(role_cfg.get("values", []))
    roles: dict = {}
    if role_cfg.get("required", False) and valid_roles:
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            role = str(s.get("role") or "").strip()
            if not role:
                fails.append(
                    f"{tag}: missing 'role' — declare why this slide exists, one of: "
                    f"{', '.join(valid_roles)}.")
            elif role not in valid_roles:
                fails.append(
                    f"{tag}: role '{role}' is not one of {', '.join(valid_roles)}.")
            else:
                roles[s.get("n")] = role
        # Cap first-introduction slides. Without this, "role" is trivially gamed by
        # calling every slide a first introduction and keeping every analogy.
        share_cap = role_cfg.get("max_concept_intro_share")
        n_intro = sum(1 for r in roles.values() if r == "concept_intro")
        if share_cap and slides and n_intro > share_cap * len(slides):
            fails.append(
                f"{n_intro} of {len(slides)} slides are labelled 'concept_intro' "
                f"(max {share_cap:.0%}) — most slides build on a concept already "
                f"introduced. Re-label the ones that explain, compare, or apply it, "
                f"and drop their analogies.")

        # --- BROAD -> SPECIFIC, made checkable ------------------------------------
        # Sections were opening on a narrow detail — one type, one formula, one step —
        # so the learner met the parts before the shape of the whole. The rule is that
        # a section opens on the LANDSCAPE: what this is and which types/parts it has.
        # Only the opening slide's ROLE is checked here; whether that slide really sets
        # the map is a judgement, and it is scored under the pedagogy dimension.
        if role_cfg.get("require_broad_to_specific_opener", False):
            opener_ok = set(role_cfg.get("section_opener_roles", []))
            for si, sec in enumerate(doc.get("sections") or []):
                sec_slides = sec.get("slides") or []
                if not sec_slides:
                    continue
                first = sec_slides[0]
                r = roles.get(first.get("n")) or str(first.get("role") or "").strip()
                if opener_ok and r and r not in opener_ok:
                    fails.append(
                        f"Section {si + 1} ('{sec.get('name') or '?'}') opens on slide "
                        f"{first.get('n', '?')} with role '{r}' — a section must start "
                        f"BROAD and then go specific. Its first slide must be "
                        f"{' or '.join(sorted(opener_ok))}: name what this topic is and "
                        f"which types/kinds/parts it has, all in one place, before any "
                        f"single one of them is taught.")

    # --- analogy placement: an EXACT biconditional against the role -------------
    # required iff role == concept_intro. An analogy earns its lines the first time a
    # concept is met; on a mechanism, comparison, pros/cons, reasoning, application or
    # worked-example slide it adds length and no understanding.
    a_cfg = con.get("analogy", {})
    req_roles = a_cfg.get("required_on_roles", [])
    ban_roles = a_cfg.get("banned_on_roles", [])
    if req_roles or ban_roles:
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            role = roles.get(s.get("n"))
            has = bool(str(s.get("analogy") or "").strip())
            if role in req_roles and not has:
                fails.append(
                    f"{tag}: role is '{role}' (a first introduction) so an analogy is "
                    f"REQUIRED — one everyday scene with an explicit tie-back.")
            if role in ban_roles and has:
                fails.append(
                    f"{tag}: role is '{role}', so it must have NO analogy — remove the "
                    f"'analogy' field. An analogy belongs only where a concept is "
                    f"introduced for the first time.")

    # --- heading / subheading word cap ---
    # A heading is a slide LABEL, not a sentence: hard 4-word cap (house rule).
    # Enforced in BOTH modes — depth mode adds body depth, never longer headings.
    hcap = con.get("headings", {}).get("max_words", 4)
    for s in slides:
        for fld in ("heading", "subheading"):
            words = str(s.get(fld) or "").split()
            if len(words) > hcap:
                fails.append(
                    f"Slide {s.get('n', '?')}: {fld} has {len(words)} words "
                    f"(max {hcap}) — \"{s.get(fld)}\". Shorten to a {hcap}-word label.")

    # --- no repeated analogy across slides (exact match; backstop for the
    #     no-repeat rule — the LLM eval set also catches same-theme reuse) ---
    analogies = [str(s.get("analogy", "")).strip().lower() for s in slides if s.get("analogy")]
    dupes = sorted({a for a in analogies if analogies.count(a) > 1})
    if dupes:
        fails.append(f"Duplicate analogy reused across {len(dupes)} slide group(s) — "
                     f"each slide needs a distinct analogy.")

    # --- agenda text == key takeaway text, numbered 1..N ------------------------
    # The reviewer asked for this repeatedly and the model kept paraphrasing, so it
    # is a gate now, not a preference. Compared on normalised lines so the numbering
    # prefix itself is not what makes them differ.
    ag_cfg = con.get("agenda", {})
    agenda = [str(a) for a in doc.get("agenda", [])]
    if ag_cfg.get("text_must_equal_key_takeaway", False) and agenda:
        src = list(session.key_takeaways)
        if len(agenda) != len(src):
            fails.append(
                f"Agenda has {len(agenda)} items but the curriculum has {len(src)} key "
                f"takeaways — emit one agenda item per takeaway, verbatim.")
        for i, item in enumerate(agenda[:len(src)]):
            if _norm_line(item) != _norm_line(src[i]):
                fails.append(
                    f"Agenda item {i + 1} was reworded. Expected the curriculum line "
                    f"verbatim:\n    expected: {src[i]}\n    got:      {item}")
        # …and the doc's own Key Takeaways list must match the agenda one-to-one.
        for i, (a, k) in enumerate(zip(agenda, doc.get("key_takeaways", []))):
            if _norm_line(a) != _norm_line(str(k)):
                fails.append(
                    f"Agenda item {i + 1} and key takeaway {i + 1} differ — they must be "
                    f"the same text.\n    agenda:   {a}\n    takeaway: {k}")
    if ag_cfg.get("numbered", False):
        unnumbered = [a for a in agenda if not re.match(r"^\s*\d+\s*[.)\-:]", a)]
        if unnumbered:
            fails.append(
                f"{len(unnumbered)} agenda item(s) are not numbered — number them "
                f"1..{len(agenda)} to mirror the numbered Key Takeaways.")

    # --- one section per takeaway, named after it, in order ---------------------
    # The layout rule has always been "one section breaker per agenda item, in agenda
    # order, with the same text", but only the rubric ever looked at it. That left the
    # coverage machinery resting on an unchecked assumption: sub-concept references are
    # scoped to "the section whose name IS takeaway i", and the takeaway-completeness
    # check below looks up the same way, so a section renamed by a word quietly detached
    # both from the takeaway they are meant to police.
    if ag_cfg.get("sections_named_after_takeaways", False):
        src_kt = list(session.key_takeaways)
        secs = doc.get("sections") or []
        if secs and len(secs) != len(src_kt):
            fails.append(
                f"{len(secs)} section(s) for {len(src_kt)} key takeaways — the document "
                f"needs exactly one section per takeaway, in curriculum order.")
        for i, sec in enumerate(secs[:len(src_kt)]):
            if _norm_line(sec.get("name")) != _norm_line(src_kt[i]):
                fails.append(
                    f"Section {i + 1} is named \"{sec.get('name')}\" but must carry key "
                    f"takeaway {i + 1} verbatim (it is the agenda item too):\n"
                    f"    expected: {src_kt[i]}")

    # --- recap must carry ALL of the previous session's agenda items -------------
    rc_cfg = con.get("recap", {})
    if (rc_cfg.get("must_cover_all_prev_agenda_items", False)
            and not is_first and isinstance(doc.get("recap"), dict)):
        n_prev = getattr(session, "prev_key_takeaways_count", None)
        bullets = doc["recap"].get("bullets") or []
        if isinstance(n_prev, int) and n_prev > 0 and len(bullets) < n_prev:
            fails.append(
                f"Recap has {len(bullets)} bullet(s) but the previous session had "
                f"{n_prev} agenda items — the recap must carry ALL of them, verbatim, "
                f"in its 'topic: subtopics' format.")

    # --- content text blocks: tight, not prose ---------------------------------
    # Applies in depth mode too: depth is meant to come from MORE slides covering
    # more sub-concepts, never from fatter paragraphs (see harness/depth_mode.md).
    c_cfg = con.get("content", {})
    max_cw = c_cfg.get("max_words_per_text_block")
    max_cs = c_cfg.get("max_sentences_per_text_block")
    for s in slides:
        tag = f"Slide {s.get('n', '?')}"
        for i, t in enumerate(_text_blocks(s)):
            if max_cw and _word_count(t) > max_cw:
                fails.append(
                    f"{tag}: content text block {i + 1} is {_word_count(t)} words "
                    f"(max {max_cw}) — this is slide text, not prose. Keep one framing "
                    f"sentence and move the detail into bullets or a table.")
            if max_cs and _sentence_count(t) > max_cs:
                fails.append(
                    f"{tag}: content text block {i + 1} has {_sentence_count(t)} "
                    f"sentences (max {max_cs}).")

    # --- prose / bullet MIX ------------------------------------------------------
    # The reviewer's complaint: "mostly all the content is bullets only, which looks
    # odd". It did, and nothing checked it — every earlier rule pushed the same way
    # ("prefer bullets/tables over text"), so the model dutifully bulleted everything,
    # including pairs of short points that are plainly one sentence. Two deterministic
    # halves, both cheap to check and both exactly what the reviewer was seeing:
    #   1. most slides must carry a framing PARAGRAPH, not open straight into a list;
    #   2. a "list" of one or two items is a bulleted sentence — write the sentence.
    min_text_share = c_cfg.get("min_slides_with_text_share")
    if min_text_share and slides:
        with_text = [s for s in slides if _text_blocks(s)]
        if len(with_text) < min_text_share * len(slides):
            bare = [s.get("n", "?") for s in slides if not _text_blocks(s)]
            fails.append(
                f"Only {len(with_text)} of {len(slides)} slides carry a prose `text` "
                f"block (need at least {min_text_share:.0%}) — this document is almost "
                f"all bullets, which reads as choppy and drops the connective reasoning "
                f"a bullet cannot carry. Give slide(s) {bare} a short framing paragraph "
                f"(<= {max_cw or 55} words) saying what the slide is about, then let the "
                f"bullets and tables carry the detail.")

    min_items = c_cfg.get("min_bullet_items")
    if min_items:
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            for li, items in enumerate(_bullet_lists(s)):
                if 0 < len(items) < min_items:
                    fails.append(
                        f"{tag}: bullet list {li + 1} has {len(items)} item(s) (min "
                        f"{min_items}) — a one- or two-item list is a sentence that was "
                        f"bulleted. Fold it into the slide's paragraph: "
                        f"{'; '.join(i[:40] for i in items)}")

    # Same idea, warned rather than failed: a list whose items are all a couple of
    # words is keyword soup — but a genuine keyword list (field names, ports, flags) is
    # legitimate, and only the judge can tell those apart.
    short_words = c_cfg.get("short_bullet_words")
    if short_words:
        for s in slides:
            for li, items in enumerate(_bullet_lists(s)):
                if len(items) >= min_items and all(
                        _word_count(i) <= short_words for i in items):
                    warns.append(
                        f"Slide {s.get('n', '?')}: bullet list {li + 1} is all "
                        f"<= {short_words}-word fragments — if these are not keywords "
                        f"(field names, ports, flags), say them in the paragraph or "
                        f"make them a table.")

    # --- no redundancy on a slide ----------------------------------------------
    # A bullet that repeats its lead-in sentence, or a table restated as bullets,
    # burns slide space and recording time for no new information.
    if c_cfg.get("no_restating_lead_in_bullets", False):
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            leads = {_norm_line(t) for t in _text_blocks(s)}
            for items in _bullet_lists(s):
                for it in items:
                    if _norm_line(it) in leads and _norm_line(it):
                        fails.append(
                            f"{tag}: a bullet restates the lead-in sentence verbatim "
                            f"(\"{it[:60]}…\") — keep one or the other, not both.")
    # --- THE PARAGRAPH AND THE BULLETS MUST SAY DIFFERENT THINGS -----------------
    # The pattern the reviewer found on nearly every slide: a lead-in sentence, then
    # bullets that say the same thing in other words —
    #     "Interrupt-driven I/O still burdens the CPU with copying each byte; DMA lets
    #      a dedicated controller transfer data directly."
    #       · DMA controller moves data memory-to-device directly
    #       · Frees CPU from byte-by-byte copying
    # Both bullets are the sentence again. The existing redundancy check only caught
    # VERBATIM repeats, so paraphrase — which is all of it in practice — passed.
    #
    # This costs coverage directly, which is why it is a failure and not a style note:
    # the document has a hard page ceiling, so a line that repeats is a line that
    # cannot teach something new. Measured on the documents generated so far, 11% of
    # all bullets were restating their own lead-in.
    #
    # The metric is the share of a bullet's distinctive words that already appear in one
    # of the paragraph's clauses. Bullet-side (not symmetric) on purpose: the question
    # is whether THIS bullet adds anything, not whether the sentence was fully consumed.
    if c_cfg.get("no_bullet_echoes_lead_in", False):
        thr = float(c_cfg.get("bullet_echo_overlap", 0.5))
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            clauses = [c for t in _text_blocks(s)
                       for c in re.split(r"[;.!?]", t) if len(_norm_tokens(c)) >= 3]
            if not clauses:
                continue
            for li, items in enumerate(_bullet_lists(s)):
                for bi, it in enumerate(items):
                    b_toks = _norm_tokens(it)
                    if len(b_toks) < 3:
                        continue
                    best, source = 0.0, None
                    for c in clauses:
                        shared = b_toks & _norm_tokens(c)
                        ratio = len(shared) / len(b_toks)
                        if len(shared) >= 2 and ratio > best:
                            best, source = ratio, c.strip()
                    if best >= thr:
                        fails.append(
                            f"{tag}: bullet {li + 1}.{bi + 1} repeats the paragraph above "
                            f"it ({best:.0%} of its words are already there) —\n"
                            f"    paragraph: {source[:110]}\n"
                            f"    bullet:    {it[:110]}\n"
                            f"    The paragraph and the bullets must carry DIFFERENT "
                            f"information. Keep the paragraph for what this is and why it "
                            f"matters, and make the bullets the specifics it does NOT "
                            f"state — the steps, the types, the values, the conditions, "
                            f"the trade-offs. A repeated line costs a line of coverage "
                            f"that the page ceiling will not give back.")

    if c_cfg.get("no_table_restated_as_bullets", False):
        # Verbatim cell matches were all this ever caught, and a bullet that re-says a
        # table row says it in prose, not word for word ("SSTF | 236 | starvation
        # possible" -> "SSTF totals 236 cylinders but can starve far requests"). So the
        # same overlap measure as the paragraph rule, bullet against whole row.
        tbl_thr = float(c_cfg.get("table_bullet_overlap", 0.6))
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            cells = {_norm_line(c) for tb in _tables(s)
                     for row in (tb.get("rows") or []) for c in row}
            cells.discard("")
            rows = [(" ".join(str(c) for c in row), _norm_tokens(" ".join(str(c) for c in row)))
                    for tb in _tables(s) for row in (tb.get("rows") or [])]
            for items in _bullet_lists(s):
                dup = [it for it in items if _norm_line(it) in cells]
                if len(dup) >= 2:            # 2+ overlaps = the table restated
                    fails.append(
                        f"{tag}: {len(dup)} bullet(s) restate cells of the table on the "
                        f"same slide — present the information once, as a table OR bullets.")
                    continue
                for it in items:
                    b_toks = _norm_tokens(it)
                    if len(b_toks) < 3:
                        continue
                    best, source = 0.0, None
                    for raw, r_toks in rows:
                        shared = b_toks & r_toks
                        ratio = len(shared) / len(b_toks)
                        if len(shared) >= 2 and ratio > best:
                            best, source = ratio, raw
                    if best >= tbl_thr:
                        fails.append(
                            f"{tag}: a bullet repeats a row of the table on the same "
                            f"slide ({best:.0%} of its words are already in it) —\n"
                            f"    table row: {source[:110]}\n"
                            f"    bullet:    {it[:110]}\n"
                            f"    The table already carries this. Delete the bullet, or "
                            f"make it say what the table cannot — why the numbers come "
                            f"out that way, when the choice flips, what it costs.")

    # --- THE SAME THING IS NOT TAUGHT ON TWO SLIDES ----------------------------
    # Everything above is within one slide. This is across the deck: the reviewer's
    # rule that "any concept, definition, criteria list, comparison table or
    # calculation must appear in exactly one place — intro-and-summary of the same
    # list, or re-deriving the same numbers in two sections, is not allowed".
    #
    # Threshold is well above the within-slide one (0.8 vs 0.5) because partial overlap
    # ACROSS slides is normal and wanted: the comparison slide names the criteria the
    # concept slide introduced. What 0.8 catches is the same line written twice.
    dup_cfg = con.get("duplication", {})
    if dup_cfg.get("check_across_slides", False):
        dthr = float(dup_cfg.get("near_duplicate_overlap", 0.8))
        dmin = int(dup_cfg.get("min_tokens", 5))
        # (slide n, the line, its tokens) for every teachable line in the document.
        lines: list[tuple] = []
        for s in slides:
            n = s.get("n", "?")
            for t in _text_blocks(s):
                for cl in re.split(r"[;.!?]", t):
                    toks = _norm_tokens(cl)
                    if len(toks) >= dmin:
                        lines.append((n, cl.strip(), toks))
            for items in _bullet_lists(s):
                for it in items:
                    toks = _norm_tokens(it)
                    if len(toks) >= dmin:
                        lines.append((n, str(it), toks))
        seen_pairs = set()
        for i, (n1, l1, t1) in enumerate(lines):
            for n2, l2, t2 in lines[i + 1:]:
                if n1 == n2:
                    continue                  # within one slide: covered above
                shared = t1 & t2
                # Symmetric here, unlike the bullet-vs-paragraph rule: neither line is
                # the "source", so the question is whether they are the same line, not
                # whether one was consumed by the other.
                ratio = len(shared) / max(len(t1), len(t2))
                if ratio < dthr:
                    continue
                key = (min(n1, n2, key=str), max(n1, n2, key=str), _norm_line(l1)[:40])
                if key in seen_pairs:
                    continue
                seen_pairs.add(key)
                fails.append(
                    f"Slides {n1} and {n2} teach the same thing twice "
                    f"({ratio:.0%} the same words) —\n"
                    f"    slide {n1}: {l1[:110]}\n"
                    f"    slide {n2}: {l2[:110]}\n"
                    f"    Keep it in ONE place — the slide where it is first needed — "
                    f"and delete the other. The page ceiling is fixed, so the second "
                    f"telling costs a line of coverage this document cannot get back.")

    # --- NO PADDING A THIN TOPIC INTO THREE SLIDES -----------------------------
    # "A single-line syllabus point (e.g. 'why X matters') gets at most 2 slides."
    # The failure mode is structural, not stylistic: the slide MINIMUM plus a takeaway
    # that names one idea leaves the model owing slides it has no material for, and it
    # pays with the same point under three titles. Measured against the curriculum line
    # itself — one sub-topic is one idea, whatever it was given.
    pad_cfg = con.get("padding", {})
    pad_max = int(pad_cfg.get("max_slides_for_single_point", 0) or 0)
    takeaways = list(getattr(session, "key_takeaways", []) or [])
    if pad_max and takeaways:
        for i, sec in enumerate(doc.get("sections") or []):
            if i >= len(takeaways):
                break
            subs = takeaway_subtopics(takeaways[i])
            got = len(sec.get("slides") or [])
            # "Names ONE point" is narrower than "has one sub-topic", and both edges
            # matter. A takeaway with NO colon yields no sub-topics at all — that is
            # "cannot tell", not "one idea", so it is left alone. And a lone sub-topic
            # that COORDINATES two things ("LOOK & C-LOOK", "IntServ and DiffServ") is
            # two ideas sharing a line: the splitter deliberately keeps "&" inside a
            # sub-topic, since "Bit & byte" really is one. Without this the gate fired
            # on a real, correct Session 32 section that teaches two algorithms.
            coordinated = len(subs) == 1 and re.search(
                r"\s(?:&|and|/|or|vs\.?|versus)\s", subs[0], re.I)
            if len(subs) == 1 and not coordinated and got > pad_max:
                fails.append(
                    f"Section {i + 1} \"{sec.get('name', '')}\" spends {got} slides on a "
                    f"takeaway that names ONE point (\"{takeaways[i][:70]}\") — max "
                    f"{pad_max}. One idea does not become three slides by being restated "
                    f"under three titles. Merge them into {pad_max}, and give the pages "
                    f"back to the takeaways that carry several sub-topics.")

    # --- slides are numbered 1..N, no gaps, no repeats --------------------------
    # pipeline.assemble renumbers the whole document; this asserts it worked. A gap or
    # a duplicate means a regenerated chunk changed length and the remap missed it,
    # which also silently invalidates every coverage_map slide reference.
    if con.get("numbering", {}).get("contiguous", False) and slides:
        nums = [s.get("n") for s in slides]
        if nums != list(range(1, len(nums) + 1)):
            fails.append(
                f"Slides are numbered {nums} — they must run 1..{len(nums)} with no "
                f"gaps or repeats. Renumber every slide in document order and update "
                f"the coverage_map references to match.")

    # --- speaker notes: 2 sentences, one cue + one exam hook -------------------
    max_ns = con.get("speaker_notes", {}).get("max_sentences")
    if max_ns:
        for s in slides:
            n = _sentence_count(s.get("speaker_notes"))
            if n > max_ns:
                fails.append(
                    f"Slide {s.get('n', '?')}: speaker_notes has {n} sentences "
                    f"(max {max_ns}) — one teaching cue plus one exam/interview hook, "
                    f"then stop.")

    # --- voice: no second person, no filler, no navigation in visible text -----
    v_cfg = con.get("voice", {})
    banned_you = v_cfg.get("banned_second_person", []) if not v_cfg.get("allow_second_person", True) else []
    banned_nav = v_cfg.get("banned_navigation", [])
    banned_filler = v_cfg.get("banned_filler", [])
    for s in slides:
        tag = f"Slide {s.get('n', '?')}"
        for fld, txt in _slide_visible_text(s):
            for grp, phrases, why in (
                    ("second person", banned_you,
                     "slide text is read, not spoken to someone — rewrite impersonally"),
                    ("navigational phrase", banned_nav,
                     "every slide must stand alone; move this to speaker_notes"),
                    ("filler", banned_filler, "cut it")):
                hits = _phrase_hits(txt, phrases)
                if hits:
                    fails.append(f"{tag}: {fld} contains {grp} "
                                 f"{', '.join(repr(x) for x in hits)} — {why}.")
        # Notes may keep continuity language, but still no second person.
        hits = _phrase_hits(s.get("speaker_notes"), banned_you)
        if hits:
            fails.append(
                f"{tag}: speaker_notes contains second person "
                f"{', '.join(repr(x) for x in hits)} — address the instructor's action, "
                f"not the learner.")

    # --- analogies must correlate, not just illustrate -------------------------
    if a_cfg.get("require_explicit_tie_back", False):
        connectives = a_cfg.get("tie_back_connectives", [])
        for s in slides:
            an = str(s.get("analogy") or "")
            if an and not any(c.lower() in an.lower() for c in connectives):
                fails.append(
                    f"Slide {s.get('n', '?')}: the analogy never ties back to the concept. "
                    f"End it with an explicit mapping — e.g. \"… — just as <how the concept "
                    f"works>\" — naming what it stands for.")

    # --- worked examples: only where one earns its slide -----------------------
    # depth_mode used to make a worked example MANDATORY on every doc, so definitional
    # takeaways ("what a file is", "types of scheduling") got a traced example that
    # taught nothing and cost a page. The judge scores whether each example was
    # WARRANTED; the deterministic part is the share cap — a deck that is mostly
    # examples has stopped teaching the concepts.
    we_cfg = con.get("worked_example", {})
    we_slides = [s for s in slides if roles.get(s.get("n")) == "working_example"]
    # ...but for an ALGORITHM session it is not optional, and one example must serve
    # all of them. "Any session teaching an algorithm (scheduling, replacement,
    # allocation, Banker's, etc.) must include a step-by-step worked example with a
    # concrete input and the computed result — and the same example reused across all
    # algorithms in that session for comparison."
    #
    # The second half is the one that gets dropped, and it is the one that matters:
    # three algorithms traced on three different request queues cannot be compared,
    # which is the whole reason a session teaches them together. One input, one results
    # table, and the comparison makes itself.
    if we_cfg.get("required_for_algorithm_sessions", False):
        markers = [m.lower() for m in we_cfg.get("algorithm_markers", [])]
        # The session must be ABOUT an algorithm, not merely mention one. The first cut
        # of this asked whether any marker appeared anywhere in the session's text, and
        # it fired on "SCTP & Quality of Service" — whose fifth takeaway is "Techniques
        # to Improve QoS: Scheduling, Traffic Shaping". One sub-topic of one takeaway is
        # a topic that gets a slide, not a session that owes a traced example.
        # So: the session TITLE names one, or at least two separate takeaway lines do.
        name_l = str(getattr(session, "name", "") or "").lower()
        kts = [str(t).lower() for t in getattr(session, "key_takeaways", []) or []]
        in_title = [m for m in markers if m in name_l]
        kt_hits = [m for kt in kts for m in markers if m in kt]
        kt_lines = sum(1 for kt in kts if any(m in kt for m in markers))
        hits = in_title or (kt_hits if kt_lines >= 2 else [])
        if not hits and kt_lines == 1 and not we_slides:
            # Named, not failed: one takeaway may well deserve a traced example, but
            # "types of X, one of which is an algorithm" often does not, and only the
            # judge can tell those apart.
            warns.append(
                f"A takeaway names an algorithm ({', '.join(sorted(set(kt_hits))[:2])}) "
                f"and no slide works one through — if the learner is expected to EXECUTE "
                f"it, it needs a concrete input traced to a computed result.")
        if hits:
            if not we_slides:
                fails.append(
                    f"This session teaches an algorithm ({', '.join(hits[:3])}) and no "
                    f"slide works one through. Add a slide with role 'working_example': "
                    f"a concrete input (a request queue, a reference string, an "
                    f"allocation matrix), the steps applied in order, and the computed "
                    f"result — total head movement, fault count, whether the state is "
                    f"safe. State any assumption that changes the answer (initial head "
                    f"position and direction, frame count, tie-breaking).")
            elif we_cfg.get("shared_input_across_algorithms", False) and len(we_slides) > 1:
                need = int(we_cfg.get("min_shared_values", 3))
                vals = [(s.get("n", "?"), set(_NUMERIC.findall(_slide_text_blob(s))))
                        for s in we_slides]
                base_n, base = vals[0]
                odd = [n for n, v in vals[1:] if len(v & base) < need]
                if odd:
                    fails.append(
                        f"Worked example(s) on slide(s) {odd} use a different input from "
                        f"the one on slide {base_n} (fewer than {need} values in common). "
                        f"Every algorithm in this session must be traced on the SAME "
                        f"input — the same request queue / reference string / allocation "
                        f"state — so their results can be compared side by side. Reuse "
                        f"slide {base_n}'s input and end with one table of results.")

    we_cap = we_cfg.get("max_share_of_slides")
    if we_cap and slides and len(we_slides) > we_cap * len(slides):
        fails.append(
            f"{len(we_slides)} of {len(slides)} slides are worked examples "
            f"(max {we_cap:.0%}) — keep the examples that let the learner EXECUTE "
            f"something and fold the rest back into the concept slides.")

    # --- examples must use realistic, concrete figures -------------------------
    # A toy number teaches a toy mental model: "base = 5" is not an address. The
    # magnitude/shape judgement is the judge's; deterministically we require that a
    # worked example carries real values at all, and that none of them is a stand-in.
    ex_cfg = con.get("examples", {})
    if ex_cfg.get("require_realistic_figures", False):
        min_lits = ex_cfg.get("min_numeric_literals", 2)
        placeholders = ex_cfg.get("banned_placeholders", [])
        for s in we_slides:
            tag = f"Slide {s.get('n', '?')}"
            blob = _slide_text_blob(s)
            found = _NUMERIC.findall(blob)
            if min_lits and len(found) < min_lits:
                fails.append(
                    f"{tag}: a worked example with {len(found)} concrete value(s) "
                    f"(min {min_lits}) is not worked through. Use realistic figures — "
                    f"a hex base address, a power-of-two page size, a real port/PID — "
                    f"and trace them step by step.")
        # Placeholders are checked on EVERY slide: a vague stand-in is just as bad in a
        # mechanism explanation as in a dedicated example slide.
        for s in slides:
            hits = _phrase_hits(_slide_text_blob(s), placeholders)
            if hits:
                fails.append(
                    f"Slide {s.get('n', '?')}: placeholder figure(s) "
                    f"{', '.join(repr(x) for x in hits)} — substitute a realistic "
                    f"value a practitioner would recognise.")

    # --- coverage map: the sub-concept enumeration, VERIFIED -------------------
    # The prompt has asked for this enumeration since 1.24, but only in the model's
    # head, so "I forgot one" stayed invisible. Emitting it turns a silent omission
    # into a checkable claim: the map says slide 9 teaches minor-vs-major faults, so
    # either slide 9 exists or the run fails.
    cov_cfg = con.get("coverage", {})
    if cov_cfg.get("require_coverage_map", False):
        cmap = doc.get("coverage_map")
        src_kt = list(session.key_takeaways)
        slide_ns = {s.get("n") for s in slides}
        min_subs = cov_cfg.get("min_sub_concepts_per_takeaway", 2)
        # Which slides belong to the section that teaches takeaway i. Resolved BY NAME,
        # because that is the rule (a section's name is its key-takeaway line verbatim)
        # and it is what makes the check sound: where a section cannot be matched to its
        # takeaway the naming gate already fails, and scoping a coverage reference to a
        # section we only GUESSED at would produce false failures — the golden, whose
        # four grouped sections predate the verbatim rule, is exactly that case.
        by_name = {_norm_line(sec.get("name")): sec for sec in doc.get("sections") or []}
        own_section: dict[int, set] = {}
        for i, kt in enumerate(src_kt):
            sec = by_name.get(_norm_line(kt))
            if sec is not None:
                own_section[i] = {s.get("n") for s in (sec.get("slides") or [])}
        if not isinstance(cmap, list) or not cmap:
            fails.append(
                "Missing 'coverage_map'. For EACH key takeaway, list the exam-testable "
                "sub-concepts and map each one to the slide 'n' that teaches it (or to "
                "a named deferral). This is the check that catches a silently missing "
                "sub-concept.")
        else:
            if len(cmap) != len(src_kt):
                fails.append(
                    f"coverage_map has {len(cmap)} entries but the curriculum has "
                    f"{len(src_kt)} key takeaways — one entry per takeaway, in order.")
            for i, entry in enumerate(cmap):
                where = f"coverage_map[{i + 1}]"
                if not isinstance(entry, dict):
                    fails.append(f"{where} is not an object.")
                    continue
                if i < len(src_kt) and _norm_line(entry.get("takeaway")) != _norm_line(src_kt[i]):
                    fails.append(
                        f"{where}.takeaway must be key takeaway {i + 1} verbatim:\n"
                        f"    expected: {src_kt[i]}\n    got:      {entry.get('takeaway')}")
                subs = entry.get("sub_concepts") or []
                if len(subs) < min_subs:
                    fails.append(
                        f"{where} lists {len(subs)} sub-concept(s) (min {min_subs}) — a "
                        f"syllabus line names a topic, not its scope. Enumerate what an "
                        f"exam would actually test on it.")

                # --- DEFERRAL IS A LAST RESORT, NOT A RELEASE VALVE ----------------
                # `deferred_to` exists so a sub-concept belonging to a later session is
                # named rather than dropped silently. Nothing stopped it being used to
                # make room, though — and a takeaway whose sub-concepts are mostly
                # "deferred" has not been taught, it has been postponed while every
                # other gate stayed green.
                if subs:
                    n_def = sum(1 for s in subs if isinstance(s, dict)
                                and str(s.get("deferred_to") or "").strip()
                                and s.get("slide") in (None, ""))
                    max_share = cov_cfg.get("max_deferred_share_per_takeaway", 0.34)
                    if n_def == len(subs):
                        fails.append(
                            f"{where}: every one of its {n_def} sub-concept(s) is deferred "
                            f"to a later session, so takeaway {i + 1} is not taught in this "
                            f"session at all. The agenda promises it — teach it here.")
                    elif max_share and n_def > max_share * len(subs):
                        fails.append(
                            f"{where}: {n_def} of {len(subs)} sub-concepts are deferred "
                            f"(max {max_share:.0%}). Deferral is for material that genuinely "
                            f"belongs to a later session, never for making room — group "
                            f"closely-related sub-concepts onto one slide instead.")
                for j, sub in enumerate(subs):
                    at = f"{where}.sub_concepts[{j + 1}]"
                    if not isinstance(sub, dict) or not str(sub.get("name") or "").strip():
                        fails.append(f"{at} has no 'name'.")
                        continue
                    name = str(sub["name"]).strip()
                    slide_ref, deferred = sub.get("slide"), sub.get("deferred_to")
                    if slide_ref in (None, "") and not str(deferred or "").strip():
                        fails.append(
                            f"{at} \"{name}\" is mapped to neither a slide nor a named "
                            f"deferral — cover it, or say in the section which later "
                            f"session covers it and record that here as 'deferred_to'.")
                        continue
                    if slide_ref not in (None, ""):
                        try:
                            ref = int(slide_ref)
                        except (TypeError, ValueError):
                            fails.append(f"{at} \"{name}\": slide '{slide_ref}' is not a "
                                         f"slide number.")
                            continue
                        if ref not in slide_ns:
                            fails.append(
                                f"{at} \"{name}\" claims slide {ref}, which does not "
                                f"exist (slides are {sorted(n for n in slide_ns if n is not None)}). "
                                f"Add the slide or correct the map.")
                        elif i in own_section and ref not in own_section[i]:
                            # A reference that RESOLVES but points OUTSIDE the section
                            # teaching this takeaway. Checking only that the slide exists
                            # let this through silently, and it is the exact defect the
                            # judge kept reporting as a coverage failure: "sub-concept
                            # mapped to Slide 2, but Slide 2 does not teach it — it is on
                            # Slide 5". In guided mode it is also what a stale slide
                            # number looks like after a chunk was regenerated at a
                            # different length. Section i is the one whose name IS
                            # takeaway i, so the correct slide can only be in it.
                            fails.append(
                                f"{at} \"{name}\" maps to slide {ref}, which is not in "
                                f"the section teaching takeaway {i + 1} (its slides are "
                                f"{sorted(own_section[i])}). Point it at the slide that "
                                f"actually teaches this sub-concept, or teach it there.")
            # --- NOTHING OFF THE AGENDA -------------------------------------------
            # A slide nothing in the map points at is a slide teaching something the
            # session never promised. This used to be a warning, and warnings do not
            # reach the revision pass — so off-agenda slides shipped, which is exactly
            # what the reviewer kept striking out. Now it fails, with a named exception
            # for the roles that legitimately serve several sub-concepts at once (the
            # section's opening landscape, a contrast table, a consolidating summary).
            mapped = {int(sub["slide"]) for entry in cmap if isinstance(entry, dict)
                      for sub in (entry.get("sub_concepts") or [])
                      if isinstance(sub, dict) and str(sub.get("slide") or "").strip().isdigit()}
            orphans = sorted(n for n in slide_ns if n is not None and n not in mapped)
            if orphans:
                allowed = set(cov_cfg.get("unmapped_slide_roles_allowed", []))
                by_n = {s.get("n"): s for s in slides}
                off = [n for n in orphans
                       if (roles.get(n) or str((by_n.get(n) or {}).get("role") or "")) not in allowed]
                if cov_cfg.get("every_slide_mapped", False) and off:
                    titles = "; ".join(
                        f"{n} \"{(by_n.get(n) or {}).get('title', '?')}\"" for n in off)
                    fails.append(
                        f"Slide(s) {off} teach nothing the coverage map points at, so "
                        f"nothing on the agenda promised them: {titles}. Either map each "
                        f"one to the exam-testable sub-concept it teaches under its "
                        f"takeaway, or CUT it — an adjacent topic spends the session's "
                        f"budget on something the learner was not promised. (Only "
                        f"{', '.join(sorted(allowed)) or 'no'} roles may stand unmapped.)")
                elif off:
                    warns.append(
                        f"Slide(s) {off} are not referenced by any sub-concept in the "
                        f"coverage map — worth a look; that is what off-agenda content "
                        f"looks like.")

    # --- 100% OF EACH TAKEAWAY: the sub-topics the CURRICULUM LINE itself names ----
    # Everything checked above measures the doc against the model's OWN enumeration of
    # sub-concepts, so a promise the curriculum made and the model never enumerated was
    # invisible to every gate. The sheet writes a takeaway as
    #   "2. Number Systems: Decimal notation & radix / base, Binary notation; counting
    #    in binary"
    # — each item after the colon is owed to the learner. This checks the section that
    # teaches takeaway i actually mentions each of them, matched on distinctive
    # vocabulary (de-pluralised) across the section's slides AND its coverage entries,
    # so a sub-topic taught under a different wording still counts.
    if cov_cfg.get("takeaway_subtopics_must_be_taught", False):
        thresh = float(cov_cfg.get("subtopic_token_match", 0.6))
        src_kt = list(session.key_takeaways)
        by_name = {_norm_line(sec.get("name")): sec for sec in doc.get("sections") or []}
        cmap = doc.get("coverage_map") if isinstance(doc.get("coverage_map"), list) else []
        for i, kt in enumerate(src_kt):
            subtopics = takeaway_subtopics(kt)
            if not subtopics:
                continue                      # no colon -> the line promises no items
            sec = by_name.get(_norm_line(kt))
            # Prefer the section that teaches this takeaway. When no section carries the
            # takeaway's name (the naming gate reports that separately) fall back to the
            # WHOLE document rather than skipping: a promise the curriculum made must be
            # checked even when the section titles have drifted — skipping is how a
            # reworded name would silently switch this check off.
            if sec is not None:
                scope = sec.get("slides") or []
                where = f'section "{sec.get("name")}"'
            else:
                scope = slides
                where = f"this document (no section is named after takeaway {i + 1})"
            blob = " ".join(_slide_text_blob(s) for s in scope)
            if i < len(cmap) and isinstance(cmap[i], dict):
                blob += " " + " ".join(str(sub.get("name") or "")
                                       for sub in (cmap[i].get("sub_concepts") or [])
                                       if isinstance(sub, dict))
            have = _norm_tokens(blob)
            missing = [st for st in subtopics if not _covers(st, have, thresh)]
            if missing:
                quoted = "; ".join(f'"{m}"' for m in missing)
                fails.append(
                    f"Takeaway {i + 1} promises sub-topic(s) that are never taught: "
                    f"{quoted}. The curriculum line names them, so the session owes them "
                    f"to the learner — add a slide (or fold them into an existing one) "
                    f"in {where}. Do not defer or drop a sub-topic the takeaway "
                    f"itself names.")

    # --- DO NOT RE-TEACH WHAT AN EARLIER SESSION ALREADY TAUGHT -------------------
    # The whole reason prior decks are ingested. Until now nothing checked it: the
    # instruction lived in the prompt, and the judge scored "no repetition" without
    # ever being shown what the earlier sessions contained.
    #
    # Deliberately narrow, because "covers the same ground" and "re-teaches" are not the
    # same thing — the curriculum legitimately revisits a topic to go deeper, and the
    # takeaway-coverage gate above REQUIRES that. So only one case is unambiguous
    # enough to fail: a slide that introduces a concept (role concept_intro) under a
    # title an earlier session's deck already used. That is re-running the introduction.
    # Everything else that looks like overlap is surfaced as a warning and handed to the
    # judge, which now receives the same already-taught digest and can tell "goes
    # deeper" from "says it again".
    rep_cfg = con.get("repetition", {})
    if rep_cfg.get("check_prior_decks", False) and getattr(session, "number", None):
        try:
            from src import pptx_ingest
            prior = pptx_ingest.taught_titles(session.number)
        except Exception:
            prior = []
        if prior:
            prior_toks = [(sn, t, _norm_tokens(t)) for sn, t in prior]
            near = float(rep_cfg.get("near_duplicate_jaccard", 0.7))
            for s in slides:
                title = str(s.get("title") or "")
                t_toks = _norm_tokens(title)
                if len(t_toks) < 2:
                    continue                  # a one-word title collides by accident
                best = None
                for sn, ptitle, p_toks in prior_toks:
                    if len(p_toks) < 2:
                        continue
                    union = t_toks | p_toks
                    j = len(t_toks & p_toks) / len(union) if union else 0.0
                    if best is None or j > best[0]:
                        best = (j, sn, ptitle)
                if not best:
                    continue
                j, sn, ptitle = best
                role = roles.get(s.get("n")) or str(s.get("role") or "")
                if j >= 1.0 and role == "concept_intro":
                    fails.append(
                        f"Slide {s.get('n', '?')} \"{title}\" introduces a concept Session "
                        f"{sn} already introduced under the same title — the learner has "
                        f"been taught this. Either build on it (a deeper mechanism, the "
                        f"case that session did not cover, with role changed from "
                        f"concept_intro) or drop the slide and use the recap line.")
                elif j >= near:
                    warns.append(
                        f"Slide {s.get('n', '?')} \"{title}\" closely matches Session {sn}'s "
                        f"slide \"{ptitle}\" — make sure this goes BEYOND what was already "
                        f"taught rather than repeating it.")

    # --- AND DO NOT TEACH WHAT THE NEXT SESSION IS FOR ---------------------------
    # The other edge of "coverage = syllabus, no more". The gate above guards the past;
    # this guards the future. Same shape deliberately: an outright INTRODUCTION of a
    # next-session topic fails, anything less is a warning — because naming a topic in
    # one forward-looking line is explicitly allowed, and the Upcoming Session line
    # requires it.
    leak_cfg = con.get("leakage", {})
    nxt_takeaways = list(getattr(session, "next_key_takeaways", []) or [])
    if leak_cfg.get("check_next_session", False) and nxt_takeaways:
        thr = float(leak_cfg.get("title_match", 0.8))
        # Sub-topics, not whole takeaway lines: a takeaway names several, and it is one
        # of those a leaking slide is built on.
        future = []
        for line in nxt_takeaways:
            for sub in (takeaway_subtopics(line) or [line]):
                toks = _norm_tokens(sub)
                if len(toks) >= 2:
                    future.append((sub, toks))
        for s in slides:
            title = str(s.get("title") or "")
            t_toks = _norm_tokens(title)
            if len(t_toks) < 2:
                continue
            best = (0.0, "")
            for sub, f_toks in future:
                union = t_toks | f_toks
                j = len(t_toks & f_toks) / len(union) if union else 0.0
                if j > best[0]:
                    best = (j, sub)
            j, sub = best
            if j < thr:
                continue
            role = roles.get(s.get("n")) or str(s.get("role") or "")
            if role in ("concept_intro", "overview"):
                fails.append(
                    f"Slide {s.get('n', '?')} \"{title}\" introduces \"{sub}\" — that is "
                    f"the NEXT session's material, and it has its own session to teach "
                    f"it properly. Drop the slide; if the connection is needed, one "
                    f"forward-looking line in the Upcoming Session field is enough. The "
                    f"pages belong to this session's takeaways.")
            else:
                warns.append(
                    f"Slide {s.get('n', '?')} \"{title}\" overlaps the next session's "
                    f"\"{sub}\" — keep it to what THIS session needs.")

    passed = len(fails) == 0
    if gates.get("structural_pass") is True and not passed:
        pass  # already reflected in fails
    return GuardrailResult(passed=passed, failures=fails, warnings=warns)


# --------------------------------------------------------------------------- #
# WHAT THE CODE ALREADY KNOWS — handed to the LLM judge as ground truth
# --------------------------------------------------------------------------- #
def deterministic_facts(doc: dict) -> dict:
    """Per-slide counts for the rules this module checks MECHANICALLY.

    WHY THIS EXISTS. The judge scored session 33's slide_content_style 3/5 — below the
    per-dimension bar, which fails the run on its own — on four cited grounds, and three
    of them were false against the document it was grading:

      · "Slide 9 has a one-item bullet list"   — slide 9 has FOUR items, and no slide in
        that document was under the three-item minimum;
      · "Slide 2 speaker_notes … violates the speaker_notes rule" — two sentences, no
        question mark, and the second sentence is the exam hook the rule asks for;
      · the same claim again about slide 19, which is also correctly shaped.

    Every one of those is a rule with a deterministic gate a few hundred lines up, and
    every gate had already PASSED on that document. The judge was simply counting wrong,
    and a 3/5 on an 8-weight dimension discarded a document the reviewer had approved.

    Time and pages were never judged this way: both are handed over as
    "DETERMINISTIC … (ground truth for this dimension)" precisely so the judge grades
    against a measured number instead of its own estimate. This is the same treatment
    for the countable structure rules — the judge is told what the code measured, so a
    dimension about style is scored on style rather than on arithmetic.

    Reports only what is countable and already gated. Nothing here is an opinion.
    """
    con = config.harness()["constraints"]
    c_cfg = con.get("content", {}) or {}
    min_items = c_cfg.get("min_bullet_items")
    max_ns = (con.get("speaker_notes", {}) or {}).get("max_sentences")
    max_cw = c_cfg.get("max_words_per_text_block")
    max_cs = c_cfg.get("max_sentences_per_text_block")

    per_slide = []
    for s in _slides(doc):
        lists = _bullet_lists(s)
        texts = _text_blocks(s)
        per_slide.append({
            "n": s.get("n"),
            "role": s.get("role"),
            "bullet_list_sizes": [len(x) for x in lists],
            "speaker_notes_sentences": _sentence_count(s.get("speaker_notes")),
            "has_analogy": bool(s.get("analogy")),
            "text_block_words": [_word_count(t) for t in texts],
            "text_block_sentences": [_sentence_count(t) for t in texts],
        })

    def _all(pred) -> bool:
        return all(pred(r) for r in per_slide)

    rules = []
    if min_items:
        rules.append({
            "rule": f"every bullet list has at least {min_items} items "
                    f"(a shorter list is a bulleted sentence)",
            "passed": _all(lambda r: all(n >= min_items for n in r["bullet_list_sizes"])),
            "field": "bullet_list_sizes"})
    if max_ns:
        rules.append({
            "rule": f"speaker_notes is at most {max_ns} sentences "
                    f"(one teaching cue + one exam/interview hook)",
            "passed": _all(lambda r: r["speaker_notes_sentences"] <= max_ns),
            "field": "speaker_notes_sentences"})
    if max_cw:
        rules.append({
            "rule": f"every prose text block is at most {max_cw} words",
            "passed": _all(lambda r: all(w <= max_cw for w in r["text_block_words"])),
            "field": "text_block_words"})
    if max_cs:
        rules.append({
            "rule": f"every prose text block is at most {max_cs} sentences",
            "passed": _all(lambda r: all(n <= max_cs for n in r["text_block_sentences"])),
            "field": "text_block_sentences"})
    return {"rules": rules, "per_slide": per_slide}
