"""Single source of truth for "which topic has already been published".

Two things publish reels: you, locally, and the cloud job in CI. They used to
track that separately - sqlite locally, a committed JSON file in CI - which meant
CI would happily re-upload a reel that was already live. That is exactly how a
duplicate upload burned through YouTube's per-channel daily cap.

This module is the one ledger both paths write to. It is committed, because the
CI runner has no history to fall back on.

Each entry carries the title and video id the platform actually accepted, not
just the topic id. A list of topic ids cannot be joined to the channel, so
"does the ledger match reality?" was unanswerable: script titles are generated,
and `topics.yaml` titles are only what the script was asked for. Legacy entries
that are bare strings still read fine and are upgraded in place by `add()`.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _path() -> Path:
    return Path(__file__).resolve().parents[2] / "state" / "published.json"


def _read() -> dict:
    p = _path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text()) or {}
    except (ValueError, OSError):
        return {}


def entries() -> list[dict]:
    """Every entry as a dict, upgrading legacy bare strings on read.

    Legacy shape:  "five-minute-rule"
    Current shape: {"topic_id": ..., "title": ..., "video_id": ..., "published_at": ...}
    """
    out: list[dict] = []
    for raw in _read().get("published") or []:
        if isinstance(raw, dict):
            topic_id = str(raw.get("topic_id") or "").strip()
            if not topic_id:
                continue
            out.append({
                "topic_id": topic_id,
                "title": raw.get("title"),
                "video_id": raw.get("video_id"),
                "published_at": raw.get("published_at"),
            })
        elif str(raw).strip():
            out.append({"topic_id": str(raw), "title": None, "video_id": None,
                        "published_at": None})
    return out


def load() -> list[str]:
    return [e["topic_id"] for e in entries()]


def last_published_date(fmt: str = "reel") -> str | None:
    """UTC date of the most recent publish *for this format*. Used to make retries
    idempotent.

    Scoped per format because the two builds share one ledger and one `--once-per-day`
    guard: without the scope, the 08:37 IST video stamps today's date and the 19:07
    IST reel then decides a reel has already gone out and exits. A legacy string
    value (written before formats existed) is the reel's date, which is what it was.
    """
    raw = _read().get("last_published")
    if isinstance(raw, dict):
        value = raw.get(fmt)
        return str(value) if value else None
    # A legacy string predates video builds entirely, so it can only be the
    # reel's date. Returning it for "video" would make the first video run skip
    # because a reel had published that same day.
    return (raw or None) if fmt == "reel" else None


def add(topic_id: str | None, when: str | None = None,
        title: str | None = None, video_id: str | None = None,
        fmt: str = "reel") -> None:
    """Idempotent: records a topic once, whatever platform succeeded.

    A second call updates the entry in place - that is how a legacy bare string
    gains its title and video id the next time the reel is touched - and only
    the fields actually supplied are overwritten.
    """
    if not topic_id:
        return
    tid = str(topic_id)
    stamp = when or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    data = _read()
    new_raw: list[dict] = []
    found = False
    for raw in data.get("published") or []:
        current = raw if isinstance(raw, dict) else {"topic_id": str(raw)}
        if str(current.get("topic_id")) != tid:
            new_raw.append(current)
            continue
        found = True
        merged = dict(current)
        if title:
            merged["title"] = title
        if video_id:
            merged["video_id"] = video_id
        merged.setdefault("published_at", stamp)
        new_raw.append(merged)
    if not found:
        entry = {"topic_id": tid, "published_at": stamp}
        if title:
            entry["title"] = title
        if video_id:
            entry["video_id"] = video_id
        new_raw.append(entry)

    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    previous = data.get("last_published")
    stamps = dict(previous) if isinstance(previous, dict) else (
        {"reel": previous} if previous else {}
    )
    stamps[fmt] = stamp
    p.write_text(json.dumps({"published": new_raw, "last_published": stamps},
                            indent=2) + "\n")


def is_published(topic_id: str) -> bool:
    return str(topic_id) in load()
