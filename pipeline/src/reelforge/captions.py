"""ASS subtitle generation with per-word karaoke.

edge-tts gives exact word start/end offsets, so each word's karaoke slot runs
from its own start to the *next* word's start. That absorbs the pauses between
words, which is what stops a caption line drifting out of sync halfway through.
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {w}
PlayResY: {h}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{font},{size},{primary},{secondary},{outline},&H00000000,0,0,0,0,100,100,1.2,0,1,{border},0,5,64,64,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

PUNCT_BREAK = re.compile(r"[.!?;,]$")


def _ts(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape(text: str) -> str:
    return text.replace("\\", "").replace("{", "(").replace("}", ")").replace("\n", " ")


def _chunks(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    out: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        cand = cur + [w]
        text = " ".join(x["w"] for x in cand)
        too_long = len(cand) > max_words or len(text) > max_chars
        if cur and too_long:
            out.append(cur)
            cur = [w]
        else:
            cur = cand
        if PUNCT_BREAK.search(w["w"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [c for c in out if c]


def build(cfg, scenes: list[dict], out_path: Path) -> Path:
    """scenes: [{words: [...], offset: float}] with offset = scene start on the master timeline."""
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    max_words = int(cfg.get("captions.max_words_per_line", 3))
    max_chars = int(cfg.get("captions.max_chars_per_line", 20))
    upper = bool(cfg.get("captions.uppercase", True))
    y = int(cfg.get("captions.y", 1180))

    lines = [
        HEADER.format(
            w=w, h=h,
            font=cfg.get("captions.font", "Anton"),
            size=int(cfg.get("captions.size", 88)),
            primary=cfg.get("captions.primary", "&H004DD8FF"),
            secondary=cfg.get("captions.secondary", "&H00FFFFFF"),
            outline=cfg.get("captions.outline", "&H00101010"),
            border=int(cfg.get("captions.outline_width", 7)),
        )
    ]

    for scene in scenes:
        base = float(scene.get("offset", 0.0))
        for chunk in _chunks(scene.get("words", []), max_words, max_chars):
            start = base + chunk[0]["start"]
            parts = []
            for i, word in enumerate(chunk):
                slot_start = word["start"]
                slot_end = chunk[i + 1]["start"] if i + 1 < len(chunk) else word["end"]
                cs = max(2, round(max(0.06, slot_end - slot_start) * 100))
                text = _escape(word["w"].upper() if upper else word["w"])
                parts.append(r"{\k%d}%s " % (cs, text))
            end = base + chunk[-1]["end"] + 0.06

            # Subtle pop-in per chunk: start at 92% and settle to 100%.
            head = (
                r"{\an5\pos(%d,%d)\fscx92\fscy92\t(0,140,\fscx100\fscy100)\fad(40,40)}"
                % (w // 2, y)
            )
            lines.append(
                f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{head}{''.join(parts).strip()}"
            )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out_path
