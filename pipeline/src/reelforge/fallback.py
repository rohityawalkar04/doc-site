"""Persistent provider health for ReelForge fallback chains."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def _path() -> Path:
    root = Path(os.environ.get("REELFORGE_ROOT", "."))
    return root / "work" / "provider-health.json"


def _read() -> dict:
    path = _path()
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}


def _write(data: dict) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def available(provider: str) -> bool:
    item = _read().get(provider, {})
    try:
        return float(item.get("disabled_until", 0)) <= time.time()
    except (TypeError, ValueError):
        # A hand-edited or partially migrated state file must not stop a reel.
        return True


def disable(provider: str, reason: str, seconds: int = 86400) -> None:
    data = _read()
    data[provider] = {
        "disabled_until": time.time() + max(60, int(seconds)),
        "reason": str(reason)[:300],
    }
    _write(data)


def enable(provider: str) -> None:
    data = _read()
    if provider in data:
        del data[provider]
        _write(data)
