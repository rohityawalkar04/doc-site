"""Per-platform metadata.

Cross-posting the same file is fine; cross-posting the same *text* is not. Each
platform gets its own title/description/caption and its own hashtag mix, because
the audiences search differently.
"""
from __future__ import annotations

YT_EXTRA = ["#Shorts"]
IG_EXTRA = ["#reels", "#reelsinstagram", "#explore"]


def _source_line(meta: dict) -> str:
    src = (meta.get("source") or "").strip()
    return f"\n\nSource: {src}" if src else ""


def youtube_meta(meta: dict) -> dict:
    tags = [h.lstrip("#") for h in meta.get("hashtags", [])]
    description = (meta.get("description") or "").strip()
    if meta.get("cta"):
        description += "\n\n" + meta["cta"].strip()
    description += _source_line(meta)
    # #Shorts is a promise about the file: vertical, under three minutes. Putting
    # it on a 16:9 long-form video suppresses the normal player and looks like a
    # mistake, so the format decides.
    extras = [] if meta.get("format") == "video" else YT_EXTRA
    hashtags = list(dict.fromkeys(meta.get("hashtags", []) + extras))
    description += "\n\n" + " ".join(hashtags[:12])
    title = meta["title"][:100]
    return {"title": title, "description": description.strip(), "tags": tags[:15],
            "format": meta.get("format") or "reel"}


def facebook_meta(meta: dict) -> str:
    """Facebook descriptions show ~1-2 lines before 'See more'. Lead with the hook."""
    text = (meta.get("caption") or meta["title"]).strip()
    if meta.get("cta"):
        text += " " + meta["cta"].strip()
    text += _source_line(meta)
    hashtags = list(dict.fromkeys(meta.get("hashtags", []) + ["#reels", "#reelsfb"]))
    text += "\n\n" + " ".join(hashtags[:12])
    return text[:2100]


def instagram_meta(meta: dict) -> str:
    caption = (meta.get("caption") or meta["title"]).strip()
    if meta.get("cta"):
        caption += " " + meta["cta"].strip()
    caption += _source_line(meta)
    hashtags = list(dict.fromkeys(meta.get("hashtags", []) + IG_EXTRA))
    caption += "\n\n" + " ".join(hashtags[:20])
    return caption[:2100]
