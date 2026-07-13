"""Interactive startup interface.

On open, the workflow asks the user for the two Google Sheet links (course
structure + session details), validates each against its template, and syncs.
If a sheet does not match its template it is discarded with a clear message and
the user is re-prompted. A built-in help section shows how the templates should
look. Configured links are remembered so future runs can reuse them.
"""
from __future__ import annotations

from . import sheets, sync, config

BANNER = r"""
============================================================
  TR DOC GENERATOR  —  Session Workflow
  Generates a recording-ready Word TR doc for one session,
  in sync with your two Google Sheets.
============================================================
"""


def _prompt(msg: str, default: str | None = None) -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{msg}{suffix}: ").strip()
    return val or (default or "")


def show_template_guide():
    print("\n" + "=" * 60)
    print("  HOW YOUR GOOGLE SHEETS MUST LOOK")
    print("=" * 60)
    print(sheets.guide_text())
    print("=" * 60 + "\n")


def _get_valid_link(kind_label: str, template_key: str, default: str | None) -> str:
    """Prompt until a link loads AND matches its template."""
    while True:
        link = _prompt(f"Paste the {kind_label} Google Sheet link", default)
        if not link:
            print("  A link is required.")
            continue
        try:
            sheets.load_sheet(link, template_key)
            print(f"  ✓ {kind_label} sheet matches the template.")
            return link
        except sheets.TemplateError as e:
            print("\n" + str(e) + "\n")
            print("  Tip: type 'guide' to see the correct template, or re-enter the link.")
            if _prompt("  Enter link (or 'guide')", "guide").lower() == "guide":
                show_template_guide()
            default = None
        except Exception as e:
            print(f"  ⚠ Could not read that sheet: {e}\n"
                  f"    Share it as 'Anyone with the link -> Viewer' and try again.")
            default = None


def run_wizard(reuse: bool = True) -> tuple[str, str]:
    print(BANNER)
    prev_course, prev_details = (sync.last_links() if reuse else (None, None))
    if prev_course and prev_details:
        if _prompt("Reuse the previously configured sheet links? (y/n)", "y").lower().startswith("y"):
            return prev_course, prev_details

    if _prompt("Show the required sheet template first? (y/n)", "n").lower().startswith("y"):
        show_template_guide()

    course_link = _get_valid_link("Course Curriculum Structure", "course_structure", prev_course)
    details_link = _get_valid_link("Session Details (past decks)", "session_details", prev_details)

    print("\nSyncing with your sheets ...")
    sync.sync(course_link, details_link, verbose=True)
    print("\n✓ Setup complete. The agent is now in sync with both sheets.\n")
    return course_link, details_link
