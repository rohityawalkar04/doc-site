"""Text helpers shared by the script, topic and publish layers.

Two functions that must mean exactly the same thing wherever they are used, so
they live here rather than in whichever module happened to need them first:

  slug       an identifier that survives a filename (reel ids, topic ids)
  title_key  a comparable form of a title (ledger, channel feed, novelty check)

`pipeline.slug` and `publish.youtube.title_key` are re-exported from their old
homes, so no caller changes.
"""
from __future__ import annotations

import re


def slug(text: str, limit: int = 52) -> str:
    """Lowercase, dash-separated, safe as a filename."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    if len(s) <= limit:
        return s
    cut = s[:limit]
    # Do not end on a half word: "capture-decisio" reads like a bug.
    return cut.rsplit("-", 1)[0] if "-" in cut else cut


def title_key(text: str) -> str:
    """Comparable form of a title: case, accents-as-typed, spacing and punctuation
    are not what tells two uploads apart - the words are."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").casefold()).strip()
