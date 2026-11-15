"""Single source of truth for "which topic has already been published".

Two things publish reels: you, locally, and the cloud job in CI. They used to
track that separately - sqlite locally, a committed JSON file in CI - which meant
CI would happily re-upload a reel that was already live. That is exactly how a
duplicate upload burned through YouTube's per-channel daily cap.

This module is the one ledger both paths write to. It is committed, because the
CI runner has no history to fall back on.
"""
from __future__ import annotations

import json
from pathlib import Path


def _path() -> Path:
    return Path(__file__).resolve().parents[2] / "state" / "published.json"


def load() -> list[str]:
    p = _path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text())
    except (ValueError, OSError):
        return []
    return [str(x) for x in (data.get("published") or [])]


def add(topic_id: str | None) -> None:
    """Idempotent: records a topic once, whatever platform succeeded."""
    if not topic_id:
        return
    done = load()
    if str(topic_id) in done:
        return
    done.append(str(topic_id))
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"published": done}, indent=2) + "\n")


def is_published(topic_id: str) -> bool:
    return str(topic_id) in load()
