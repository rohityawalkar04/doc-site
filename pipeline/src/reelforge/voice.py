"""Voice synthesis.

edge-tts is used because it returns WordBoundary events: exact per-word offsets,
in 100-nanosecond ticks. That removes the need for any speech alignment step, so
captions cost nothing in time or money.

The engine is pluggable because edge-tts output is not clearly licensed for
commercial reuse. `kokoro` (Apache-2.0) is the rights-clean path.
"""
from __future__ import annotations

import asyncio
import random
import shutil
import subprocess
import time
from pathlib import Path

import edge_tts

RETRIES = 6
SCENE_GAP = 0.45  # the endpoint rate-limits hard; a small gap is cheaper than a retry
ATTEMPT_TIMEOUT = 70  # seconds; a hung request must never block the reel


async def _edge_once(text: str, voice: str, rate: str, pitch: str, out_path: Path) -> list[dict]:
    words: list[dict] = []
    with open(out_path, "wb") as f:
        comm = edge_tts.Communicate(
            text, voice, rate=rate, pitch=pitch, boundary="WordBoundary"
        )
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start = chunk["offset"] / 1e7
                words.append(
                    {
                        "w": chunk["text"],
                        "start": round(start, 4),
                        "end": round(start + chunk["duration"] / 1e7, 4),
                    }
                )
    return words


def _even_words(text: str, seconds: float) -> list[dict]:
    """No boundaries returned: spread words across the real duration.

    Captions stay readable and roughly in sync, which beats failing the reel.
    """
    toks = [t for t in text.split() if t]
    if not toks:
        return []
    step = (seconds * 0.96) / len(toks)
    return [
        {"w": t, "start": round(0.02 + i * step, 4), "end": round(0.02 + (i + 1) * step, 4)}
        for i, t in enumerate(toks)
    ]


async def _edge_scene(text: str, voice: str, rate: str, pitch: str, out_path: Path) -> list[dict]:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            # A hard timeout per attempt. Without it, a hung aiohttp read blocks
            # the whole reel forever - observed at 13 min on a single scene when
            # several reels requested TTS at once.
            words = await asyncio.wait_for(
                _edge_once(text, voice, rate, pitch, out_path), timeout=ATTEMPT_TIMEOUT
            )
            if out_path.stat().st_size > 2000:
                if words:
                    return words
                # Audio arrived without boundaries - degrade, do not fail.
                print("      ! no word boundaries, falling back to even spacing")
                return []
            last = RuntimeError("empty audio")
        except (Exception, asyncio.TimeoutError) as e:  # NoAudioReceived, network, 403, hang
            last = e if not isinstance(e, asyncio.TimeoutError) else RuntimeError(
                f"timed out after {ATTEMPT_TIMEOUT}s"
            )
        # Jittered backoff: parallel retries at a fixed delay just re-collide.
        await asyncio.sleep(0.8 * (attempt + 1) + random.random() * 0.7)
    raise RuntimeError(f"edge-tts failed after {RETRIES} attempts: {last}")


def _kokoro_scene(text: str, voice: str, out_path: Path) -> list[dict]:
    """Local Apache-2.0 TTS. No word timings, so captions fall back to even spacing."""
    from kokoro import KPipeline  # noqa: PLC0415 - optional dependency
    import numpy as np
    import soundfile as sf

    pipe = KPipeline(lang_code="a")
    chunks = []
    for _gs, _ps, audio in pipe(text, voice=voice):
        chunks.append(audio)
    if not chunks:
        raise RuntimeError("kokoro returned no audio")
    audio = np.concatenate(chunks)
    sf.write(str(out_path.with_suffix(".wav")), audio, 24000)
    # No boundaries available: spread words across the measured duration.
    dur = len(audio) / 24000
    toks = [t for t in text.split() if t]
    step = dur / max(1, len(toks))
    return [
        {"w": t, "start": round(i * step, 4), "end": round((i + 1) * step, 4)}
        for i, t in enumerate(toks)
    ]


def synthesise(cfg, scenes: list[dict], audio_dir: Path) -> list[dict]:
    """Writes one audio file per scene. Returns scenes with audio_path + words."""
    audio_dir.mkdir(parents=True, exist_ok=True)
    engine = cfg.get("voice.engine", "edge")
    voice = cfg.get("voice.name", "en-US-AndrewNeural")
    rate = cfg.get("voice.rate", "+0%")
    pitch = cfg.get("voice.pitch", "+0Hz")

    out: list[dict] = []
    total = len(scenes)
    for i, scene in enumerate(scenes, 1):
        print(f"  [{i}/{total}] voice ({engine}) ...", flush=True)
        if engine == "kokoro":
            path = audio_dir / f"scene_{i:02d}.wav"
            words = _kokoro_scene(scene["narration"], cfg.get("voice.kokoro_voice", "af_heart"), path)
        else:
            path = audio_dir / f"scene_{i:02d}.mp3"
            words = asyncio.run(_edge_scene(scene["narration"], voice, rate, pitch, path))
            if not words:
                words = _even_words(scene["narration"], duration(path))
            time.sleep(SCENE_GAP)
        out.append({**scene, "audio_path": str(path), "words": words})
    return out


def duration(path: str | Path) -> float:
    exe = shutil.which("ffprobe")
    if not exe:
        raise RuntimeError("ffprobe not found on PATH")
    res = subprocess.run(
        [exe, "-v", "error", "-show_entries", "format=duration", "-of",
         "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {path}: {res.stderr.strip()}")
    return float(res.stdout.strip())
