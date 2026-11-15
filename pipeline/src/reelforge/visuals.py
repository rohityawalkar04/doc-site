"""Background visuals: one mp4 per scene, already 1080x1920 @ 30fps.

Providers
  procedural : ffmpeg `gradients` source. Zero keys, zero licensing questions,
               but identical-looking across videos - a testing default only.
  pexels     : licensed stock footage/stills. Free API key, 200 req/hour.
  local      : files you drop into assets/broll/.
"""
from __future__ import annotations

import hashlib
import os
import random
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests

PEXELS_VIDEO = "https://api.pexels.com/videos/search"
PEXELS_PHOTO = "https://api.pexels.com/v1/search"
POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}"

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
    if motion == "out":
        z = f"'max({1.28:.3f}-0.0016*on,1.001)'"
        pos = centre
    elif motion == "left":
        z = "'1.14'"
        pos = f"x='(iw-iw/zoom)*(1-on/{frames})':y='ih/2-(ih/zoom/2)'"
    elif motion == "right":
        z = "'1.14'"
        pos = f"x='(iw-iw/zoom)*(on/{frames})':y='ih/2-(ih/zoom/2)'"
    else:  # "in"
        z = "'min(1.0+0.0016*on,1.28)'"
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


# --------------------------------------------------------------------------- pexels
def _cache_dir() -> Path:
    p = Path(os.environ.get("REELFORGE_ROOT", ".")) / "assets" / "cache" / "pexels"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pexels_candidates(key: str, query: str, orientation: str) -> list[tuple[str, str]]:
    """Every usable (kind, url) for a query, videos first."""
    out: list[tuple[str, str]] = []
    headers = {"Authorization": key}
    try:
        r = requests.get(
            PEXELS_VIDEO,
            headers=headers,
            params={"query": query, "orientation": orientation, "per_page": 30},
            timeout=25,
        )
        if r.status_code == 200:
            for v in r.json().get("videos", []):
                files = sorted(
                    (f for f in v.get("video_files", []) if f.get("height", 0) >= 1280),
                    key=lambda f: f.get("height", 0),
                )
                if files:
                    out.append(("video", files[0]["link"]))
    except requests.RequestException:
        pass
    if not out:
        try:
            r = requests.get(
                PEXELS_PHOTO,
                headers=headers,
                params={"query": query, "orientation": orientation, "per_page": 30},
                timeout=25,
            )
            if r.status_code == 200:
                for p in r.json().get("photos", []):
                    out.append(("photo", p["src"]["portrait"]))
        except requests.RequestException:
            pass
    return out


def _download(url: str, dest: Path) -> Path:
    if dest.exists() and dest.stat().st_size > 10_000:
        return dest
    with requests.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 16):
                f.write(chunk)
    return dest


# --------------------------------------------------------------------------- ai images
def _ai_cache() -> Path:
    p = Path(os.environ.get("REELFORGE_ROOT", ".")) / "assets" / "cache" / "ai"
    p.mkdir(parents=True, exist_ok=True)
    return p


def build_prompt(cfg, query: str) -> str:
    style = " ".join((cfg.get("visuals.style_prompt") or "").split())
    q = " ".join((query or "").split())
    return f"{q}, {style}" if style else q


_PIPE = None


def _local_pipe(cfg):
    """Load the local diffusion pipeline once per process (MPS on Apple Silicon)."""
    global _PIPE
    if _PIPE is None:
        import torch
        from diffusers import AutoPipelineForText2Image

        model = cfg.get("visuals.local_model", "stabilityai/sdxl-turbo")
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        dtype = torch.float16 if device == "mps" else torch.float32
        try:
            pipe = AutoPipelineForText2Image.from_pretrained(
                model, torch_dtype=dtype, variant="fp16"
            )
        except Exception:
            pipe = AutoPipelineForText2Image.from_pretrained(model, torch_dtype=dtype)
        pipe.to(device)
        try:
            pipe.set_progress_bar_config(disable=True)
        except Exception:
            pass
        _PIPE = (pipe, torch, device)
    return _PIPE


def _gen_local(cfg, prompt: str, seed: int, dest: Path) -> Path | None:
    """Local text-to-image. Free, unlimited, offline; costs wall-clock time only.

    Turbo/SDXL-turbo checkpoints are distilled: guidance_scale must be 0.0 and
    1-4 steps, otherwise output is washed out.
    """
    try:
        pipe, torch, device = _local_pipe(cfg)
    except Exception as e:
        print(f"    ! local diffusion unavailable ({type(e).__name__}: {e})")
        return None

    w = int(cfg.get("visuals.ai_width", 832))
    h = int(cfg.get("visuals.ai_height", 1216))
    # SDXL requires both dimensions divisible by 8; 1138 silently raises.
    w = (w // 8) * 8
    h = (h // 8) * 8
    steps = int(cfg.get("visuals.local_steps", 2))
    gen = torch.Generator(device="cpu").manual_seed(seed & 0x7FFFFFFF)
    try:
        image = pipe(
            prompt=prompt,
            num_inference_steps=steps,
            guidance_scale=0.0,
            width=w,
            height=h,
            generator=gen,
        ).images[0]
    except Exception as e:
        print(f"    ! local generation failed ({type(e).__name__}: {e})")
        return None
    image.save(dest, quality=95)
    return dest


def _gen_pollinations(cfg, prompt: str, seed: int, dest: Path) -> Path | None:
    """Free image generation via Pollinations.

    Empirically (Nov 2026): `flux` returns 402, `sana` works but is throttled so
    hard that ~1 in 5 requests succeeds. Kept as a secondary backend only.
    """
    w = min(int(cfg.get("visuals.ai_width", 1024)), int(cfg.get("visuals.width", 1080)))
    h = min(int(cfg.get("visuals.ai_height", 1792)), int(cfg.get("visuals.height", 1920)))
    params = {
        "width": w,
        "height": h,
        "seed": seed,
        "model": cfg.get("visuals.ai_model", "sana"),
        "nologo": "true",
        "enhance": "false",
    }
    url = POLLINATIONS.format(prompt=quote(prompt))
    retries = int(cfg.get("visuals.ai_retries", 4))
    backoff = float(cfg.get("visuals.ai_backoff", 5))
    timeout = int(cfg.get("visuals.ai_timeout", 120))

    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 200 and len(r.content) > 5000:
                dest.write_bytes(r.content)
                return dest
            # 402 here means "throttled on the anonymous tier", not "pay up".
        except requests.RequestException:
            pass
        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1) * 0.5)
    return None


def generate_image(cfg, query: str, seed: int, dest: Path) -> Path | None:
    """Generate one illustration. Cached on disk by seed, so re-renders are free."""
    if dest.exists() and dest.stat().st_size > 5000:
        return dest

    prompt = build_prompt(cfg, query)
    backend = cfg.get("visuals.ai_backend", "local_sd")

    if backend == "local_sd":
        img = _gen_local(cfg, prompt, seed, dest)
        if img is not None:
            return img
        # One shot at the hosted backend before giving up on the scene.
        return _gen_pollinations(cfg, prompt, seed, dest)

    img = _gen_pollinations(cfg, prompt, seed, dest)
    if img is not None:
        return img
    return _gen_local(cfg, prompt, seed, dest)


# --------------------------------------------------------------------------- local
def _local_files() -> list[Path]:
    root = Path(os.environ.get("REELFORGE_ROOT", "."))
    d = root / "assets" / "broll"
    if not d.exists():
        return []
    exts = {".mp4", ".mov", ".m4v", ".jpg", ".jpeg", ".png", ".webp"}
    return sorted(p for p in d.iterdir() if p.suffix.lower() in exts)


# --------------------------------------------------------------------------- entry point
def for_scene(cfg, store, index: int, seconds: float, query: str, out: Path,
              used: set[str] | None = None) -> tuple[Path, str]:
    provider = cfg.get("visuals.provider", "procedural")
    orientation = cfg.get("visuals.pexels_orientation", "portrait")
    used = used if used is not None else set()
    animate = bool(cfg.get("visuals.animate", True))
    motion = MOTIONS[index % len(MOTIONS)] if animate else "in"

    if provider in ("ai_cartoon", "ai"):
        # Seed from query + position: distinct image per scene even when two
        # scenes share a visual_query, but stable across re-renders.
        seed = int(hashlib.sha1(f"{query}|{index}".encode()).hexdigest()[:8], 16)
        dest = _ai_cache() / f"{seed:08x}.jpg"
        img = generate_image(cfg, query, seed, dest)
        if img is not None:
            store.bump_visual(f"ai:{seed:08x}")
            return from_image(cfg, img, seconds, out, motion), f"ai:{seed:08x}"
        print("    ! AI image unavailable after retries, using procedural")

    if provider == "pexels":
        key = os.environ.get("PEXELS_API_KEY", "").strip()
        if key:
            cands = [
                c for c in _pexels_candidates(key, query, orientation) if c[1] not in used
            ]
            if not cands:
                # Retry without the dedupe filter rather than repeating a scene.
                cands = _pexels_candidates(key, query, orientation)
            if cands:
                # Rotate by scene index: identical queries still yield different clips.
                kind, url = cands[index % len(cands)]
                used.add(url)
                digest = hashlib.sha1(url.encode()).hexdigest()[:16]
                ext = ".mp4" if kind == "video" else ".jpg"
                cached = _download(url, _cache_dir() / f"{digest}{ext}")
                store.bump_visual(digest)
                if kind == "video":
                    return from_video(cfg, cached, seconds, out), f"pexels:{digest[:8]}"
                return from_image(cfg, cached, seconds, out), f"pexels:{digest[:8]}"
        else:
            print("    ! PEXELS_API_KEY missing, using procedural")

    if provider == "local":
        files = _local_files()
        if files:
            # Least-used file first: stops the same clip appearing in every reel.
            files.sort(key=lambda p: (store.visual_uses(p.name), p.name))
            src = next((f for f in files if f.name not in used), files[0])
            used.add(src.name)
            store.bump_visual(src.name)
            if src.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                return from_video(cfg, src, seconds, out), f"local:{src.name}"
            return from_image(cfg, src, seconds, out), f"local:{src.name}"
        print("    ! assets/broll/ is empty, using procedural")

    return procedural(cfg, index, seconds, out), "procedural"


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
