"""Deterministic hard gates. No LLM. These run on the generator's JSON output
BEFORE any doc is accepted. Any FAIL blocks acceptance and feeds the reason
into the revision pass.
"""
from __future__ import annotations
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


def check(doc: dict, session, is_first: bool, is_last: bool) -> GuardrailResult:
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
    if len(slides) < con["slides"]["min"]:
        fails.append(f"Only {len(slides)} slides (min {con['slides']['min']}).")
    if len(slides) > con["slides"]["max"]:
        fails.append(f"{len(slides)} slides (max {con['slides']['max']}) — split content, don't cram.")

    # --- per-slide required fields ---
    # House rule: EVERY slide must carry all six fields (heading, subheading,
    # content, analogy, visual_guidance, speaker_notes).
    for s in slides:
        tag = f"Slide {s.get('n', '?')}"
        for req in ("heading", "subheading", "content",
                    "analogy", "visual_guidance", "speaker_notes"):
            if not s.get(req) or not str(s.get(req)).strip():
                fails.append(f"{tag}: missing '{req}' (required on every slide).")

    # --- no repeated analogy across slides (exact match; backstop for the
    #     no-repeat rule — the LLM eval set also catches same-theme reuse) ---
    analogies = [str(s.get("analogy", "")).strip().lower() for s in slides if s.get("analogy")]
    dupes = sorted({a for a in analogies if analogies.count(a) > 1})
    if dupes:
        fails.append(f"Duplicate analogy reused across {len(dupes)} slide group(s) — "
                     f"each slide needs a distinct analogy.")

    passed = len(fails) == 0
    if gates.get("structural_pass") is True and not passed:
        pass  # already reflected in fails
    return GuardrailResult(passed=passed, failures=fails, warnings=warns)
