"""Caption rendering to transparent PNGs, composited by ffmpeg's `overlay`.

Why not ASS: Homebrew's ffmpeg 8 build has no libass / freetype / fontconfig, so
the `ass`, `subtitles` and `drawtext` filters do not exist.

Two modes:
  per_word: false - one card per phrase (cheapest, most overlays avoided)
  per_word: true  - one card per *word*, with that word in the accent colour.
                    This is the karaoke look most high-performing shorts use;
                    it costs ~2.5x the overlay count and therefore encode time.

Everything is drawn on a single line with the font auto-fitted to the frame,
because a wrapped caption with per-word colouring is where the maths gets wrong.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PUNCT_BREAK = set(".!?;,:")
MAX_WIDTH_RATIO = 0.86
LINE_HEIGHT_RATIO = 1.04
SHADOW_ALPHA = 150
SHADOW_OFFSET = 5


def _font_path(cfg) -> Path:
    name = cfg.get("captions.font", "Anton")
    candidate = cfg.path(f"assets/fonts/{name}-Regular.ttf")
    if candidate.exists():
        return candidate
    for p in sorted(cfg.path("assets/fonts").glob("*.ttf")):
        if name.lower() in p.name.lower():
            return p
    raise RuntimeError(f"no caption font found in {cfg.path('assets/fonts')}")


def _colour(ass_style: str) -> tuple[int, int, int, int]:
    """'&HAABBGGRR' -> RGBA tuple."""
    h = ass_style.strip().lstrip("&H").lstrip("hH").zfill(8)
    a, b, g, r = (int(h[i : i + 2], 16) for i in (0, 2, 4, 6))
    return (r, g, b, 255 - a)


def _fit_font(path: Path, text: str, size: int, max_width: float) -> ImageFont.FreeTypeFont:
    """Shrink until the line fits. One line, always - see module docstring."""
    while size > 24:
        font = ImageFont.truetype(str(path), size)
        if font.getlength(text) <= max_width:
            return font
        size -= 4
    return ImageFont.truetype(str(path), size)


def _chunks(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    out: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cand = cur + [w]
        text = " ".join(x["w"] for x in cand)
        if cur and (len(cand) > max_words or len(text) > max_chars):
            out.append(cur)
            cur = [w]
        else:
            cur = cand
        if w["w"] and w["w"][-1] in PUNCT_BREAK:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [c for c in out if c]


def _draw_card(cfg, path: Path, words: list[str], active: int | None,
               w: int, h: int, y: int, hook: bool = False) -> None:
    """Render one caption card.

    `hook` renders every word in the accent colour (scene one). Otherwise only
    the `active` word is accented.
    """
    size = int(cfg.get("captions.size", 88))
    fill = _colour(cfg.get("captions.secondary", "&H00FFFFFF"))
    accent_fill = _colour(cfg.get("captions.primary", "&H004DD8FF"))
    stroke = _colour(cfg.get("captions.outline", "&H00101010"))
    stroke_w = int(cfg.get("captions.outline_width", 7))
    max_w = w * MAX_WIDTH_RATIO

    text = " ".join(words)
    font = _fit_font(_font_path(cfg), text, size, max_w)

    # lay the words out so the active one can be coloured independently
    space = font.getlength(" ")
    widths = [font.getlength(x) for x in words]
    total = sum(widths) + space * (len(words) - 1)
    x = (w - total) / 2

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i, word in enumerate(words):
        colour = accent_fill if (hook or active == i) else fill
        for dx, dy, col in ((0, SHADOW_OFFSET, (0, 0, 0, SHADOW_ALPHA)),
                            (0, 0, colour)):
            draw.text(
                (x + dx, y + dy), word, font=font, fill=col, anchor="lm",
                stroke_width=stroke_w,
                stroke_fill=(0, 0, 0, SHADOW_ALPHA) if dy else stroke,
            )
        x += widths[i] + space
    img.save(path, optimize=True)


def render(cfg, scenes: list[dict], out_dir: Path) -> list[dict]:
    """Returns [{png, start, end}] on the master timeline, in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    y = int(cfg.get("captions.y", 1230))
    upper = bool(cfg.get("captions.uppercase", True))
    max_words = int(cfg.get("captions.max_words_per_line", 3))
    max_chars = int(cfg.get("captions.max_chars_per_line", 20))
    # "all"  - every word highlighted (accurate, but ~8 min encode per reel)
    # "hook" - only scene one, where attention is decided (~50 extra overlays)
    # false  - one card per phrase
    mode = cfg.get("captions.per_word", "hook")
    per_word = True if mode is True else str(mode).lower()
    accent_hook = bool(cfg.get("captions.accent_hook", True))

    items: list[dict] = []
    n = 0
    for scene_i, scene in enumerate(scenes):
        base = float(scene.get("offset", 0.0))
        for chunk in _chunks(scene.get("words", []), max_words, max_chars):
            words = [x["w"].upper() if upper else x["w"] for x in chunk]
            # Scene one gets the accent colour throughout: the first frame is the
            # only one you are guaranteed a viewer sees.
            is_hook = accent_hook and scene_i == 0

            want_words = per_word in ("all", "true", "yes") or (
                per_word == "hook" and scene_i == 0
            )
            if want_words and len(chunk) > 1:
                for i, word in enumerate(chunk):
                    n += 1
                    png = out_dir / f"cap_{n:04d}.png"
                    _draw_card(cfg, png, words, active=i, w=w, h=h, y=y, hook=is_hook)
                    items.append({
                        "png": str(png),
                        "start": round(base + word["start"], 3),
                        "end": round(base + word["end"] + 0.02, 3),
                    })
            else:
                n += 1
                png = out_dir / f"cap_{n:04d}.png"
                _draw_card(cfg, png, words, active=None, w=w, h=h, y=y, hook=is_hook)
                items.append({
                    "png": str(png),
                    "start": round(base + chunk[0]["start"], 3),
                    "end": round(base + chunk[-1]["end"] + 0.10, 3),
                })

    # Close gaps so the text does not flicker between words, but never let two
    # cards be on screen at once - they would composite on top of each other.
    for i in range(len(items) - 1):
        limit = items[i + 1]["start"] - 0.01
        if limit <= items[i]["start"]:
            continue
        items[i]["end"] = round(max(min(items[i]["end"], limit),
                                    min(items[i]["start"] + 3.5, limit)), 3)
    return items
