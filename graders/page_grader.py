"""Deterministic page-count estimator — the 16-page gate.

WHY a second length gate exists. Recording time was the only length control, and it
is a poor proxy for how long the document is: a five-row comparison table costs a
third of a page and almost no narration, while a chatty speaker note costs a line and
a minute. So a doc could sit comfortably inside the 40-minute budget and still run
past twenty pages — which is what the reviewer was reading and rejecting.

HOW it works. This does not guess from word counts; it walks the SAME sequence of
paragraphs, bullets and tables that `docx_writer.write_docx` emits, lays each one out
against the template's real metrics (6.0in x 9.0in text area, 11pt body at 1.15 line
spacing with 10pt space-after, Heading 1/2/3 sizes and space-before, the List Bullet
indent, table cell padding), sums the height in points and divides by the usable page
height. Every constant comes from `constraints.pages.layout` in the harness, so the
model can be re-tuned there if docx_writer's template ever changes.

ACCURACY. This is a layout proxy, not a rendering engine: Word's widow/orphan control
and "keep with next" on headings push a little content onto the next page, so a real
render can come out about a page longer than the estimate on a heading-dense doc.
That is why the harness target (14) sits below the ceiling (16) — the margin absorbs
it, and the gate stays deterministic and free.
"""
from __future__ import annotations
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src import config  # noqa: E402
from src.docx_writer import BREAKER  # noqa: E402  (same literal the renderer uses)


def _layout() -> dict:
    return config.harness()["constraints"]["pages"]["layout"]


class _Sheet:
    """Accumulates rendered height, in points, for one document."""

    def __init__(self, layout: dict):
        self.L = layout
        self.height = 0.0
        self.breakdown: dict[str, float] = {}

    # -- primitives ---------------------------------------------------------- #
    def _lines(self, text: str, font_pt: float, width_pt: float) -> int:
        """How many wrapped lines `text` occupies in a column `width_pt` wide."""
        chars = len(str(text or ""))
        if chars == 0:
            return 1                      # an empty paragraph still occupies a line
        per_line = max(1.0, width_pt / (font_pt * self.L["avg_char_width_ratio"]))
        return max(1, math.ceil(chars / per_line))

    def _para_height(self, text: str, *, font_pt: float, indent_pt: float = 0.0,
                     space_before_pt: float = 0.0, space_after_pt: float | None = None,
                     width_pt: float | None = None) -> float:
        width = (width_pt if width_pt is not None
                 else self.L["usable_width_pt"] - indent_pt)
        after = self.L["space_after_pt"] if space_after_pt is None else space_after_pt
        lines = self._lines(text, font_pt, width)
        return space_before_pt + lines * font_pt * self.L["line_height_factor"] + after

    def add(self, bucket: str, points: float):
        self.height += points
        self.breakdown[bucket] = round(self.breakdown.get(bucket, 0.0) + points, 1)

    # -- the elements docx_writer emits -------------------------------------- #
    def body(self, text: str, bucket: str, *, indent_pt: float = 0.0):
        self.add(bucket, self._para_height(text, font_pt=self.L["body_font_pt"],
                                          indent_pt=indent_pt))

    def bullet(self, text: str, bucket: str):
        self.body(text, bucket, indent_pt=self.L["bullet_indent_pt"])

    def heading(self, text: str, level: int, bucket: str):
        font = self.L[f"heading{level}_pt"]
        before = (self.L["heading1_space_before_pt"] if level == 1
                  else self.L["heading_space_before_pt"])
        self.add(bucket, self._para_height(text, font_pt=font, space_before_pt=before,
                                           space_after_pt=0.0))

    def spacer(self, bucket: str):
        self.add(bucket, self.L["empty_paragraph_pt"])

    def table(self, columns: list, rows: list, bucket: str):
        """A native Word table: one header row plus the data rows. Cell paragraphs
        inherit Normal, so each carries the same 10pt space-after a body paragraph
        does — which is why a long table is expensive in pages."""
        ncols = len(columns or [])
        if not ncols:
            return
        col_w = self.L["usable_width_pt"] / ncols
        font = self.L["body_font_pt"]
        for row in [columns] + list(rows or []):
            tallest = max(
                (self._para_height(cell, font_pt=font, width_pt=col_w)
                 for cell in row) if row else [0.0])
            self.add(bucket, tallest + self.L["table_row_padding_pt"])


def _labelled_text(label: str, value) -> str:
    """docx_writer renders these as one paragraph: a bold label then the value."""
    return f"{label} {value}"


def estimate(doc: dict) -> dict:
    """Estimated rendered page count for a TR-doc JSON, plus the gate verdict.

    Mirrors docx_writer.write_docx element for element — if the renderer changes,
    change this in the same commit or the gate silently drifts.
    """
    cfg = config.harness()["constraints"]["pages"]
    s = _Sheet(_layout())

    # 1. title
    s.heading(f"Session {doc.get('session_no', '')} : {doc.get('session_title', '')}",
              1, "front_matter")
    s.spacer("front_matter")

    # 2. recap
    recap = doc.get("recap")
    if recap:
        s.heading(f"RECAP: Session {recap.get('prev_session_no', '')} : "
                  f"{recap.get('prev_session_name', '')}", 2, "front_matter")
        for b in recap.get("bullets") or []:
            s.bullet(str(b), "front_matter")

    # 3. agenda
    s.heading("Agenda for Today's Session", 2, "front_matter")
    for a in doc.get("agenda") or []:
        s.bullet(str(a), "front_matter")

    # 4. sections and slides
    for sec in doc.get("sections") or []:
        s.spacer("sections")
        s.heading(f"{BREAKER} SECTION {sec.get('index', '')}: {sec.get('name', '')} "
                  f"{BREAKER}", 2, "sections")
        for sl in sec.get("slides") or []:
            s.heading(f"Slide {sl.get('n', '')}: {sl.get('title', '')}", 3, "slides")
            s.body(_labelled_text("Heading:", sl.get("heading", "")), "slides")
            s.body(_labelled_text("Subheading:", sl.get("subheading", "")), "slides")
            s.body("Content:", "slides")
            for block in sl.get("content") or []:
                btype = block.get("type")
                if btype == "text":
                    s.body(str(block.get("text", "")), "content")
                elif btype == "bullets":
                    for item in block.get("items") or []:
                        s.bullet(str(item), "content")
                elif btype == "table":
                    s.table(block.get("columns") or [], block.get("rows") or [],
                            "tables")
            if sl.get("analogy"):
                s.body(_labelled_text("Analogy:", sl["analogy"]), "analogies")
            if sl.get("visual_guidance"):
                s.body(_labelled_text("Visual Guidance:", sl["visual_guidance"]),
                       "visual_guidance")
            if sl.get("speaker_notes"):
                s.body(_labelled_text("Speaker Notes:", f'"{sl["speaker_notes"]}"'),
                       "speaker_notes")
            s.spacer("slides")

    # 5. key takeaways, upcoming, closing
    s.heading("Key Takeaways", 2, "back_matter")
    for k in doc.get("key_takeaways") or []:
        s.bullet(str(k), "back_matter")
    s.spacer("back_matter")
    if doc.get("upcoming_session"):
        s.body(_labelled_text("Upcoming Session :", doc["upcoming_session"]),
               "back_matter")
    s.body(str(doc.get("closing", "Thank You  |  All the Best")), "back_matter")

    usable = s.L["usable_height_pt"]
    pages = max(1, math.ceil(s.height / usable))
    max_pages = cfg["max"]
    target = cfg.get("target", max_pages)
    return {
        "estimated_pages": pages,
        "max_pages": max_pages,
        "target_pages": target,
        "within_budget": pages <= max_pages,
        "within_target": pages <= target,
        "total_height_pt": round(s.height, 1),
        "usable_height_pt": usable,
        # Where the pages went — this is what makes an over-budget doc actionable
        # ("analogies: 2.4 pages" is a different fix from "tables: 3 pages").
        "pages_by_part": {k: round(v / usable, 2) for k, v in s.breakdown.items()},
    }


if __name__ == "__main__":       # quick manual check: python -m graders.page_grader f.json
    import json
    for path in sys.argv[1:]:
        est = estimate(json.loads(Path(path).read_text()))
        print(f"{Path(path).name:60} {est['estimated_pages']:3} pages "
              f"(max {est['max_pages']}, within={est['within_budget']})")
        print(f"    {est['pages_by_part']}")
