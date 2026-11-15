"""Derive the Meta values reelforge needs, from one short-lived user token.

Meta's token model is the single most confusing part of publishing, so this does
the derivation instead of asking you to assemble it by hand:

  1. exchange the short-lived user token for a long-lived one (60 days)
  2. list the Pages you administer, and take each Page's own access token
     (a Page token derived from a long-lived user token does not expire)
  3. read the Instagram Business account linked to that Page

Get the short-lived token from https://developers.facebook.com/tools/explorer
with these permissions:
    pages_show_list, pages_read_engagement, pages_manage_posts,
    publish_video, instagram_basic, instagram_content_publish

Usage:
    python scripts/meta_setup.py <SHORT_LIVED_USER_TOKEN>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
VERSION = os.environ.get("FB_GRAPH_VERSION", "v21.0")
BASE = f"https://graph.facebook.com/{VERSION}"


def die(msg: str) -> None:
    print(f"\n  error: {msg}\n")
    sys.exit(1)


def main() -> int:
    if len(sys.argv) < 2:
        die(__doc__)
    token = sys.argv[1].strip()

    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
    except ImportError:
        pass

    app_id = os.environ.get("META_APP_ID", "").strip()
    app_secret = os.environ.get("META_APP_SECRET", "").strip()

    # 1. long-lived user token
    if app_id and app_secret:
        r = requests.get(
            f"{BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": app_id,
                "client_secret": app_secret,
                "fb_exchange_token": token,
            },
            timeout=60,
        )
        data = r.json()
        if "error" in data:
            die(f"token exchange failed: {data['error'].get('message')}")
        token = data.get("access_token", token)
        print("  exchanged for a long-lived user token")
    else:
        print("  META_APP_ID / META_APP_SECRET not set - keeping the token as given")
        print("  (set both in .env to get a 60-day token instead of a 1-hour one)")

    # 2. pages
    r = requests.get(
        f"{BASE}/me/accounts",
        params={"access_token": token, "fields": "id,name,access_token,instagram_business_account"},
        timeout=60,
    )
    data = r.json()
    if "error" in data:
        die(f"could not list pages: {data['error'].get('message')}\n"
            "  make sure pages_show_list was granted and you admin at least one Page")
    pages = data.get("data", [])
    if not pages:
        die("no Facebook Pages found for this account")

    print(f"\n  pages found: {len(pages)}")
    for p in pages:
        ig = (p.get("instagram_business_account") or {}).get("id", "-")
        print(f"    {p['id']}  {p['name']:<32} ig_business_id={ig}")

    page = pages[0]
    if len(pages) > 1:
        choice = input("\n  page id to use [default: first]: ").strip()
        page = next((p for p in pages if p["id"] == choice), page) if choice else page

    ig_id = (page.get("instagram_business_account") or {}).get("id", "")
    if not ig_id:
        print("\n  ! this Page has no Instagram Business account linked.")
        print("    Instagram Reels will not work until you link one")
        print("    (Instagram app > Settings > Business tools > connect a Page).")

    print("\n  --- add these to .env ---")
    print(f"FB_PAGE_ID={page['id']}")
    print(f"FB_PAGE_TOKEN={page.get('access_token', '<missing - check pages_show_list>')}")
    print(f"IG_USER_ID={ig_id}")
    print(f"IG_ACCESS_TOKEN={token}")
    print(f"FB_GRAPH_VERSION={VERSION}")
    print("  -------------------------\n")
    print(f"  youtube: no change, run `reelforge publish <reel> --yes` once to authorise\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
