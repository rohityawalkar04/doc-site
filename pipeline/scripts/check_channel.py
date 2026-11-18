"""Reconcile the committed ledger with what is actually on the channel.

A guard is a belief; this is a check.

Every publish path now refuses to re-upload something the ledger or the channel
says is live. That still leaves one question nobody was asking: does the ledger
match reality *right now*? On 16 Nov the answer was no - six uploads the ledger
had never heard of - and nothing in the system said so. This script says so.

Directions it checks, and what each one means:

  * channel -> ledger  every video on the channel must map to a ledger topic.
                       A video with no ledger entry is drift: it was published
                       by something that did not record it (a lost push, a manual
                       upload, another machine). That is the 16 Nov failure, and
                       it is an error.
  * duplicates         the same title twice in the feed. Historical copies are a
                       warning; --strict turns them into a failure once they are
                       gone and a new one must never appear.
  * ledger -> channel  a recorded topic with no video on the channel. While the
                       channel holds fewer than 15 videos the feed shows all of
                       it, so absence is provable: deleted, unlisted, or never
                       public. Past 15, this direction only sees the window and
                       says so instead of guessing.

Exit code: 1 on any error (or any warning with --strict), 0 otherwise.

Usage:
  PYTHONPATH=src python scripts/check_channel.py [--strict]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("REELFORGE_ROOT", str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

import yaml  # noqa: E402

from reelforge import config, ledger  # noqa: E402
from reelforge.publish import youtube  # noqa: E402

FEED_WINDOW = 15   # what youtube.com/feeds/videos.xml carries


def title_index(ledger_entries: list[dict]) -> dict[str, str]:
    """title_key -> topic id.

    Two sources, in this order of authority:
      1. every topics*.yaml - what the script was asked for. All three files
         matter: the mythology and productivity reels are in the ledger too.
      2. the ledger's own recorded title - what the platform actually accepted.
         This one wins, because script titles are generated and do not always
         match the topic title ("Voice Memo To Content").
    """
    index: dict[str, str] = {}
    for path in sorted(glob.glob(str(config.ROOT / "config" / "topics*.yaml"))):
        try:
            data = yaml.safe_load(Path(path).read_text()) or {}
        except (OSError, yaml.YAMLError):
            continue
        for topic in data.get("topics") or []:
            if topic.get("title") and topic.get("id"):
                index[youtube.title_key(str(topic["title"]))] = str(topic["id"])
    for entry in ledger_entries:
        if entry.get("title"):
            index[youtube.title_key(entry["title"])] = entry["topic_id"]
    return index


def reconcile(feed_titles: list[str], ledger_topics: set[str],
              index: dict[str, str]) -> tuple[list[str], list[str], list[str]]:
    """Pure reconciliation. Returns (errors, warnings, notes)."""
    errors: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    seen: dict[str, list[str]] = {}
    for title in feed_titles:
        seen.setdefault(youtube.title_key(title), []).append(title)

    for key, copies in seen.items():
        topic_id = index.get(key)
        if topic_id is None:
            errors.append(
                f"on the channel but no ledger entry or topic title matches it: "
                f"{copies[0]!r}"
            )
        elif topic_id not in ledger_topics:
            errors.append(
                f"live on the channel but missing from the ledger: {copies[0]!r} "
                f"({topic_id}) - this is the 16 Nov drift"
            )
        if len(copies) > 1:
            warnings.append(f"{len(copies)} copies live of {copies[0]!r}")

    on_channel = {index[k] for k in seen if k in index}
    absent = sorted(ledger_topics - on_channel)
    if absent:
        if len(feed_titles) < FEED_WINDOW:
            warnings.append(
                f"{len(absent)} ledger topic(s) have no video on the channel "
                f"(the feed lists all {len(feed_titles)}): {', '.join(absent)}"
            )
        else:
            notes.append(
                f"{len(absent)} ledger topic(s) outside the {FEED_WINDOW}-video feed "
                f"window - not checked: {', '.join(absent)}"
            )

    return errors, warnings, notes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict", action="store_true",
                    help="treat duplicate copies on the channel as a failure too")
    args = ap.parse_args()

    cfg = config.load()
    feed = youtube.recent_titles(cfg)
    if feed is None:
        print("::error title=Channel unreadable::cannot reconcile without the feed - "
              "check publish.youtube.channel_id and the network")
        return 1

    topics = set(ledger.load())
    entries = ledger.entries()
    errors, warnings, notes = reconcile(feed, topics, title_index(entries))

    print(f"ledger: {len(topics)} topic(s)   channel feed: {len(feed)} video(s)")
    for n in notes:
        print(f"  note    {n}")
    for w in warnings:
        print(f"  WARN    {w}")
        print(f"::warning title=Channel ledger drift::{w}")
    for e in errors:
        print(f"  ERROR   {e}")
        print(f"::error title=Channel drift::{e}")

    if errors:
        print("\nDRIFT: the channel and the ledger disagree. Fix the ledger before "
              "the next run, or that run may upload a copy.")
        return 1
    if warnings and args.strict:
        print("\nSTRICT: warnings treated as failures.")
        return 1
    print("\nOK: every video on the channel is in the ledger.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
