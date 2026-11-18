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
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ.setdefault("REELFORGE_ROOT", str(ROOT))

from reelforge import config, ledger, pipeline, state  # noqa: E402
from reelforge.visuals import AiQuotaExhausted  # noqa: E402

# CI captures stdout, so Python block-buffers it and progress appears in
# 8KB lumps - which looks exactly like a hang.
sys.stdout.reconfigure(line_buffering=True)
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
    raw = token.read_text().strip()
    if not raw:
        problems.append(
            f"{token} is EMPTY.\n"
            "     The YT_TOKEN_JSON repository secret is not set (or is blank).\n"
            "     Value to paste: the entire contents of secrets/yt_token.json on your machine.\n"
            "     Settings > Secrets and variables > Actions > New repository secret"
        )
        return problems
    try:
        data = json.loads(raw)
    except ValueError:
        problems.append(
            f"{token} is not valid JSON (starts with {raw[:40]!r}).\n"
            "     The secret must be the WHOLE json file, braces included - not just the\n"
            "     refresh_token string, and not a copy that lost its newlines."
        )
        return problems
    if not data.get("refresh_token"):
        problems.append(
            f"{token} has no refresh_token. Copy the entire file produced by a local\n"
            "     ./reelforge publish <reel> --platforms youtube --yes"
        )
        return problems
    if not cfg.path(os.environ.get("YT_CLIENT_SECRET", "secrets/yt_client_secret.json")).exists():
        problems.append(
            "YT_CLIENT_SECRET_JSON secret is not set - the OAuth client JSON is needed\n"
            "     to refresh the token."
        )
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


def record_now() -> bool:
    """Commit and push the ledger. Called after every upload when --record is set.

    Separate process on purpose: git state (detached HEAD, rebase conflicts, a
    protected branch) is the shell's problem, and the script's exit code is the
    one fact this loop needs.
    """
    script = ROOT / "scripts" / "record_ledger.sh"
    print("  recording the ledger", flush=True)
    try:
        r = subprocess.run([str(script)], cwd=str(ROOT))
    except OSError as e:
        print(f"  ! could not run {script}: {e}", flush=True)
        return False
    return r.returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=1)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--build-only", action="store_true",
                    help="build reels for review; publish nothing and leave the ledger alone")
    ap.add_argument("--once-per-day", action="store_true",
                    help="exit if a reel was already published today (UTC); makes "
                         "redundant scheduled triggers safe")
    ap.add_argument("--record", action="store_true",
                    help="commit and push the ledger after every upload, and stop the "
                         "batch if that push fails (CI: one lost push = one duplicate, "
                         "not a night's worth)")
    args = ap.parse_args()

    if args.dry_run:
        print("=" * 68)
        print("DRY RUN - building only, NOTHING will be published to any platform.")
        print("=" * 68, flush=True)

    cfg = config.load()
    cfg.override("script.mode", "beats")          # no LLM in CI
    cfg.override("visuals.provider", "ai_cartoon")
    cfg.override("visuals.ai_backend", os.environ.get("AI_BACKEND", "cloudflare"))

    # A build-only run publishes nothing, so it must not require publish
    # credentials. Demanding the YouTube token here is what failed the first
    # two-stage build.
    if not args.dry_run and not args.build_only:
        problems = preflight(cfg)
        if problems:
            for p in problems:
                print(f"::error title=Credentials::{p.replace(chr(10), ' ')}")
                print(p)
            return 1
        print("preflight: YouTube token refreshes OK")

    if not args.dry_run and cfg.get("visuals.ai_backend") == "cloudflare":
        missing = [k for k in ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
                   if not os.environ.get(k, "").strip()]
        if missing:
            msg = (
                f"missing {', '.join(missing)}. Add them as repository secrets.\n"
                "  Without them every scene falls back to procedural gradients.\n"
                "  Cloudflare Workers AI: free 10,000 neurons/day, no card required."
            )
            print(f"::error title=Image backend::{msg.replace(chr(10), ' ')}")
            print(msg)
            return 1
        print("preflight: Cloudflare Workers AI configured")

    # GitHub's schedule event is best-effort and can drop a run under load, so
    # the workflow fires more than once a day. This guard makes the retries free:
    # the first success stamps the date and the rest become no-ops. A rehearsal
    # (--dry-run / --build-only) publishes nothing, so it must not be skipped -
    # otherwise "build for review" would silently do nothing after a good night.
    if args.once_per_day and not args.dry_run and not args.build_only:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if ledger.last_published_date() == today:
            print(f"already published on {today}; nothing to do")
            return 0

    done = ledger.load()
    topics = [t for t in config.load_topics() if str(t["id"]) not in done]
    if not topics:
        # Deliberately a failure, not a warning: an unattended channel that has
        # stopped posting is broken, and GitHub only notifies on failed runs. A
        # green run with a warning would go unread for weeks.
        print("::error title=Out of topics::every topic in config/topics.yaml has "
              "been published - add more beats, or the channel stops here")
        return 1

    store = state.Store(cfg.db)
    # count 0 means "work through the whole queue": the loop stops on its own at
    # the platform's daily cap, so there is no point guessing a number up front.
    batch = topics if args.count == 0 else topics[: args.count]

    for topic in batch:
        print(f"\n=== {topic['id']} ===", flush=True)
        try:
            meta = pipeline.build(cfg, store, topic, keep_work=False)
        except AiQuotaExhausted as e:
            for line in str(e).splitlines():
                print(line)
            print("::error title=Image quota exhausted::" + str(e).splitlines()[0])
            return 1
        except Exception as e:  # noqa: BLE001 - stop the batch, report clearly
            print(f"::error title=Build failed::{type(e).__name__}: {str(e).splitlines()[0][:300]}")
            print(f"\nSTOPPED on {topic['id']} after {len(ledger.load())} published.")
            return 1
        reel_id = meta["reel_id"]

        if args.build_only:
            print(f"  built for review: {meta['video_path']}")
            print("  not publishing; run the `publish` workflow once you have watched it")
            continue

        if args.dry_run:
            print("  dry run: not publishing")
            continue

        result = publish_mod.publish(
            cfg, store, reel_id, list(publish_mod.PLATFORMS), dry_run=False
        )
        failures = result.get("_failures") or {}

        # publish() writes the ledger itself; nothing to do here on success.
        # Stop the whole batch on the first failure. Continuing would build
        # another reel against a broken condition (cap, quota, expired token) and
        # burn time or neurons for nothing. The topic stays out of the ledger, so
        # nothing is lost and the next run retries it.
        if failures:
            for platform, err in failures.items():
                first = str(err).splitlines()[0]
                print(f"::error title={platform} failed::{first[:300]}")
            left = len(topics) - topics.index(topic)
            print(f"\nSTOPPED after {len(ledger.load())} published. {left} reel(s) still queued.")
            return 1

        # Record this reel before building the next one. The 16-17 Nov jobs uploaded
        # six reels and pushed the ledger once, at the end; that push failed on a
        # detached HEAD and the next run re-uploaded the first three. Recording per
        # reel caps one lost push at one duplicate, and stopping here - rather than
        # carrying on unrecorded - is what keeps the ledger from drifting further.
        if args.record and not result.get("skipped"):
            if not record_now():
                left = len(topics) - topics.index(topic)
                print("::error title=Ledger NOT recorded::the upload succeeded but the "
                      "ledger did not land; stopping so the next run does not re-upload "
                      "this reel or anything after it")
                print(f"\nSTOPPED (unrecorded) after {len(ledger.load())} recorded. "
                      f"{left} reel(s) still queued.")
                return 1

    print(f"\nledger now holds {len(ledger.load())} published topic(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
