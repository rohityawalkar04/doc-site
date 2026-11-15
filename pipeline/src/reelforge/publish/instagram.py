"""Instagram Reels via the Instagram Graph API.

Instagram does not accept an upload - it accepts a public URL and fetches the
file itself. So a reel has to be hosted somewhere reachable first.

Free hosts, in order of preference:
  r2          Cloudflare R2 free tier (10 GB storage, zero egress fees)
  gh_release  a release asset on a public GitHub repo

Prerequisites, once:
  * IG account is Business or Creator, linked to a Facebook Page
  * A Meta app in Dev mode (no review needed) with instagram_content_publish
  * Your IG account added as a role-holder on that app
  * A long-lived token in IG_ACCESS_TOKEN

Rate limit: 25 API-published posts per rolling 24h. Renders are much slower than
that, so it will never bind in practice.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import requests


# --------------------------------------------------------------------- hosting
def _host_r2(cfg, video: Path) -> str:
    import boto3

    bucket = os.environ["R2_BUCKET"]
    base = os.environ["R2_PUBLIC_BASE"].rstrip("/")
    key = f"reels/{video.stem}-{int(time.time())}{video.suffix}"
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{os.environ['R2_ACCOUNT_ID']}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
        region_name="auto",
    )
    client.upload_file(
        str(video), bucket, key, ExtraArgs={"ContentType": "video/mp4"}
    )
    return f"{base}/{key}"


def _host_gh(cfg, video: Path) -> str:
    repo = os.environ["GH_RELEASE_REPO"]
    tag = "reels"
    exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo], capture_output=True, text=True
    ).returncode == 0
    if not exists:
        subprocess.run(
            ["gh", "release", "create", tag, "--repo", repo, "--title", "Reels",
             "--notes", "Rendered reels awaiting Instagram fetch."],
            check=True, capture_output=True, text=True,
        )
    subprocess.run(
        ["gh", "release", "upload", tag, str(video), "--repo", repo, "--clobber"],
        check=True, capture_output=True, text=True,
    )
    return f"https://github.com/{repo}/releases/download/{tag}/{video.name}"


def host(cfg, video: Path) -> str | None:
    mode = os.environ.get("IG_HOST") or cfg.get("publish.instagram.host", "none")
    if mode == "r2":
        return _host_r2(cfg, video)
    if mode == "gh_release":
        return _host_gh(cfg, video)
    return None


# --------------------------------------------------------------------- publish
def _graph(path: str) -> str:
    """Two login types exist for Instagram publishing:

    facebook  - graph.facebook.com, token from a Page-linked IG professional
                account. Covers IG + FB Reels with one app. Default.
    instagram - graph.instagram.com, Instagram Login; no Facebook Page needed.
                Simpler, but cannot publish Facebook Reels.
    """
    v = os.environ.get("IG_GRAPH_VERSION", "v21.0")
    host = "graph.instagram.com" if os.environ.get("IG_LOGIN_TYPE", "facebook") == "instagram" else "graph.facebook.com"
    return f"https://{host}/{v}/{path}"


def _post(path: str, payload: dict) -> dict:
    r = requests.post(_graph(path), data=payload, timeout=120)
    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(f"instagram non-JSON response {r.status_code}: {r.text[:300]}")
    if r.status_code >= 400 or "error" in data:
        raise RuntimeError(f"instagram API error: {json.dumps(data)[:600]}")
    return data


def publish(cfg, video: Path, caption: str) -> dict:
    user = os.environ.get("IG_USER_ID", "").strip()
    token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    if not user or not token:
        raise RuntimeError("IG_USER_ID / IG_ACCESS_TOKEN not set in .env")

    url = host(cfg, video)
    if not url:
        raise RuntimeError(
            "Instagram needs a public video URL. Set IG_HOST=r2 (or gh_release) in .env.\n"
            f"  Without it, the reel is ready at {video} with caption:\n\n{caption}\n"
        )
    print(f"      hosted at {url}")

    container = _post(f"{user}/media", {
        "media_type": "REELS",
        "video_url": url,
        "caption": caption,
        "share_to_feed": str(bool(cfg.get("publish.instagram.share_to_feed", True))).lower(),
        "access_token": token,
    })
    cid = container["id"]

    # Instagram transcodes asynchronously. Publishing before FINISHED fails.
    for _ in range(60):
        r = requests.get(
            _graph(cid), params={"fields": "status_code", "access_token": token}, timeout=60
        )
        code = r.json().get("status_code")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"instagram transcode failed: {r.text[:400]}")
        time.sleep(5)
    else:
        raise RuntimeError("instagram transcode timed out after 5 minutes")

    published = _post(f"{user}/media_publish", {"creation_id": cid, "access_token": token})
    media_id = published["id"]
    info = requests.get(
        _graph(media_id), params={"fields": "permalink", "access_token": token}, timeout=60
    ).json()
    return {"remote_id": media_id, "url": info.get("permalink", "")}
