"""Publish reels that an earlier `build` run produced, from the downloaded artifact.

This is the second half of the two-stage flow: build attaches the mp4 and its
metadata JSON as an artifact, you watch it, then trigger `publish` with the build
run's id. Nothing here generates anything - it only uploads what already exists
and records it in the committed ledger.

Usage (from the pipeline directory, with the artifact unpacked into out/):
    PYTHONPATH=src python scripts/publish_run.py --dir out --platforms youtube
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("REELFORGE_ROOT", str(ROOT))
sys.stdout.reconfigure(line_buffering=True)

from reelforge import config, ledger, state  # noqa: E402
from reelforge import publish as publish_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="out", help="directory holding the built mp4 + json")
    ap.add_argument("--reel", default=None, help="publish only this reel id or filename")
    ap.add_argument("--platforms", default="youtube",
                    help="comma list: youtube,instagram,facebook or 'all'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    out = Path(args.dir)
    if not out.is_dir():
        print(f"::error::no such directory: {out}")
        return 1

    built: list[tuple[Path, dict]] = []
    for f in sorted(out.glob("*.json")):
        try:
            meta = json.loads(f.read_text())
        except ValueError:
            continue
        if meta.get("topic_id") and meta.get("reel_id"):
            built.append((f, meta))

    if args.reel:
        # Accept any of the three names a user might reasonably reach for:
        # the topic id from topics.yaml, the reel id, or the file name.
        built = [b for b in built
                 if args.reel in (b[1]["reel_id"], b[1]["topic_id"],
                                  Path(b[1]["video_path"]).name)]

    if not built:
        print(f"::warning::no built reels found in {out} - check the artifact downloaded")
        return 0

    platforms = list(publish_mod.PLATFORMS) if args.platforms == "all" else [
        p.strip() for p in args.platforms.split(",") if p.strip()
    ]

    store = state.Store(cfg.db)
    failed = 0
    for _, meta in built:
        reel_id = meta["reel_id"]
        video = out / Path(meta["video_path"]).name
        if not video.exists():
            print(f"::error::video missing for {reel_id}: {video}")
            failed += 1
            continue

        print(f"\n=== {reel_id}  ({video.stat().st_size/1e6:.1f} MB) ===")
        print(f"    title: {meta.get('title','')[:70]}")
        if ledger.is_published(meta["topic_id"]):
            print(f"    already in the ledger - skipping")
            continue

        # Rebuild the local row so the tested publish path (with its idempotency)
        # is reused rather than reimplemented here.
        store.add_reel(reel_id, meta["topic_id"], meta.get("title", reel_id), meta)
        store.set_status(reel_id, "rendered", str(video), meta=meta)

        result = publish_mod.publish(cfg, store, reel_id, platforms, dry_run=args.dry_run)
        if result.get("_failures"):
            failed += 1
            for platform, err in result["_failures"].items():
                print(f"::error title={platform} failed::{err[:300]}")

    print(f"\n{len(built) - failed}/{len(built)} reel(s) ok; ledger holds {len(ledger.load())}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
