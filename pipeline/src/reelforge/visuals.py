"""Background visuals: one mp4 per scene, already 1080x1920 @ 30fps.

Providers
  procedural : ffmpeg `gradients` source. Zero keys, zero licensing questions,
               but identical-looking across videos - a testing default only.
  openverse  : openly licensed images via anonymous API; CC0 only, no key.
  local      : files you drop into assets/broll/.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from . import fallback

# Zoom/pan variants, cycled per scene. A reel where every shot pushes in at the
# same speed reads as static even though it is moving.
MOTIONS = ("in", "out", "left", "right", "in", "out")

GRADIENT_ANGLES = [(0, 0, 1080, 1920), (1080, 0, 0, 1920), (0, 1920, 1080, 0), (540, 0, 540, 1920)]


def _run(cmd: list[str]) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            "ffmpeg failed\n  cmd: " + " ".join(cmd[:12]) + "...\n  err: "
            + res.stderr.strip()[-1200:]
        )


def _hex(c: str) -> str:
    return "0x" + c.lstrip("#")


# --------------------------------------------------------------------------- procedural
def procedural(cfg, index: int, seconds: float, out: Path) -> Path:
    palette = cfg.get("visuals.palette") or ["#0f0c29", "#302b63", "#24243e"]
    rnd = random.Random(index * 7919)
    cols = rnd.sample(palette, min(3, len(palette))) if len(palette) >= 3 else palette * 3
    x0, y0, x1, y1 = GRADIENT_ANGLES[index % len(GRADIENT_ANGLES)]
    speed = 0.004 + (index % 5) * 0.002
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    fps = int(cfg.get("visuals.fps", 30))
    src = (
        f"gradients=s={w}x{h}:c0={_hex(cols[0])}:c1={_hex(cols[1])}:c2={_hex(cols[2])}"
        f":nb_colors=3:x0={x0}:y0={y0}:x1={x1}:y1={y1}:speed={speed}:d={seconds:.3f}:r={fps}"
    )
    # `noise` is temporal and costs roughly 3x the encode time. Off by default;
    # gradient banding is barely visible at this bitrate.
    vf = "vignette=PI/4.2,format=yuv420p"
    if cfg.get("visuals.grain", False):
        vf = "noise=alls=7:allf=t+u," + vf
    _run([
        "ffmpeg", "-y", "-f", "lavfi", "-i", src,
        "-vf", vf,
        "-t", f"{seconds:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", str(cfg.get("visuals.preset", "ultrafast")),
        "-crf", str(cfg.get("visuals.crf", 20)), "-pix_fmt", "yuv420p",
        str(out),
    ])
    return out


# --------------------------------------------------------------------------- helpers
def _fit_filter(cfg, motion: bool) -> str:
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    fps = int(cfg.get("visuals.fps", 30))
    chain = (
        f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase,"
        f"crop={w * 2}:{h * 2},setsar=1"
    )
    if motion:
        chain += (
            f",zoompan=z='1.02+0.0012*on':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d=1:s={w}x{h}:fps={fps}"
        )
    else:
        chain += f",scale={w}:{h},fps={fps}"
    return chain


def from_video(cfg, src: Path, seconds: float, out: Path) -> Path:
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(src),
        "-vf", _fit_filter(cfg, motion=False),
        "-t", f"{seconds:.3f}",
        "-c:v", "libx264", "-preset", str(cfg.get("visuals.preset", "ultrafast")),
        "-crf", str(cfg.get("visuals.crf", 20)), "-pix_fmt", "yuv420p",
        "-an", str(out),
    ])
    return out


def from_image(cfg, src: Path, seconds: float, out: Path, motion: str = "in") -> Path:
    """Animate a still with a slow zoom or pan.

    Cheap motion, but it is the difference between "a slideshow" and "a video".
    Uses `on` (output frame index) rather than the accumulating `zoom` variable
    so each shot's motion is deterministic and reproducible.
    """
    fps = int(cfg.get("visuals.fps", 30))
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    frames = max(2, int(seconds * fps))
    over = 1.6  # headroom for the crop, so panning never reveals an edge
    centre = "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
    rate = float(cfg.get("visuals.zoom_rate", 0.0024))
    zmax = float(cfg.get("visuals.zoom_max", 1.34))

    if motion == "out":
        z = f"'max({zmax:.3f}-{rate}*on,1.001)'"
        pos = centre
    elif motion == "left":
        z = f"'{(zmax + 0.02):.3f}'"
        pos = f"x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'"
    elif motion == "right":
        z = f"'{(zmax + 0.02):.3f}'"
        pos = f"x='(iw-iw/zoom)*(on/{frames})':y='ih/2-(ih/zoom/2)'"
    else:  # "in"
        z = f"'min(1.0+{rate}*on,{zmax})'"
        pos = centre

    vf = (
        f"scale={int(w * over)}:{int(h * over)}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={int(w * over)}:{int(h * over)},setsar=1,"
        f"zoompan=z={z}:{pos}:d=1:s={w}x{h}:fps={fps},"
        # Generated images arrive at 512x768, so this is a ~2x upscale. Lanczos
        # plus a light unsharp keeps flat-vector edges crisp instead of mushy.
        f"unsharp=5:5:0.7:3:3:0.0,"
        # JPEG sources are full-range (yuvj420p). Without this conversion the
        # full range leaks into the output and contrast shifts on upload.
        f"scale=in_range=full:out_range=limited,format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-loop", "1", "-framerate", str(fps), "-i", str(src),
        "-vf", vf, "-t", f"{seconds:.3f}", "-r", str(fps),
        "-c:v", "libx264", "-preset", str(cfg.get("visuals.preset", "ultrafast")),
        "-crf", str(cfg.get("visuals.crf", 20)), "-pix_fmt", "yuv420p",
        str(out),
    ])
    return out


# --------------------------------------------------------------------------- ai images
def _ai_cache() -> Path:
    p = Path(os.environ.get("REELFORGE_ROOT", ".")) / "assets" / "cache" / "ai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_prompt(cfg, query: str) -> str:
    style = " ".join((cfg.get("visuals.style_prompt") or "").split())
    q = " ".join((query or "").split())
    return f"{q}, {style}" if style else q


_MFLUX_MODEL = None


def _mflux_model(cfg):
    """Load the quantized Z-Image-Turbo model once per process on Apple Silicon."""
    global _MFLUX_MODEL
    if sys.platform != "darwin" or os.uname().machine != "arm64":
        raise RuntimeError(
            "The mflux_z_image backend requires an Apple Silicon Mac. "
            "Run reel generation on the M4 Mac, not a GitHub-hosted Linux runner."
        )
    if _MFLUX_MODEL is None:
        from mflux.models.z_image import ZImageTurbo

        quantize = int(cfg.get("visuals.mflux_quantize", 4))
        _MFLUX_MODEL = ZImageTurbo(quantize=quantize)
    return _MFLUX_MODEL


def _gen_mflux_z_image(cfg, prompt: str, seed: int, dest: Path) -> Path | None:
    """Generate with Z-Image-Turbo through native Apple MLX (no network inference)."""
    model = _mflux_model(cfg)
    width = max(256, (int(cfg.get("visuals.ai_width", 512)) // 16) * 16)
    height = max(256, (int(cfg.get("visuals.ai_height", 768)) // 16) * 16)
    steps = int(cfg.get("visuals.local_steps", 9))
    image = model.generate_image(
        prompt=prompt,
        seed=seed & 0x7FFFFFFF,
        num_inference_steps=steps,
        width=width,
        height=height,
    )
    image.save(str(dest))
    return dest

def generate_image(cfg, query: str, seed: int, dest: Path) -> Path | None:
    """Generate one local AI image. Cached on disk by seed, so rerenders are free."""
    if dest.exists():
        try:
            if dest.stat().st_size > 5_000:
                with Image.open(dest) as cached:
                    cached.verify()
                return dest
        except Exception as e:
            print(f"    ! discarding invalid cached image {dest.name}: {e}")
        dest.unlink(missing_ok=True)

    prompt = build_prompt(cfg, query)
    backend = cfg.get("visuals.ai_backend", "mflux_z_image")
    if backend != "mflux_z_image":
        raise RuntimeError(
            f"Unsupported visual backend {backend!r}. This channel is configured "
            "for local MFLUX + Z-Image-Turbo only; remote image services are disabled."
        )

    provider_name = "mflux_z_image"
    if not fallback.available(provider_name):
        raise RuntimeError("Local MFLUX backend is in cooldown after a previous failure.")
    try:
        img = _gen_mflux_z_image(cfg, prompt, seed, dest)
        if img is None or not img.exists() or img.stat().st_size < 5_000:
            raise ValueError("generated image is missing or too small")
        with Image.open(img) as check:
            check.verify()
        fallback.enable(provider_name)
        return img
    except Exception as e:
        fallback.disable(provider_name, f"{type(e).__name__}: {e}", seconds=900)
        raise RuntimeError(f"Local Z-Image-Turbo generation failed: {e}") from e


# --------------------------------------------------------------------------- entry point
def for_scene(cfg, store, index: int, seconds: float, query: str, out: Path,
              used: set[str] | None = None) -> tuple[Path, str]:
    """Render a scene using locally generated AI imagery only."""
    provider = cfg.get("visuals.provider", "ai_cartoon")
    if provider not in ("ai_cartoon", "ai"):
        raise RuntimeError(
            f"Visual provider {provider!r} is disabled. Gita & Grit uses only "
            "local MFLUX Z-Image-Turbo generation; image search and stock pickers are removed."
        )

    animate = bool(cfg.get("visuals.animate", True))
    motion = MOTIONS[index % len(MOTIONS)] if animate else "in"
    seed = int(hashlib.sha1(f"{query}|{index}".encode()).hexdigest()[:8], 16)
    dest = _ai_cache() / f"{seed:08x}.jpg"
    img = generate_image(cfg, query, seed, dest)
    if img is None:
        raise RuntimeError(
            f"required local AI image unavailable for scene {index + 1}: {query!r}; "
            "refusing to render a gradient placeholder"
        )
    store.bump_visual(f"ai:{seed:08x}")
    return from_image(cfg, img, seconds, out, motion), f"ai:{seed:08x}"

def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
