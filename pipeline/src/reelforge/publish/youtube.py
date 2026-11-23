"""YouTube Shorts upload via the Data API v3.

Cost is quota, not money: videos.insert is 1,600 units of the 10,000/day default
budget, so ~6 uploads per day per project. Quota resets at midnight Pacific.

Two traps worth knowing:
  * While the OAuth consent screen is in "Testing", refresh tokens expire after 7
    days. Publish the app ("In production", unverified is fine for your own
    channel) or the nightly job starts failing silently.
  * Uploads from an unaudited project made after 28 July 2020 are forced to
    private. Public uploads need the API audit form once.
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

from ..text import title_key  # noqa: F401 - re-exported: check_channel imports it here

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
API = "youtube"
VERSION = "v3"
FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={cid}"
ATOM = "{http://www.w3.org/2005/Atom}"


def recent_titles(cfg, timeout: float = 15.0, attempts: int = 3) -> list[str] | None:
    """Titles of the channel's most recent uploads, from the public RSS feed.

    Zero quota, no OAuth scope, no auth at all: this is the one view of the
    channel that cannot be stale-and-local. The Data API's own list call would
    need `youtube.readonly` added to SCOPES, which invalidates every cached token.

    Returns None only after `attempts` tries, so a transient 5xx does not decide
    anything. The caller treats None as "cannot verify" and refuses to upload:
    a missed night is recoverable, a second copy on the channel is not.

    The feed carries the last 15 uploads, so this catches the case that actually
    happens - a run re-walking the head of its own queue. Older reels are the
    ledger's job.
    """
    cid = str(cfg.get("publish.youtube.channel_id", "") or "").strip()
    if not cid:
        return None
    url = FEED.format(cid=cid)
    for attempt in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read()
            root = ET.fromstring(body)
            titles = []
            for entry in root.findall(f"{ATOM}entry"):
                t = entry.findtext(f"{ATOM}title")
                if t:
                    titles.append(t.strip())
            return titles
        except (urllib.error.URLError, OSError, ValueError, ET.ParseError) as e:
            if attempt == attempts:
                print(f"     ! channel feed unreadable after {attempts} attempts: "
                      f"{type(e).__name__}: {str(e)[:120]}", flush=True)
                return None
            time.sleep(2 * attempt)
    return None



def _service(cfg):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    secret = Path(cfg.path(os.environ.get("YT_CLIENT_SECRET", "secrets/yt_client_secret.json")))
    token = Path(cfg.path(os.environ.get("YT_TOKEN", "secrets/yt_token.json")))
    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    if not creds or not creds.valid:
        # run_local_server() waits for a browser callback. In CI there is no
        # browser and no TTY, so it would block until the job timeout (45 min)
        # and report nothing useful. Refuse instead.
        interactive = sys.stdin.isatty() and not (
            os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")
        )
        if not interactive:
            raise RuntimeError(
                "no usable YouTube token, and this is a non-interactive run.\n"
                "  Authorise once on your own machine:\n"
                "    ./reelforge publish <reel_id> --platforms youtube --yes\n"
                "  Then copy the whole contents of secrets/yt_token.json into the\n"
                "  YT_TOKEN_JSON repository secret (Settings > Secrets > Actions)."
            )
        if not secret.exists():
            raise RuntimeError(
                f"missing OAuth client secret at {secret}\n"
                "  Google Cloud Console > APIs & Services > Credentials >\n"
                "  Create credentials > OAuth client ID > Desktop app, then save the JSON."
            )
        try:
            creds = InstalledAppFlow.from_client_secrets_file(
                str(secret), SCOPES
            ).run_local_server(port=0, open_browser=True)
        except Exception as e:  # noqa: BLE001 - the message is the whole point
            msg = str(e)
            if "access_denied" in msg.lower() or "403" in msg:
                raise RuntimeError(
                    "Google refused the request: access_denied.\n"
                    "  The OAuth app is in 'Testing' and this account is not an\n"
                    "  approved tester.\n\n"
                    "  Fix (preferred): Cloud Console > APIs & Services > OAuth\n"
                    "  consent screen > Audience > Publish app. It stays 'unverified',\n"
                    "  which is fine for your own channel, and - unlike Testing - the\n"
                    "  refresh token does not expire after 7 days.\n\n"
                    "  Quick fix: same page > Test users > Add users > your address.\n\n"
                    "  Also check the browser is signed in as the account that owns\n"
                    "  the Cloud project."
                ) from e
            raise
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json())
    return build(API, VERSION, credentials=creds, cache_discovery=False)


def authorise(cfg) -> Path:
    """Run the OAuth consent flow on purpose and cache the token.

    Separate from upload() so re-authorising never requires uploading something
    just to refresh a credential.
    """
    from google_auth_oauthlib.flow import InstalledAppFlow

    secret = Path(cfg.path(os.environ.get("YT_CLIENT_SECRET", "secrets/yt_client_secret.json")))
    token = Path(cfg.path(os.environ.get("YT_TOKEN", "secrets/yt_token.json")))
    if not secret.exists():
        raise RuntimeError(
            f"missing OAuth client secret at {secret}\n"
            "  Google Cloud Console > APIs & Services > Credentials >\n"
            "  Create credentials > OAuth client ID > Desktop app."
        )
    creds = InstalledAppFlow.from_client_secrets_file(
        str(secret), SCOPES
    ).run_local_server(port=0, open_browser=True)
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text(creds.to_json())
    try:
        os.chmod(token, 0o600)
    except OSError:
        pass
    return token


def upload(cfg, video: Path, meta: dict) -> dict:
    from googleapiclient.http import MediaFileUpload

    svc = _service(cfg)
    body = {
        "snippet": {
            "title": meta["title"],
            "description": meta["description"],
            "tags": meta.get("tags", []),
            "categoryId": str(cfg.get("publish.youtube.category_id", "27")),
        },
        "status": {
            "privacyStatus": cfg.get("publish.youtube.privacy", "public"),
            "selfDeclaredMadeForKids": bool(cfg.get("publish.youtube.made_for_kids", False)),
        },
    }
    media = MediaFileUpload(str(video), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    req = svc.videos().insert(part="snippet,status", body=body, media_body=media)
    response = None
    while response is None:
        try:
            status, response = req.next_chunk()
        except Exception as e:  # HttpError
            msg = str(e)
            low = msg.lower()
            if "uploadlimitexceeded" in low or "exceeded the number of videos" in low:
                raise RuntimeError(
                    "YouTube CHANNEL upload limit reached - this is not API quota.\n"
                    "  YouTube caps how many videos one channel may upload per day, and\n"
                    "  new or unverified channels get a low cap.\n"
                    "  - Raise it: youtube.com/verify (phone verification, free).\n"
                    "  - Or wait: the limit resets daily.\n"
                    "  The reel stays unpublished and will retry on the next run."
                ) from e
            if "quotaexceeded" in low or "exceeded your quota" in low:
                raise RuntimeError(
                    "YouTube API quota exhausted (1,600 units per upload of 10,000/day).\n"
                    "  Resets at midnight Pacific. ~6 uploads/day per project."
                ) from e
            raise
        if status:
            print(f"      youtube upload {int(status.progress() * 100)}%", flush=True)

    vid = response["id"]

    thumb = meta.get("thumbnail")
    if thumb and Path(str(thumb)).exists():
        # 50 quota units. A long-form video without a thumbnail falls back to a
        # random frame from the first seconds, which is the single biggest CTR
        # cost you can avoid for free.
        try:
            svc.thumbnails().set(
                videoId=vid,
                media_body=MediaFileUpload(str(thumb), mimetype="image/jpeg"),
            ).execute()
            print("      thumbnail set")
        except Exception as e:  # noqa: BLE001 - an upload without it still shipped
            print(f"      ! thumbnail not set ({type(e).__name__}: {str(e)[:120]})")

    requested = body["status"]["privacyStatus"]
    actual = (response.get("status") or {}).get("privacyStatus")
    if actual and actual != requested:
        print(
            f"      ! requested {requested}, got {actual}. Unaudited Google Cloud projects\n"
            "        force uploads private. Request the YouTube API audit to publish publicly."
        )
    return {"remote_id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": actual}
