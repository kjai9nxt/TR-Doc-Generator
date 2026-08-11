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
          *, rich: bool = False) -> GuardrailResult:
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
    slide_max = con["slides"].get("max_rich", con["slides"]["max"]) if rich else con["slides"]["max"]
    if len(slides) < con["slides"]["min"]:
        fails.append(f"Only {len(slides)} slides (min {con['slides']['min']}).")
    if len(slides) > slide_max:
        fails.append(f"{len(slides)} slides (max {slide_max}) — split content, don't cram.")

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
    if c_cfg.get("no_table_restated_as_bullets", False):
        for s in slides:
            tag = f"Slide {s.get('n', '?')}"
            cells = {_norm_line(c) for tb in _tables(s)
                     for row in (tb.get("rows") or []) for c in row}
            cells.discard("")
            for items in _bullet_lists(s):
                dup = [it for it in items if _norm_line(it) in cells]
                if len(dup) >= 2:            # 2+ overlaps = the table restated
                    fails.append(
                        f"{tag}: {len(dup)} bullet(s) restate cells of the table on the "
                        f"same slide — present the information once, as a table OR bullets.")

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
            # A slide nothing in the map points at is not a failure — a comparison or
            # summary slide legitimately consolidates several sub-concepts — but it is
            # worth surfacing, because it is also what padding looks like.
            mapped = {int(sub["slide"]) for entry in cmap if isinstance(entry, dict)
                      for sub in (entry.get("sub_concepts") or [])
                      if isinstance(sub, dict) and str(sub.get("slide") or "").strip().isdigit()}
            orphans = sorted(n for n in slide_ns if n is not None and n not in mapped)
            if orphans:
                warns.append(
                    f"Slide(s) {orphans} are not referenced by any sub-concept in the "
                    f"coverage map — fine for a comparison or summary slide, worth a "
                    f"look otherwise.")

    passed = len(fails) == 0
    if gates.get("structural_pass") is True and not passed:
        pass  # already reflected in fails
    return GuardrailResult(passed=passed, failures=fails, warnings=warns)
