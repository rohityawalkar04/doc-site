"""Cloud entry point: build one reel and publish it, on a machine with no GPU.

Designed for GitHub Actions, where:
  * there is no GPU and no model cache  -> images come from Cloudflare Workers AI
  * there is no local LLM               -> scripts come from the beats in topics.yaml
  * the filesystem is wiped each run    -> "what has been published" lives in a
                                          committed JSON ledger, not in sqlite

Usage (locally too, if you set the env vars):
    PYTHONPATH=src python scripts/cloud_run.py [--count 1] [--dry-run]
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

from reelforge import config, ledger, pipeline, state  # noqa: E402
from reelforge import publish as publish_mod  # noqa: E402



def preflight(cfg) -> list[str]:
    """Fail before spending minutes on a render that cannot be published.

    While the OAuth app is in Testing, the Google refresh token dies every 7 days.
    Without this check the job renders a full reel and then fails at upload, which
    wastes the run and buries the real cause.
    """
    problems: list[str] = []
    import os

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    from reelforge.publish.youtube import SCOPES

    token = cfg.path(os.environ.get("YT_TOKEN", "secrets/yt_token.json"))
    if not token.exists():
        problems.append(f"YouTube token missing at {token} - run a local publish once to authorise")
        return problems
    try:
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
        if not creds.valid:
            creds.refresh(Request())
    except Exception as e:  # noqa: BLE001 - the message is the point
        detail = str(e)
        hint = ""
        if "invalid_grant" in detail or "expired" in detail.lower():
            hint = (
                "\n     -> refresh token expired. In Testing mode this happens every 7 days."
                "\n     -> re-authorise locally: ./reelforge publish <reel> --platforms youtube --yes"
                "\n     -> then commit the new secrets/yt_token.json into the YT_TOKEN_JSON secret."
                "\n     -> publishing the OAuth app to production removes this permanently."
            )
        problems.append(f"YouTube token cannot refresh: {detail[:200]}{hint}")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = config.load()
    cfg.override("script.mode", "beats")          # no LLM in CI
    cfg.override("visuals.provider", "ai_cartoon")
    cfg.override("visuals.ai_backend", os.environ.get("AI_BACKEND", "cloudflare"))

    if not args.dry_run:
        problems = preflight(cfg)
        if problems:
            for p in problems:
                print(f"::error title=Credentials::{p.replace(chr(10), ' ')}")
                print(p)
            return 1
        print("preflight: YouTube token refreshes OK")

    done = ledger.load()
    topics = [t for t in config.load_topics() if str(t["id"]) not in done]
    if not topics:
        print("::warning title=Out of topics::every topic in config/topics.yaml "
              "has been published - add more")
        return 0

    store = state.Store(cfg.db)
    for topic in topics[: args.count]:
        print(f"\n=== {topic['id']} ===", flush=True)
        meta = pipeline.build(cfg, store, topic, keep_work=False)
        reel_id = meta["reel_id"]

        if args.dry_run:
            print("  dry run: not publishing")
            continue

        result = publish_mod.publish(
            cfg, store, reel_id, list(publish_mod.PLATFORMS), dry_run=False
        )
        failures = result.get("_failures") or {}

        # publish() writes the ledger itself; nothing to do here on success.
        for platform, err in failures.items():
            print(f"::warning title={platform} failed::{err[:300]}")

    print(f"\nledger now holds {len(ledger.load())} published topic(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
