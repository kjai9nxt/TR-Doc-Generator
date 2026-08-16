#!/usr/bin/env python3
"""TR Doc Generator — CLI / sync entrypoint.

TR docs themselves are written in the web app's GUIDED review flow (start.sh ->
`python3 server.py` + the React UI): every chunk is reviewed and approved by a human
before the document is assembled. There is no command-line "generate the whole doc"
path any more — that was the one-shot mode, and it is gone.

Interface (on open):
  python run.py                       # interactive setup: ask for the curriculum
                                      #   Google Sheet link, validate it, sync.

Direct / scripted use:
  python run.py --sync                # re-sync with the saved sheet link
  python run.py --setup               # re-enter / change the sheet link
  python run.py --watch               # keep syncing on an interval, log changes
  python run.py --template-guide      # print how the sheet must look
  python run.py --list                # list sessions (from the synced structure)
"""
from __future__ import annotations
import argparse
import sys
import time

from src import course_loader, wizard, sync
from src import config


def _do_watch():
    """Top up the knowledge base on an interval.

    No sheet is re-read and no deck is re-downloaded: this fetches only decks the
    curriculum says are still missing. The curriculum itself is edited in the app.
    """
    interval = config.harness()["context"].get("sync_poll_seconds", 60)
    print(f"Checking for decks to fetch every {interval}s. Ctrl-C to stop.")
    try:
        while True:
            sync.ingest_decks(verbose=True)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\nStopped watching.")


def main():
    ap = argparse.ArgumentParser(
        description="Keep the TR Doc Generator in sync with the curriculum sheet.")
    ap.add_argument("--course", default=None, help="Offline: explicit course-structure .xlsx.")
    ap.add_argument("--list", action="store_true", help="List sessions and exit.")
    ap.add_argument("--setup", action="store_true", help="Re-enter the Google Sheet link.")
    ap.add_argument("--sync", action="store_true",
                    help="Fetch any decks the curriculum still needs, and exit.")
    ap.add_argument("--reimport", action="store_true",
                    help="With --sync: re-read the saved sheet into the curriculum "
                         "(merges; already-extracted decks are not downloaded again).")
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
        # Top-up only: fetch decks the curriculum still needs. Pass --reimport to pull
        # the sheet in again (which merges, and still re-downloads nothing already held).
        sync.sync(sync.last_link() if args.reimport else None, verbose=True)
        return
    if args.list:
        for s in course_loader.load_sessions(args.course):
            print(f"{s.number:>2}. {s.name}  ({s.key_takeaways_count} takeaways)")
        return

    wizard.run_wizard(reuse=True)
    print("Open the web app to write a TR doc: ./start.sh  (guided review flow).")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
