"""SRT subtitles for long-form: soft, searchable, and cheap.

The reel format draws per-phrase PNG cards because a Short is watched on mute and
the caption *is* the hook. A three-minute video is watched with sound, and a few
hundred Pillow cards would cost minutes of render time for something that looks
worse than an ordinary subtitle track. So long-form gets an SRT muxed as a soft
track: no render cost, YouTube indexes the text, and the viewer can switch it off.

Cues are cut on word boundaries with a two-line maximum, which is the broadcast
convention and what YouTube's own auto-captions approximate.
"""
from __future__ import annotations


def _cues_from_words(words: list[dict], start: float, max_chars: int) -> list[tuple[float, float, str]]:
    """Group word timings into cues of at most `max_chars`, never splitting a word."""
    cues: list[tuple[float, float, str]] = []
    cur: list[dict] = []
    for w in words:
        candidate = " ".join(x["w"] for x in cur + [w])
        if cur and len(candidate) > max_chars:
            cues.append((cur[0]["start"] + start, cur[-1]["end"] + start,
                         " ".join(x["w"] for x in cur)))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        cues.append((cur[0]["start"] + start, cur[-1]["end"] + start,
                     " ".join(x["w"] for x in cur)))
    return cues


def _stamp(seconds: float) -> str:
    ms = int(round(max(0.0, seconds) * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1_000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def build(cfg, scenes: list[dict], out) -> "object":
    """Write one .srt for the whole reel. Returns the path.

    Each scene carries `offset` (its start in the final timeline), `seconds` and,
    when the voice engine provides them, `words` with absolute times inside that
    scene. Without word timings the sentence becomes one cue spanning the scene,
    which is still readable and never overlaps.
    """
    max_chars = int(cfg.get("captions.srt_chars", 84))
    cues: list[tuple[float, float, str]] = []

    for scene in scenes:
        start = float(scene.get("offset", 0.0))
        dur = float(scene.get("seconds", 0.0))
        words = scene.get("words") or []
        if words:
            cues.extend(_cues_from_words(words, start, max_chars))
            continue
        text = str(scene.get("narration") or "").strip()
        if text:
            cues.append((start, start + max(1.0, dur), text))

    # A cue that outlasts the next one's start would overlap on playback; trim it
    # to just before the next cue, which is what a subtitle editor would do.
    fixed: list[tuple[float, float, str]] = []
    for i, (s, e, text) in enumerate(cues):
        if i + 1 < len(cues) and cues[i + 1][0] > s:
            e = min(e, cues[i + 1][0] - 0.01)
        fixed.append((s, max(e, s + 0.4), text))

    body = []
    for i, (s, e, text) in enumerate(fixed, 1):
        body.append(f"{i}\n{_stamp(s)} --> {_stamp(e)}\n{text}\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(body), encoding="utf-8")
    return out
