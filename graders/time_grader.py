"""Deterministic recording-time estimator — the 40-minute gate.

A TR doc is a SKELETON; the instructor elaborates on it live. So we count the
full skeleton words the instructor works from (content + speaker notes +
analogy) and multiply by a calibrated `elaboration_factor` to approximate the
real narration length, then add per-slide overhead. Visual-guidance text is a
design instruction, never spoken, so it is excluded.

Calibration anchor: the golden Session 15 (1399 skeleton words, 8 slides) is a
known ~40-minute session; the factor is tuned so it lands near the 36-min target.
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

    slides = [s for sec in doc.get("sections", []) for s in sec.get("slides", [])]
    skeleton_words = 0
    per_slide = []
    for s in slides:
        w = (_content_words(s.get("content", []))
             + _wc(s.get("speaker_notes"))
             + _wc(s.get("analogy")))
        skeleton_words += w
        per_slide.append({"n": s.get("n"), "skeleton_words": w})

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
    total_min = round(speak_min + overhead_min, 1)

    return {
        "estimated_minutes": total_min,
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
        "per_slide": per_slide,
    }
