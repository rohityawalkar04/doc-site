"""Facebook Reels upload via the Graph API Reels Publishing endpoints.

Three steps, and the middle one is not a Graph call:
  1. POST /{page-id}/video_reels  upload_phase=start   -> video_id + upload_url
  2. POST {upload_url} with the raw bytes (rupload.facebook.com, not graph)
  3. POST /{page-id}/video_reels  upload_phase=finish  -> PUBLISHED

Needs a PAGE access token (not a user token) with pages_manage_posts and
publish_video. `scripts/meta_setup.py` derives one from a user token.

Reels limits: 9:16, 3 s to 90 s, max 1 GB. Ours are ~35 s at ~18 MB.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import requests

UPLOAD_CHUNK = 4 * 1024 * 1024


def _graph(version: str, path: str) -> str:
    return f"https://graph.facebook.com/{version}/{path}"


def _check(r: requests.Response, what: str) -> dict:
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"facebook {what}: non-JSON {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"facebook {what} failed: {str(data)[:600]}")
    return data


def publish(cfg, video: Path, description: str) -> dict:
    page = os.environ.get("FB_PAGE_ID", "").strip()
    token = os.environ.get("FB_PAGE_TOKEN", "").strip()
    version = os.environ.get("FB_GRAPH_VERSION", "v21.0")
    if not page or not token:
        raise RuntimeError(
            "FB_PAGE_ID / FB_PAGE_TOKEN not set. Run: python scripts/meta_setup.py"
        )

    size = video.stat().st_size
    state = cfg.get("publish.facebook.video_state", "PUBLISHED")

    # 1. start
    started = _check(
        requests.post(
            _graph(version, f"{page}/video_reels"),
            data={"upload_phase": "start", "access_token": token},
            timeout=60,
        ),
        "start",
    )
    video_id = started.get("video_id")
    upload_url = started.get("upload_url")
    if not video_id or not upload_url:
        raise RuntimeError(f"facebook start returned no upload target: {started}")
    print(f"      facebook reel {video_id}")

    # 2. upload the bytes. Plain requests, not the Graph host.
    with open(video, "rb") as f:
        data = f.read()
    up = requests.post(
        upload_url,
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(size),
            "Content-Type": "application/octet-stream",
        },
        data=data,
        timeout=600,
    )
    if up.status_code >= 400:
        raise RuntimeError(f"facebook upload failed {up.status_code}: {up.text[:400]}")

    # 3. finish
    finished = _check(
        requests.post(
            _graph(version, f"{page}/video_reels"),
            data={
                "upload_phase": "finish",
                "video_id": video_id,
                "video_state": state,
                "description": description,
                "access_token": token,
            },
            timeout=120,
        ),
        "finish",
    )

    # Poll until Facebook has processed it. Publishing before `ready` fails.
    permalink = ""
    for _ in range(60):
        info = requests.get(
            _graph(version, video_id),
            params={"fields": "status,permalink_url", "access_token": token},
            timeout=60,
        )
        if info.status_code == 200:
            body = info.json()
            permalink = body.get("permalink_url") or permalink
            status = (body.get("status") or {}).get("video_status")
            if status in ("ready", "published"):
                break
            if status == "error":
                raise RuntimeError(f"facebook processing failed: {body}")
        time.sleep(5)

    return {
        "remote_id": finished.get("post_id") or video_id,
        "url": permalink or f"https://www.facebook.com/reel/{video_id}",
    }
