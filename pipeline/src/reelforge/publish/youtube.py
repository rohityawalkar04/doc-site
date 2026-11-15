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
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
API = "youtube"
VERSION = "v3"


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
            if "quotaExceeded" in msg or "exceeded your quota" in msg.lower():
                raise RuntimeError(
                    "YouTube daily quota exhausted (1,600 units per upload of 10,000/day).\n"
                    "  Resets at midnight Pacific. ~6 uploads/day per project."
                ) from e
            raise
        if status:
            print(f"      youtube upload {int(status.progress() * 100)}%", flush=True)

    vid = response["id"]
    requested = body["status"]["privacyStatus"]
    actual = (response.get("status") or {}).get("privacyStatus")
    if actual and actual != requested:
        print(
            f"      ! requested {requested}, got {actual}. Unaudited Google Cloud projects\n"
            "        force uploads private. Request the YouTube API audit to publish publicly."
        )
    return {"remote_id": vid, "url": f"https://youtube.com/shorts/{vid}", "privacy": actual}
