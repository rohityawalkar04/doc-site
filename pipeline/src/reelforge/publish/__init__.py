"""Publisher dispatch.

Three properties matter for an unattended daily job:

  * Idempotent - a platform already recorded for a reel is skipped, so a retry
    after a partial failure never double-posts. The committed ledger is consulted
    too, not just this machine's sqlite: a fresh checkout has no history of its
    own, and that is how the 16-17 Nov jobs re-uploaded three reels.
  * Verified - before a YouTube upload, the channel's own feed is asked whether a
    video with this title is already live. A ledger is only as true as its last
    successful push; the channel is always true.
  * Isolated - one platform failing (expired token, quota, no business account)
    must not stop the other two. Failures are collected and reported at the end.
"""
from __future__ import annotations

import json
from pathlib import Path

from .. import ledger
from .. import meta as meta_mod
from .. import state
from . import facebook, instagram, youtube

PLATFORMS = ("youtube", "instagram", "facebook")


def publish(cfg, store: state.Store, reel_id: str, platforms: list[str],
            dry_run: bool = True, ignore_ledger: bool = False) -> dict:
    reel = store.get_reel(reel_id)
    if not reel:
        raise SystemExit(f"unknown reel {reel_id!r} - run `reelforge list`")
    if reel["status"] not in ("rendered", "published"):
        raise SystemExit(f"reel {reel_id!r} is {reel['status']}, not rendered")

    meta = json.loads(reel["meta_json"] or "{}")
    video = Path(reel["video_path"] or "")
    if not video.exists():
        raise SystemExit(f"video missing on disk: {video}")

    # The committed ledger is the only record that survives a wiped filesystem; sqlite
    # is not. Until this check existed, `store.pub()` was the whole guard, so a run on
    # a fresh checkout uploaded whatever the ledger already knew about - which is how
    # the 16-17 Nov jobs uploaded the same three topics twice. Ask the ledger, here,
    # once, on behalf of every caller.
    topic_id = reel.get("topic_id")
    if not ignore_ledger and topic_id and ledger.is_published(topic_id):
        print(f"  = ledger: {topic_id} is already published -> skipping {reel_id}")
        print("     (rebuild-and-reupload on purpose: pass --force)")
        return {"skipped": True, "ledger": True, "topic_id": topic_id}

    results: dict[str, dict] = {}
    failures: dict[str, str] = {}

    for platform in platforms:
        if platform not in PLATFORMS:
            print(f"  ? unknown platform {platform!r}, skipping")
            continue

        existing = store.pub(reel_id, platform)
        if existing:
            print(f"  = {platform}: already published -> {existing['url']}")
            results[platform] = {"skipped": True, **existing}
            continue

        if not cfg.get(f"publish.{platform}.enabled", True):
            print(f"  - {platform}: disabled in config/channel.yaml")
            continue

        print(f"  -> {platform}")

        # The ledger only knows what this project recorded. The channel knows what is
        # actually live - including anything uploaded by hand or from a machine whose
        # ledger push lost. Ask it before spending 1,600 quota units, and before
        # putting the same video on the channel a second time.
        if platform == "youtube" and not ignore_ledger:
            live = youtube.recent_titles(cfg)
            if live is None:
                print("     ! channel feed unreadable - relying on the ledger alone")
            elif youtube.title_key(meta_mod.youtube_meta(meta)["title"]) in {
                    youtube.title_key(t) for t in live}:
                print("     = youtube: a video with this title is already on the channel"
                      " -> skipping")
                results[platform] = {"skipped": True, "live_title": True}
                continue

        if dry_run:
            print(f"     would upload {video.name} ({video.stat().st_size/1e6:.1f} MB)")
            results[platform] = {"dry_run": True}
            continue

        try:
            if platform == "youtube":
                out = youtube.upload(cfg, video, meta_mod.youtube_meta(meta))
            elif platform == "instagram":
                out = instagram.publish(cfg, video, meta_mod.instagram_meta(meta))
            else:
                out = facebook.publish(cfg, video, meta_mod.facebook_meta(meta))
        except Exception as e:  # noqa: BLE001 - one platform must not stop the rest
            failures[platform] = f"{type(e).__name__}: {e}"
            print(f"     FAILED {failures[platform][:300]}")
            continue

        store.record_pub(reel_id, platform, out["remote_id"], out.get("url", ""))
        # Keep the committed ledger in step with local publishing, or CI
        # re-uploads reels that are already live.
        ledger.add(reel.get("topic_id"))
        print(f"     done {out.get('url') or out['remote_id']}")
        results[platform] = out

    published = [p for p, r in results.items() if not r.get("dry_run") and not r.get("skipped")]
    if published:
        store.set_status(reel_id, "published")

    if failures:
        print("\n  summary: " + ", ".join(
            f"{p} ok" for p, r in results.items() if not r.get("dry_run")
        ) + ("  |  " if results else "") + ", ".join(f"{p} FAILED" for p in failures))
        results["_failures"] = failures
    return results
