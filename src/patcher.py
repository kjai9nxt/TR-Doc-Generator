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


def apply(kind: str, prev_fragment: dict, patch: dict) -> tuple[dict, dict]:
    """Dispatch on chunk kind ("opening" | "section")."""
    if kind == "opening":
        return apply_opening_patch(prev_fragment, patch)
    return apply_section_patch(prev_fragment, patch)
