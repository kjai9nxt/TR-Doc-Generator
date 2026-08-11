"""Deterministic recording-time estimator — the 40-minute gate.

TWO PACING MODELS, selected by constraints.recording.pacing:

"per_slide" (ACTIVE) — minutes = slide_count x minutes_per_slide. Calibrated by the
person who records the sessions: a slide takes about a minute and a half whatever is on
it, so 26 slides is 39 minutes. Under this model the recording budget is a limit on how
many SLIDES a session can hold, and what bounds the amount of text is the PAGE ceiling
(graders/page_grader.py) — the two gates measure genuinely different things.

"word_count" (the original) — counts the skeleton words the instructor elaborates from
(content + speaker notes + analogy), multiplies by a calibrated `elaboration_factor`, and
adds per-slide transition overhead. Visual-guidance text is a design instruction, never
spoken, so it is excluded from both models.

The two disagreed by about 2x: the accepted 14-slide Session 30 doc reads as 39.8 minutes
by word count and 21.0 by pacing. The word-count figure is therefore still computed and
reported as `narration_minutes` whichever model is active, so the disagreement is visible
rather than discarded — and any slide carrying more narration than its per-slide budget
can hold is listed in `dense_slides`, which is information for the reviewer, not a gate.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402


def _wc(text) -> int:
    return len(str(text).split()) if text else 0


def _content_words(content: list[dict]) -> int:
    total = 0
    for block in content or []:
        t = block.get("type")
        if t == "text":
            total += _wc(block.get("text"))
        elif t == "bullets":
            total += sum(_wc(i) for i in block.get("items", []))
        elif t == "table":
            total += sum(_wc(c) for row in block.get("rows", []) for c in row)
    return total


def estimate(doc: dict) -> dict:
    con = config.harness()["constraints"]["recording"]
    wpm = con["speaking_words_per_minute"]
    overhead = con["seconds_per_slide_overhead"]
    factor = con.get("elaboration_factor", 3.3)
    pacing = con.get("pacing", "word_count")
    mps = float(con.get("minutes_per_slide") or 0)

    slides = [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]
    skeleton_words = 0
    per_slide = []
    for s in slides:
        w = (_content_words(s.get("content", []))
             + _wc(s.get("speaker_notes"))
             + _wc(s.get("analogy")))
        skeleton_words += w
        per_slide.append({"n": s.get("n"), "skeleton_words": w,
                          # what narrating THIS slide costs, for the density warning
                          "narration_minutes": round(w * factor / wpm, 2)})

    # front/back matter (recap, agenda, takeaways) — spoken but not elaborated much
    frame_words = 0
    if doc.get("recap"):
        frame_words += sum(_wc(b) for b in doc["recap"].get("bullets", []))
    frame_words += sum(_wc(a) for a in doc.get("agenda", []))
    frame_words += sum(_wc(k) for k in doc.get("key_takeaways", []))
    skeleton_words += frame_words

    spoken_words = int(skeleton_words * factor)
    speak_min = spoken_words / wpm
    overhead_min = (len(slides) * overhead) / 60.0
    word_model_min = round(speak_min + overhead_min, 1)
    slide_model_min = round(len(slides) * mps, 1) if mps else None

    if pacing == "per_slide" and slide_model_min is not None:
        total_min = slide_model_min
    else:
        total_min = word_model_min

    # A slide whose narration alone runs well past its per-slide budget is not a gate
    # failure — the reviewer's pacing is the authority — but it IS worth naming, because
    # it is the one case where the two models mean something different about the doc.
    dense = []
    if pacing == "per_slide" and mps:
        dense = [p for p in per_slide if p["narration_minutes"] > mps * 2]

    return {
        "estimated_minutes": total_min,
        "pacing": pacing,
        "minutes_per_slide": mps or None,
        # The other model's number, always reported so the two stay comparable.
        "narration_minutes": word_model_min,
        "slide_paced_minutes": slide_model_min,
        "skeleton_words": skeleton_words,
        "spoken_words": spoken_words,
        "elaboration_factor": factor,
        "slide_count": len(slides),
        "speaking_minutes": round(speak_min, 1),
        "overhead_minutes": round(overhead_min, 1),
        "max_minutes": con["max_minutes"],
        "target_minutes": con["target_minutes"],
        "within_budget": total_min <= con["max_minutes"],
        "within_target": total_min <= con["target_minutes"],
        "dense_slides": [p["n"] for p in dense],
        "per_slide": per_slide,
    }


def max_slides_in_budget() -> int | None:
    """How many slides the recording ceiling allows under the per-slide pacing model.

    Exists so the slide ceiling and the recording ceiling cannot drift apart silently:
    harness constraints.slides.max should equal this, and a mismatch is worth surfacing.
    """
    con = config.harness()["constraints"]["recording"]
    mps = float(con.get("minutes_per_slide") or 0)
    if con.get("pacing") != "per_slide" or not mps:
        return None
    return int(con["max_minutes"] // mps)
