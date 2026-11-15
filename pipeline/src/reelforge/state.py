"""SQLite ledger. Makes every stage idempotent and stops duplicate posting."""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reels (
    id          TEXT PRIMARY KEY,
    topic_id    TEXT,
    title       TEXT,
    status      TEXT NOT NULL,          -- planned | rendered | published | failed
    video_path  TEXT,
    meta_json   TEXT,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publications (
    reel_id      TEXT NOT NULL,
    platform     TEXT NOT NULL,
    remote_id    TEXT,
    url          TEXT,
    published_at TEXT NOT NULL,
    PRIMARY KEY (reel_id, platform)
);
CREATE TABLE IF NOT EXISTS topics_used (
    topic_id TEXT PRIMARY KEY,
    reel_id  TEXT,
    used_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS visual_usage (
    key      TEXT PRIMARY KEY,
    uses     INTEGER NOT NULL DEFAULT 0,
    last_at  TEXT
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # ---- reels -------------------------------------------------------------
    def add_reel(self, reel_id: str, topic_id: str, title: str, meta: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO reels (id, topic_id, title, status, meta_json,"
            " created_at, updated_at) VALUES (?,?,?,'planned',?,?,?)",
            (reel_id, topic_id, title, json.dumps(meta, ensure_ascii=False), _now(), _now()),
        )
        self.conn.commit()

    def set_status(self, reel_id: str, status: str, video_path: str | None = None,
                   error: str | None = None, meta: dict | None = None) -> None:
        if meta is not None:
            self.conn.execute(
                "UPDATE reels SET status=?, video_path=COALESCE(?, video_path), error=?,"
                " meta_json=?, updated_at=? WHERE id=?",
                (status, video_path, error, json.dumps(meta, ensure_ascii=False), _now(), reel_id),
            )
        else:
            self.conn.execute(
                "UPDATE reels SET status=?, video_path=COALESCE(?, video_path), error=?,"
                " updated_at=? WHERE id=?",
                (status, video_path, error, _now(), reel_id),
            )
        self.conn.commit()

    def get_reel(self, reel_id: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM reels WHERE id=?", (reel_id,)).fetchone()
        return dict(row) if row else None

    def latest_reels(self, limit: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM reels ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # ---- topics ------------------------------------------------------------
    def topic_used(self, topic_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM topics_used WHERE topic_id=?", (topic_id,)
        ).fetchone() is not None

    def mark_topic(self, topic_id: str, reel_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO topics_used (topic_id, reel_id, used_at) VALUES (?,?,?)",
            (topic_id, reel_id, _now()),
        )
        self.conn.commit()

    # ---- publications ------------------------------------------------------
    def record_pub(self, reel_id: str, platform: str, remote_id: str, url: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO publications (reel_id, platform, remote_id, url,"
            " published_at) VALUES (?,?,?,?,?)",
            (reel_id, platform, remote_id, url, _now()),
        )
        self.conn.commit()

    def pub(self, reel_id: str, platform: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM publications WHERE reel_id=? AND platform=?", (reel_id, platform)
        ).fetchone()
        return dict(row) if row else None

    # ---- visual reuse ------------------------------------------------------
    def bump_visual(self, key: str) -> int:
        self.conn.execute(
            "INSERT INTO visual_usage (key, uses, last_at) VALUES (?, 1, ?)"
            " ON CONFLICT(key) DO UPDATE SET uses = uses + 1, last_at = excluded.last_at",
            (key, _now()),
        )
        self.conn.commit()
        row = self.conn.execute("SELECT uses FROM visual_usage WHERE key=?", (key,)).fetchone()
        return row["uses"] if row else 1

    def visual_uses(self, key: str) -> int:
        row = self.conn.execute("SELECT uses FROM visual_usage WHERE key=?", (key,)).fetchone()
        return row["uses"] if row else 0
