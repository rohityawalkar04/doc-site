"""Config loading. One YAML file drives the whole pipeline."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    def load_dotenv(*_a, **_k):
        return False

ROOT = Path(__file__).resolve().parents[2]


class Config:
    def __init__(self, data: dict, root: Path = ROOT):
        self._d = data or {}
        self.root = root

    def get(self, path: str, default: Any = None) -> Any:
        cur: Any = self._d
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                return default
            cur = cur[key]
        return cur

    def override(self, path: str, value: Any) -> None:
        """Set a dotted config value in memory, e.g. visuals.provider=ai_cartoon."""
        keys = path.split(".")
        cur = self._d
        for key in keys[:-1]:
            cur = cur.setdefault(key, {})
        cur[keys[-1]] = value

    def apply_format(self, name: str) -> None:
        """Load a format profile from `formats:` into the live config.

        A format is the difference between a 50-second vertical reel and a
        three-minute landscape video: aspect, scene count, caption kind, whether
        a thumbnail is published. Everything else - script, voice, visuals
        backend, ledger, publishing - is shared, so the two builds cannot drift
        apart. Unknown names are a hard error: silently falling back would
        publish a landscape reel.
        """
        block = self.get(f"formats.{name}")
        if not isinstance(block, dict) or not block:
            raise SystemExit(
                f"unknown format {name!r}: add it under `formats:` in config/channel.yaml"
            )
        self._d["format"] = name
        for section, values in block.items():
            if not isinstance(values, dict):
                continue
            for key, value in values.items():
                self.override(f"{section}.{key}", value)

    def path(self, p: str | Path) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.root / q

    @property
    def work(self) -> Path:
        return self.path(self.get("paths.work", "work"))

    @property
    def out(self) -> Path:
        return self.path(self.get("paths.out", "out"))

    @property
    def db(self) -> Path:
        return self.path(self.get("paths.db", "work/reelforge.db"))


def load() -> Config:
    load_dotenv(ROOT / ".env")
    f = ROOT / "config" / "channel.yaml"
    data = yaml.safe_load(f.read_text()) if f.exists() else {}
    return Config(data)


def load_topics(fmt: str = "reel") -> list[dict]:
    """Hand-written topics first, then the ones composed at runtime.

    Two files, one order: `config/topics.yaml` is the curated queue and always
    wins, `config/topics-generated.yaml` is the machine-owned append-only log of
    what the channel published when that queue ran dry. They are separate files
    because a YAML round-trip through a writer would strip the comments out of
    the hand-curated one, and in this project those comments are the memory.

    A format with a longer script has its own queue: a 30-beat video topic and a
    13-beat reel are not interchangeable, and mixing them would make the format
    depend on queue order.
    """
    files = {
        "reel": ("topics.yaml", "topics-generated.yaml"),
        "video": ("topics-video.yaml",),
    }.get(fmt, (f"topics-{fmt}.yaml",))
    topics: list[dict] = []
    for name in files:
        f = ROOT / "config" / name
        if not f.exists():
            continue
        data = yaml.safe_load(f.read_text()) or {}
        topics.extend(data.get("topics") or [])
    return topics
