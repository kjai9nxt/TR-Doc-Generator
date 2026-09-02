"""Apply a model-authored PATCH to a previously generated guided chunk.

WHY THIS EXISTS. Regenerating one section used to re-draft the whole thing from the
reviewer's note, so "drop the analogy on the example slide" came back with five
rewritten slides — including the four the reviewer had already accepted. Every
regeneration was a fresh roll of the dice on approved content.

No prompt can fix that. "Change only slide 3 and return everything else byte-identical"
asks the model to reproduce a thousand words exactly, which it will not reliably do.
So the model does not return the section any more: it returns a PATCH naming what to
change, and this module applies it. Anything the patch does not name is not passed
through the model at all — it is the same Python object as before, so it cannot drift.

PATCH SCHEMA (section chunk)
    {
      "section_name": "<new name>" | null,        // null / absent = unchanged
      "edit_slides": [
        {"n": 3, "fields": {"heading": "...",     // replace these fields only
                            "analogy": null}}     // null DELETES the field
      ],
      "add_slides":   [{"after_n": 3, "slide": {...}}],   // after_n null = prepend
      "remove_slides": [4],
      "note": "<one line: what changed and why>"
    }

PATCH SCHEMA (opening chunk)
    {"set_fields": {"recap": {...}} , "note": "..."}      // whole-field replacement

Slide numbering is deliberately NOT fixed up here — `pipeline.assemble_doc` renumbers
the whole document once every chunk is final, which is the only place that can see all
the sections at once.
"""
from __future__ import annotations
import copy


class PatchError(ValueError):
    """The patch could not be applied. The caller falls back to a full re-draft."""


def _section_of(fragment: dict) -> dict:
    """Guided section fragments come as {"section": {...}} but a bare section dict has
    been seen too; accept both, the same way server/pipeline do."""
    if isinstance(fragment.get("section"), dict):
        return fragment["section"]
    return fragment


def _apply_fields(slide: dict, fields: dict) -> list[str]:
    """Replace named fields on one slide. A null value DELETES the field, which is how
    "remove the analogy from this slide" — the single most common reviewer note — is
    expressed without rewriting the slide."""
    touched = []
    for key, value in (fields or {}).items():
        if key == "n":
            continue                     # numbering is assigned, never patched
        if value is None:
            if key in slide:
                del slide[key]
                touched.append(f"-{key}")
        else:
            slide[key] = value
            touched.append(key)
    return touched


def apply_section_patch(prev_fragment: dict, patch: dict) -> tuple[dict, dict]:
    """Return (new_fragment, summary). Raises PatchError if the patch is unusable.

    summary reports the scope of the edit — which slides changed, which were left
    untouched — so an over-broad "patch" is visible in the run log instead of passing
    for surgery.
    """
    if not isinstance(patch, dict):
        raise PatchError("patch is not a JSON object")

    fragment = copy.deepcopy(prev_fragment)
    section = _section_of(fragment)
    slides = section.get("slides")
    if not isinstance(slides, list):
        raise PatchError("previous chunk has no slides list to patch")

    by_n = {s.get("n"): s for s in slides if isinstance(s, dict)}
    changed: dict[int, list[str]] = {}
    removed: list[int] = []
    added: list[int] = []
    renamed = False

    # --- section name ---
    new_name = patch.get("section_name")
    if new_name and str(new_name).strip() and str(new_name) != str(section.get("name")):
        section["name"] = str(new_name)
        renamed = True

    # --- field edits ---
    for edit in patch.get("edit_slides") or []:
        if not isinstance(edit, dict):
            raise PatchError("edit_slides entry is not an object")
        n = edit.get("n")
        target = by_n.get(n)
        if target is None:
            raise PatchError(
                f"edit_slides names slide {n!r}, which is not in this section "
                f"(it has {sorted(k for k in by_n if k is not None)})")
        touched = _apply_fields(target, edit.get("fields") or {})
        if touched:
            changed[n] = touched

    # --- removals (before insertions, so after_n refers to the original numbering) ---
    to_remove = set()
    for n in patch.get("remove_slides") or []:
        if n not in by_n:
            raise PatchError(f"remove_slides names slide {n!r}, which is not in this section")
        to_remove.add(n)
    if to_remove:
        slides[:] = [s for s in slides if s.get("n") not in to_remove]
        removed = sorted(to_remove)

    # --- insertions ---
    for add in patch.get("add_slides") or []:
        if not isinstance(add, dict) or not isinstance(add.get("slide"), dict):
            raise PatchError("add_slides entry needs a 'slide' object")
        new_slide = copy.deepcopy(add["slide"])
        after = add.get("after_n")
        if after in (None, "", 0):
            pos = 0
        else:
            pos = next((i + 1 for i, s in enumerate(slides) if s.get("n") == after), None)
            if pos is None:
                raise PatchError(
                    f"add_slides wants to insert after slide {after!r}, which is not in "
                    f"this section")
        # n is provisional: assemble_doc renumbers the whole document at the end.
        new_slide.setdefault("n", None)
        slides.insert(pos, new_slide)
        added.append(after)

    if not (changed or removed or added or renamed):
        raise PatchError("patch is empty — nothing was changed")

    total = len(prev_fragment and _section_of(prev_fragment).get("slides") or [])
    touched_count = len(changed) + len(removed) + len(added)
    summary = {
        "mode": "patch",
        "section_renamed": renamed,
        "slides_total": total,
        "slides_changed": sorted(changed),
        "fields_changed": {str(k): v for k, v in changed.items()},
        "slides_removed": removed,
        "slides_added": len(added),
        "slides_untouched": sorted(n for n in by_n
                                   if n is not None and n not in changed
                                   and n not in set(removed)),
        "changed_share": round(touched_count / total, 2) if total else 1.0,
        "note": str(patch.get("note") or "").strip()[:300],
    }
    return fragment, summary


def apply_opening_patch(prev_fragment: dict, patch: dict) -> tuple[dict, dict]:
    """The opening chunk is just {recap, agenda}. Both are verbatim-constrained, so a
    patch here is a whole-field replacement rather than a per-slide edit."""
    if not isinstance(patch, dict):
        raise PatchError("patch is not a JSON object")
    fields = patch.get("set_fields")
    if not isinstance(fields, dict) or not fields:
        raise PatchError("opening patch needs a non-empty 'set_fields' object")
    fragment = copy.deepcopy(prev_fragment)
    changed = []
    for key, value in fields.items():
        if key not in ("recap", "agenda"):
            raise PatchError(f"opening patch may only set 'recap' or 'agenda', not {key!r}")
        fragment[key] = value
        changed.append(key)
    return fragment, {
        "mode": "patch",
        "fields_changed": changed,
        "changed_share": round(len(changed) / 2, 2),
        "note": str(patch.get("note") or "").strip()[:300],
    }


# --------------------------------------------------------------------------- #
# SPLITTING ONE SLIDE IN TWO — a reviewer's structural edit, not the model's
# --------------------------------------------------------------------------- #
# A slide carrying too much for one slide is the commonest structural note a reviewer
# has, and until now the only way to act on it was to regenerate the chunk with "split
# this" and hope. That is the wrong tool twice over: it costs a model call to move
# content that already exists, and a re-draft is free to rewrite the slides the reviewer
# had already accepted.
#
# So the split is DETERMINISTIC and local. The content the reviewer approved is moved,
# not rewritten — no model call, nothing to drift. The second slide inherits the fields
# every slide is required to carry (heading, subheading, visual guidance, speaker notes)
# so the result still satisfies the per-slide gates, and the reviewer can polish it with
# an ordinary Regenerate afterwards if the inherited wording does not fit.
#
# Two gates decide the details, and both are honoured here rather than discovered at
# finalize:
#   · analogy is required iff role == concept_intro and BANNED on every other role, and
#     the same analogy may not appear on two slides — so the analogy stays on the first
#     half and the second half is given a non-intro role;
#   · a title is capped at constraints.headings.title_max_words, so " (continued)" is
#     appended only after trimming the title to fit.
_CONTINUED = "(continued)"


def _sentences(text: str) -> list[str]:
    """Split prose into sentences, keeping their terminators."""
    import re
    parts = re.findall(r"[^.!?]+[.!?]+|\S[^.!?]*$", str(text or "").strip())
    return [p.strip() for p in parts if p.strip()]


def _split_content(content: list, *, min_bullet_items: int = 3) -> tuple[list, list]:
    """Divide one slide's ordered content blocks into (first half, second half).

    Raises PatchError with a SPECIFIC reason when it cannot be divided — splitting a
    slide into a full half and an empty one is not a split, and neither is one that
    leaves a two-item bullet list the deck gate will reject at finalize. Saying which is
    the difference between a reviewer who knows what to do next and one who does not.

    Where the slide has several blocks, the PROSE is divided too rather than handed
    wholesale to one half: the document is gated on the share of slides carrying a text
    block (constraints.content.min_slides_with_text_share), so a continuation made
    entirely of bullets drags that share down for a structural edit that was meant to be
    neutral.
    """
    blocks = [b for b in (content or []) if isinstance(b, dict)]
    if not blocks:
        raise PatchError("this slide has no content to divide.")

    if len(blocks) == 1:
        b = blocks[0]
        kind = b.get("type")
        if kind == "bullets":
            items = [i for i in (b.get("items") or []) if str(i).strip()]
            need = 2 * max(1, int(min_bullet_items or 1))
            if len(items) < need:
                raise PatchError(
                    f"this slide is a single list of {len(items)} item(s), and every list "
                    f"must keep at least {min_bullet_items} — so splitting one needs "
                    f"{need}. Regenerate the chunk with a note asking for this point to be "
                    f"taught across two slides instead.")
            half = (len(items) + 1) // 2
            return [{**b, "items": items[:half]}], [{**b, "items": items[half:]}]
        if kind == "table":
            rows = list(b.get("rows") or [])
            if len(rows) < 2:
                raise PatchError(
                    "this slide is a single table with one row — there is nothing to "
                    "divide. Regenerate the chunk with a note asking for the point to be "
                    "taught across two slides instead.")
            half = (len(rows) + 1) // 2
            # Both halves keep the header columns — a table without them is unreadable.
            return [{**b, "rows": rows[:half]}], [{**b, "rows": rows[half:]}]
        if kind == "text":
            sents = _sentences(b.get("text"))
            if len(sents) < 2:
                raise PatchError(
                    "this slide is a single sentence of prose — there is nothing to "
                    "divide. Regenerate the chunk with a note asking for the point to be "
                    "taught across two slides instead.")
            half = (len(sents) + 1) // 2
            return ([{**b, "text": " ".join(sents[:half])}],
                    [{**b, "text": " ".join(sents[half:])}])
        raise PatchError(
            f"this slide's content is a single '{kind}' block, which cannot be divided. "
            f"Regenerate the chunk with a note asking for the point to be taught across "
            f"two slides instead.")

    # SEVERAL BLOCKS. Divide them, and divide the prose across both halves when it can be
    # divided, so neither slide ends up without any.
    text_i = next((i for i, b in enumerate(blocks)
                   if b.get("type") == "text" and len(_sentences(b.get("text"))) >= 2),
                  None)
    others = [i for i in range(len(blocks)) if i != text_i]
    to_second = set(others[(len(others) + 1) // 2:])
    first, second = [], []
    for i, b in enumerate(blocks):
        if i == text_i:
            sents = _sentences(b.get("text"))
            half = (len(sents) + 1) // 2
            first.append({**b, "text": " ".join(sents[:half])})
            second.append({**b, "text": " ".join(sents[half:])})
        elif i in to_second:
            second.append(b)
        else:
            first.append(b)
    if not first or not second:
        # Only reachable when there is no divisible prose AND every other block landed on
        # one side — two blocks with an indivisible text block, say.
        half = (len(blocks) + 1) // 2
        first, second = blocks[:half], blocks[half:]
    return first, second


def _continued_title(title: str, max_words: int) -> str:
    """`title` + " (continued)", trimmed to the title word cap.

    The cap is a hard gate, so appending a word to an already-full title would fail the
    run at finalize for a structural edit the reviewer made by hand.
    """
    words = [w for w in str(title or "").split() if w]
    room = max(1, int(max_words) - 1)          # one word for "(continued)"
    return " ".join(words[:room] + [_CONTINUED]) if words else _CONTINUED


def _next_free_n(slides: list) -> int:
    """A slide number not currently used in this section.

    The continuation needs one straight away, not None: the coverage map has to be able
    to point AT it (a slide nothing points at fails the "teaches nothing the agenda
    promised" gate), and the caller's document-wide renumber maps old numbers to new ones
    positionally — so two slides sharing a number, or one carrying None, would collapse
    two coverage references into one.
    """
    used = []
    for s in slides:
        try:
            used.append(int(s.get("n")))
        except (TypeError, ValueError):
            continue
    return (max(used) + 1) if used else 1


def split_slide(prev_fragment: dict, slide_n, *, title_max_words: int = 8,
                min_bullet_items: int = 3, intro_role: str = "concept_intro",
                continuation_role: str = "mechanism") -> tuple[dict, dict]:
    """Split the slide numbered `slide_n` into two. Returns (new_fragment, summary).

    Raises PatchError, with a reason worth reading, when the slide is not in this chunk
    or carries too little content to divide.

    FINAL numbering is left to the caller: only it can see every chunk at once, and the
    whole point of this edit is that the slides after it — in the later chunks too — are
    renumbered. What happens here is provisional but well-formed: the continuation gets a
    number of its own so the coverage map can reference it.
    """
    fragment = copy.deepcopy(prev_fragment)
    section = _section_of(fragment)
    slides = section.get("slides")
    if not isinstance(slides, list) or not slides:
        raise PatchError("this chunk has no slides to split")
    pos = next((i for i, s in enumerate(slides)
                if isinstance(s, dict) and str(s.get("n")) == str(slide_n)), None)
    if pos is None:
        raise PatchError(
            f"slide {slide_n!r} is not in this chunk (it has "
            f"{', '.join(str(s.get('n')) for s in slides)})")

    original = slides[pos]
    first_content, second_content = _split_content(
        original.get("content"), min_bullet_items=min_bullet_items)
    new_n = _next_free_n(slides)

    first = copy.deepcopy(original)
    first["content"] = first_content

    second = copy.deepcopy(original)
    second["content"] = second_content
    second["n"] = new_n
    second["title"] = _continued_title(original.get("title"), title_max_words)
    # The analogy stays on the first half: it is required only on a first-introduction
    # slide, banned on every other role, and the same analogy on two slides is its own
    # gate failure. The continuation is therefore given a non-intro role and no analogy.
    second.pop("analogy", None)
    if str(original.get("role") or "").strip() == intro_role:
        second["role"] = continuation_role

    slides[pos:pos + 1] = [first, second]

    # THE COVERAGE MAP has to point at the new slide too. A slide nothing in the map
    # references fails the "teaches nothing the coverage map points at, so nothing on the
    # agenda promised it" gate — which is what a bare insertion produces. It is the same
    # sub-concept, now taught across two slides, so it keeps its name.
    cov_added = 0
    cov = fragment.get("coverage")
    if isinstance(cov, dict) and isinstance(cov.get("sub_concepts"), list):
        rebuilt = []
        for sub in cov["sub_concepts"]:
            rebuilt.append(sub)
            if isinstance(sub, dict) and str(sub.get("slide")) == str(slide_n):
                rebuilt.append({**sub, "slide": new_n})
                cov_added += 1
        cov["sub_concepts"] = rebuilt

    inherited = [f for f in ("heading", "subheading", "visual_guidance", "speaker_notes")
                 if str(second.get(f) or "").strip()]
    prose_on_both = all(any(b.get("type") == "text" for b in half)
                        for half in (first_content, second_content))
    return fragment, {
        "mode": "split",
        "split_slide": slide_n,
        "slides_total": len(slides),
        "role_changed": second.get("role") if second.get("role") != original.get("role") else None,
        "analogy_kept_on_first": bool(str(original.get("analogy") or "").strip()),
        "inherited_fields": inherited,
        "coverage_refs_added": cov_added,
        "prose_on_both_halves": prose_on_both,
        "note": f"slide {slide_n} split into two; its content was divided, not rewritten",
    }


def apply(kind: str, prev_fragment: dict, patch: dict) -> tuple[dict, dict]:
    """Dispatch on chunk kind ("opening" | "section")."""
    if kind == "opening":
        return apply_opening_patch(prev_fragment, patch)
    return apply_section_patch(prev_fragment, patch)


# --------------------------------------------------------------------------- #
# WHOLE-DOCUMENT patch — the repair pass in pipeline.finalize
# --------------------------------------------------------------------------- #
# Same argument as the section patch above, applied to the one place it was still
# missing. finalize's repair used generator.revise(), which hands the model the entire
# assembled document and asks for the corrected document back. On a 22-slide doc that
# is ~42,000 OUTPUT tokens to fix a handful of defects — measured on run 2ec34ea9384a
# (session 33) at $0.48, a third of the whole run's cost and 36% of every output token
# it spent, and the single slowest call in the pipeline by a wide margin. The four
# regenerate_patch calls in that same run averaged ~1,700 output tokens.
#
# It is also the same drift risk the section patcher was written for, one level up: 21
# slides the reviewer approved get re-sampled to fix one.
#
# Slide numbers here are the DOCUMENT's global numbering — which is exactly how the
# graders name defects ("Slide 19: speaker_notes has 3 sentences"), so a repair patch
# addresses slides by the same number the issue it fixes does.

_DOC_SETTABLE = ("recap", "agenda", "coverage_map")


def _doc_slides(doc: dict) -> list[tuple[dict, dict]]:
    """(section, slide) for every slide in the document, in reading order."""
    out = []
    for sec in doc.get("sections") or []:
        if not isinstance(sec, dict):
            continue
        for slide in sec.get("slides") or []:
            if isinstance(slide, dict):
                out.append((sec, slide))
    return out


def renumber_doc(doc: dict) -> dict:
    """Public name for `_renumber_doc` — for a document a MODEL returned whole.

    A patch is applied by this module and renumbered on the way out. A full re-draft is
    not: `generator.revise` hands back a fresh document, and a model asked to trim four
    slides deletes their objects and leaves the survivors' numbers alone. The count is
    then right and every label above the first deletion is wrong — a thirteen-slide
    document whose last slide is headed "Slide 17", with gaps wherever a cut landed and
    a coverage map still citing the old numbers.

    Same function, exported so the one other place that produces a whole document can
    reach it. See src/pipeline, after each `generator.revise`.
    """
    return _renumber_doc(doc)


def _renumber_doc(doc: dict) -> dict:
    """Renumber every slide 1..N and carry the coverage map's references with them.

    The mirror of the remap in pipeline.assemble_doc, for a document that already
    exists: removing slide 12 of 22 makes the old 13 the new 12, and a coverage entry
    still pointing at 13 would then name a different slide. Returns {old: new}; a slide
    that was ADDED has no old number and is absent from the map.
    """
    remap: dict[object, int] = {}
    seen: dict[object, int] = {}
    next_n = 1
    for _sec, slide in _doc_slides(doc):
        old = slide.get("n")
        if old is not None:
            seen[old] = seen.get(old, 0) + 1
            remap[old] = next_n
        slide["n"] = next_n
        next_n += 1
    # A number that appeared twice cannot be resolved; drop it rather than guess, and
    # let the coverage gate report the reference for what it is.
    remap = {k: v for k, v in remap.items() if seen.get(k) == 1}

    for entry in doc.get("coverage_map") or []:
        if not isinstance(entry, dict):
            continue
        for sub in entry.get("sub_concepts") or []:
            if not isinstance(sub, dict) or sub.get("slide") in (None, ""):
                continue
            try:
                old = int(sub["slide"])
            except (TypeError, ValueError):
                continue
            if old in remap:
                sub["slide"] = remap[old]
            else:
                # The slide it named is gone. Leave it unmapped rather than pointing it
                # at whatever slide inherited the number — the coverage gate then
                # reports a real dangling reference on the re-grade, which is the truth,
                # and the best-draft rule means this repair simply does not win.
                sub.pop("slide", None)
    return remap


def apply_doc_patch(doc: dict, patch: dict) -> tuple[dict, dict]:
    """Apply a repair patch to an assembled document. Returns (new_doc, summary).

    Raises PatchError if the patch is unusable, which the caller treats as a signal to
    fall back to a full re-draft rather than ship an unrepaired document.
    """
    if not isinstance(patch, dict):
        raise PatchError("patch is not a JSON object")

    new_doc = copy.deepcopy(doc)
    pairs = _doc_slides(new_doc)
    if not pairs:
        raise PatchError("document has no slides to patch")
    by_n = {s.get("n"): s for _sec, s in pairs}

    changed: dict[object, list[str]] = {}
    removed: list[int] = []
    added = 0
    fields_set: list[str] = []

    # --- document-level fields (recap / agenda / coverage_map) ---
    for key, value in (patch.get("set_fields") or {}).items():
        if key not in _DOC_SETTABLE:
            raise PatchError(
                f"set_fields may only set {', '.join(_DOC_SETTABLE)} — not {key!r}")
        new_doc[key] = value
        fields_set.append(key)

    # --- per-slide field edits ---
    for edit in patch.get("edit_slides") or []:
        if not isinstance(edit, dict):
            raise PatchError("edit_slides entry is not an object")
        n = edit.get("n")
        target = by_n.get(n)
        if target is None:
            raise PatchError(
                f"edit_slides names slide {n!r}, which is not in this document "
                f"(it has 1..{len(pairs)})")
        touched = _apply_fields(target, edit.get("fields") or {})
        if touched:
            changed[n] = touched

    # --- removals, before insertions, so after_n still means the numbering above ---
    to_remove = set()
    for n in patch.get("remove_slides") or []:
        if n not in by_n:
            raise PatchError(f"remove_slides names slide {n!r}, which is not in this document")
        to_remove.add(n)
    if to_remove:
        for sec in new_doc.get("sections") or []:
            if isinstance(sec, dict) and isinstance(sec.get("slides"), list):
                sec["slides"] = [s for s in sec["slides"] if s.get("n") not in to_remove]
        removed = sorted(to_remove)
        if not _doc_slides(new_doc):
            raise PatchError("patch removes every slide in the document")

    # --- insertions ---
    for add in patch.get("add_slides") or []:
        if not isinstance(add, dict) or not isinstance(add.get("slide"), dict):
            raise PatchError("add_slides entry needs a 'slide' object")
        after = add.get("after_n")
        new_slide = copy.deepcopy(add["slide"])
        new_slide["n"] = None                  # assigned by the renumber below
        if after in (None, "", 0):
            target_sec = (new_doc.get("sections") or [{}])[0]
            target_sec.setdefault("slides", []).insert(0, new_slide)
        else:
            placed = False
            for sec in new_doc.get("sections") or []:
                slides = sec.get("slides") if isinstance(sec, dict) else None
                if not isinstance(slides, list):
                    continue
                for i, s in enumerate(slides):
                    if s.get("n") == after:
                        slides.insert(i + 1, new_slide)
                        placed = True
                        break
                if placed:
                    break
            if not placed:
                raise PatchError(
                    f"add_slides wants to insert after slide {after!r}, which is not in "
                    f"this document")
        added += 1

    if not (changed or removed or added or fields_set):
        raise PatchError("patch is empty — nothing was changed")

    total = len(pairs)
    _renumber_doc(new_doc)
    touched_count = len(changed) + len(removed) + added
    summary = {
        "mode": "patch",
        "slides_total": total,
        "slides_changed": sorted(changed, key=lambda k: (k is None, k)),
        "fields_changed": {str(k): v for k, v in changed.items()},
        "slides_removed": removed,
        "slides_added": added,
        "doc_fields_set": fields_set,
        "slides_untouched": sorted(
            n for n in by_n
            if n is not None and n not in changed and n not in set(removed)),
        "changed_share": round(touched_count / total, 2) if total else 1.0,
        "note": str(patch.get("note") or "").strip()[:300],
    }
    return new_doc, summary
