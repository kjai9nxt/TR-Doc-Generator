#!/usr/bin/env python3
"""TR Doc Generator — CLI / workflow entrypoint.

Interface (on open):
  python run.py                       # launch the interactive workflow:
                                      #   ask for both Google Sheet links,
                                      #   validate templates, sync, then generate.

Direct / scripted use:
  python run.py --session 16          # sync (using saved links) + generate session 16
  python run.py --session 1 --no-judge
  python run.py --sync                # just re-sync with the saved sheet links
  python run.py --setup               # re-enter / change the sheet links
  python run.py --watch               # keep syncing on an interval, log changes
  python run.py --template-guide      # print how the sheets must look
  python run.py --list                # list sessions (from the synced structure)
"""
from __future__ import annotations
import argparse
import sys
import time

from src import course_loader, pipeline, wizard, sync
from src import config


def _do_watch():
    c, d = sync.last_links()
    if not (c and d):
        print("No sheet links configured. Run 'python run.py --setup' first.")
        return
    interval = config.harness()["context"].get("sync_poll_seconds", 60)
    print(f"Watching both sheets every {interval}s. Ctrl-C to stop.")
    try:
        while True:
            sync.sync(c, d, verbose=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main():
    ap = argparse.ArgumentParser(description="Generate a TR doc for one course session.")
    ap.add_argument("--session", type=int, help="Session number to generate.")
    ap.add_argument("--course", default=None, help="Offline: explicit course-structure .xlsx.")
    ap.add_argument("--no-judge", action="store_true", help="Skip the LLM-as-judge grader.")
    ap.add_argument("--list", action="store_true", help="List sessions and exit.")
    ap.add_argument("--setup", action="store_true", help="Re-enter the Google Sheet links.")
    ap.add_argument("--sync", action="store_true", help="Sync with the saved sheet links and exit.")
    ap.add_argument("--watch", action="store_true", help="Continuously sync and log changes.")
    ap.add_argument("--template-guide", action="store_true", help="Print the sheet template guide.")
    args = ap.parse_args()

    if args.template_guide:
        wizard.show_template_guide()
        return
    if args.watch:
        _do_watch()
        return
    if args.setup:
        wizard.run_wizard(reuse=False)
        return
    if args.sync:
        c, d = sync.last_links()
        if not (c and d):
            print("No sheet links configured. Run 'python run.py --setup' first.")
            return
        sync.sync(c, d, verbose=True)
        return
    if args.list:
        for s in course_loader.load_sessions(args.course):
            print(f"{s.number:>2}. {s.name}  ({s.key_takeaways_count} takeaways)")
        return

    # No session number and no offline course file -> launch the interactive workflow.
    if args.session is None and args.course is None:
        wizard.run_wizard(reuse=True)
        raw = input("Which session number should I generate now? (blank to exit): ").strip()
        if not raw:
            return
        args.session = int(raw)

    if args.session is None:
        ap.error("--session is required (or use --list / no args for the workflow).")

    pipeline.run(args.session, use_judge=not args.no_judge, course_file=args.course)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
