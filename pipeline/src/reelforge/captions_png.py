"""Caption rendering to transparent PNGs, composited by ffmpeg's `overlay`.

Why not ASS: Homebrew's ffmpeg 8 build has no libass / freetype / fontconfig, so
the `ass`, `subtitles` and `drawtext` filters simply do not exist. Rendering the
text ourselves removes that dependency entirely and works on any ffmpeg build
(and on a Linux VPS later).

`captions.mode: ass` is still supported and is used automatically if the ffmpeg
on PATH was built with libass - it gives true per-word karaoke highlighting.
"""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PUNCT_BREAK = set(".!?;,:")
MAX_WIDTH_RATIO = 0.86
LINE_HEIGHT_RATIO = 1.04
SHADOW_ALPHA = 150
SHADOW_OFFSET = 5


def _font(cfg, size: int) -> ImageFont.FreeTypeFont:
    name = cfg.get("captions.font", "Anton")
    candidate = cfg.path(f"assets/fonts/{name}-Regular.ttf")
    if not candidate.exists():
        for p in sorted(cfg.path("assets/fonts").glob("*.ttf")):
            if name.lower() in p.name.lower():
                candidate = p
                break
        else:
            raise RuntimeError(f"no caption font found in {cfg.path('assets/fonts')}")
    return ImageFont.truetype(str(candidate), size)


def _colour(ass_style: str) -> tuple[int, int, int, int]:
    """'&HAABBGGRR' -> RGBA tuple."""
    h = ass_style.strip().lstrip("&H").lstrip("hH")
    h = h.zfill(8)
    a, b, g, r = (int(h[i : i + 2], 16) for i in (0, 2, 4, 6))
    return (r, g, b, 255 - a)


def _wrap(words: list[str], font, max_width: float) -> list[str]:
    lines: list[list[str]] = [[]]
    for w in words:
        trial = " ".join(lines[-1] + [w])
        if lines[-1] and font.getlength(trial) > max_width:
            lines.append([w])
        else:
            lines[-1].append(w)
    return [" ".join(l) for l in lines if l]


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


def render(cfg, scenes: list[dict], out_dir: Path) -> list[dict]:
    """Returns [{png, start, end}] on the master timeline, in order."""
    out_dir.mkdir(parents=True, exist_ok=True)
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    size = int(cfg.get("captions.size", 88))
    y = int(cfg.get("captions.y", 1180))
    upper = bool(cfg.get("captions.uppercase", True))
    max_words = int(cfg.get("captions.max_words_per_line", 3))
    max_chars = int(cfg.get("captions.max_chars_per_line", 20))
    fill = _colour(cfg.get("captions.secondary", "&H00FFFFFF"))
    stroke = _colour(cfg.get("captions.outline", "&H00101010"))
    stroke_w = int(cfg.get("captions.outline_width", 7))
    font = _font(cfg, size)
    line_h = int(size * LINE_HEIGHT_RATIO)
    max_w = w * MAX_WIDTH_RATIO

    items: list[dict] = []
    n = 0
    for scene in scenes:
        base = float(scene.get("offset", 0.0))
        for chunk in _chunks(scene.get("words", []), max_words, max_chars):
            n += 1
            raw = " ".join(x["w"] for x in chunk)
            text = raw.upper() if upper else raw
            lines = _wrap(text.split(), font, max_w)[:3]

            img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            block_h = line_h * len(lines)
            top = y - block_h // 2
            for i, line in enumerate(lines):
                cy = top + i * line_h + line_h // 2
                # Hard drop shadow first: legibility over light b-roll.
                draw.text(
                    (w // 2, cy + SHADOW_OFFSET), line, font=font,
                    fill=(0, 0, 0, SHADOW_ALPHA), anchor="mm", stroke_width=stroke_w,
                    stroke_fill=(0, 0, 0, SHADOW_ALPHA),
                )
                draw.text(
                    (w // 2, cy), line, font=font, fill=fill, anchor="mm",
                    stroke_width=stroke_w, stroke_fill=stroke,
                )

            png = out_dir / f"cap_{n:03d}.png"
            img.save(png, optimize=True)
            items.append(
                {
                    "png": str(png),
                    "start": round(base + chunk[0]["start"], 3),
                    "end": round(base + chunk[-1]["end"] + 0.10, 3),
                }
            )

    # Close the gaps: a caption that vanishes for 0.4 s between chunks reads as
    # flicker. Extend each card up to the next one, but never past it - two
    # overlapping overlay windows would composite two cards on screen at once.
    for i in range(len(items) - 1):
        limit = items[i + 1]["start"] - 0.01
        if limit <= items[i]["start"]:
            continue
        held = min(items[i]["start"] + 3.5, limit)   # cap a long silence
        items[i]["end"] = round(max(min(items[i]["end"], limit), held), 3)
    return items
