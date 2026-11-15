"""Publisher dispatch.

Two properties matter for an unattended daily job:

  * Idempotent - a platform already recorded for a reel is skipped, so a retry
    after a partial failure never double-posts.
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
            dry_run: bool = True) -> dict:
    reel = store.get_reel(reel_id)
    if not reel:
        raise SystemExit(f"unknown reel {reel_id!r} - run `reelforge list`")
    if reel["status"] not in ("rendered", "published"):
        raise SystemExit(f"reel {reel_id!r} is {reel['status']}, not rendered")

    meta = json.loads(reel["meta_json"] or "{}")
    video = Path(reel["video_path"] or "")
    if not video.exists():
        raise SystemExit(f"video missing on disk: {video}")

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
