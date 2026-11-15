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


def load_topics() -> list[dict]:
    f = ROOT / "config" / "topics.yaml"
    if not f.exists():
        return []
    data = yaml.safe_load(f.read_text()) or {}
    return data.get("topics", []) or []
