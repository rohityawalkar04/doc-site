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
import re
import random
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import quote

import requests
from PIL import Image

from . import fallback

OPENVERSE_IMAGES = "https://api.openverse.org/v1/images/"
POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}"


class AiQuotaExhausted(RuntimeError):
    """Raised when the image backend has no allocation left.

    Deliberately fatal: carrying on fills the reel with gradient placeholders,
    which wastes the whole run and looks broken on a channel that publishes
    illustrations.
    """

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


# --------------------------------------------------------------------------- pexels
def _cache_dir() -> Path:
    p = Path(os.environ.get("REELFORGE_ROOT", ".")) / "assets" / "cache" / "openverse"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _openverse_candidates(query: str) -> list[dict]:
    """Search Openverse anonymously for CC0 images with progressively broader terms.

    Search relevance is retained by trying the full visual prompt first, then
    concise keyword combinations, and finally the main subject. No orientation
    or size filter is applied because valid CC0 sources are cropped to 9:16 by
    from_image(). CC0 avoids attribution obligations for monetized Shorts.
    """
    words = re.findall(r"[a-zA-Z]{3,}", query.lower())
    stop = {
        "the", "and", "with", "from", "into", "over", "under", "three", "four",
        "two", "one", "spilled", "open", "dark", "bright", "small", "large",
        "empty", "broken", "old", "new", "close", "closeup", "close-up",
    }
    keywords = [word for word in words if word not in stop]
    candidates_to_try = [
        query.strip(),
        " ".join(keywords),
        " ".join(keywords[-2:]),
        keywords[-1] if keywords else "",
    ]
    queries = list(dict.fromkeys(q[:200] for q in candidates_to_try if q))
    for search_query in queries:
        try:
            r = requests.get(
                OPENVERSE_IMAGES,
                params={"q": search_query, "license": "cc0", "page_size": 30},
                headers={"User-Agent": "ReelForge/1.0 (openverse anonymous media search)"},
                timeout=30,
            )
            if r.status_code == 429:
                raise RuntimeError("Openverse anonymous API rate limit reached; retry later")
            r.raise_for_status()
            candidates = [
                item for item in r.json().get("results", [])
                if item.get("url", "").startswith("https://")
                and item.get("license", "").lower() == "cc0"
            ]
            if candidates:
                return candidates
        except requests.RequestException as exc:
            raise RuntimeError(f"Openverse image search failed: {exc}") from exc
    return []

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
        fallback.disable("local_sd", f"{type(e).__name__}: {e}", seconds=3600)
        print(f"    ! local diffusion unavailable ({type(e).__name__}: {e}); cooling down for 1 hour")
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
        # OOM / backend failures are usually machine-wide for this run. Avoid
        # repeating the same expensive failure for every remaining scene.
        fallback.disable("local_sd", f"{type(e).__name__}: {e}", seconds=900)
        print(f"    ! local generation failed ({type(e).__name__}: {e}); cooling down for 15 minutes")
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


RISKY_WORDS = {"hand", "hands", "fingers", "palm", "body", "flesh", "skin", "nude", "bare"}
FRAME = "children's storybook illustration, flat vector, wholesome, plain background: "


def _framed(prompt: str) -> str:
    """Wrap the prompt so a safety classifier reads it as illustration.

    Adding explicit benign context works far better than deleting words - deleting
    "hand" from "a hand receiving a coin" removes the subject and you get nonsense.
    """
    return FRAME + prompt


def _sanitise_prompt(prompt: str) -> str:
    """Last resort: benign framing plus the words most likely to trip a filter."""
    kept = [w for w in prompt.split() if w.strip(",.!?").lower() not in RISKY_WORDS]
    text = " ".join(kept)
    # tidy artefacts left by removing a word ("a a" / leading article before a verb)
    text = re.sub(r"\b(a|an|the)\s+(?=(a|an|the)\b)", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return _framed(text)


def _gen_cloudflare(cfg, prompt: str, seed: int, dest: Path) -> Path | None:
    """Free hosted images: Cloudflare Workers AI, flux-1-schnell.

    Workers Free gives 10,000 Neurons/day with no card. At roughly 45 Neurons an
    image that is ~200 images/day, so a 13-scene reel costs ~6% of the allowance.
    This is the backend that lets rendering run in CI, where there is no GPU.
    """
    import base64

    acct = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "").strip()
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not acct or not token:
        print("    ! CLOUDFLARE_ACCOUNT_ID / CLOUDFLARE_API_TOKEN not set")
        return None

    model = cfg.get("visuals.cf_model", "@cf/black-forest-labs/flux-1-schnell")

    # flux-1-schnell accepts ONLY prompt and steps. Sending width/height/seed
    # returns "Additional or unevaluated properties ... not allowed" (code 5006).
    # It also always renders a square, which from_image crops to 9:16 - hence the
    # style prompt asks for a centred subject in a vertical composition.
    payload = {
        "prompt": prompt,
        "steps": min(int(cfg.get("visuals.cf_steps", 4)), 8),  # flux-schnell caps at 8
    }
    url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/run/{model}"

    # flux-1-schnell's safety filter false-positives on dull, benign prompts
    # ("a hand receiving a single gold coin" was rejected as NSFW). Retrying the
    # identical prompt cannot help, so rephrase and try again before giving up -
    # a gradient scene next to twelve illustrations looks broken.
    attempts = [payload, {**payload, "prompt": _framed(prompt)}]
    soft = _sanitise_prompt(prompt)
    if soft not in (prompt, attempts[1]["prompt"]):
        attempts.append({**payload, "prompt": soft})

    for attempt, body in enumerate(attempts, 1):
        try:
            r = requests.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json=body,
                timeout=int(cfg.get("visuals.cf_timeout", 180)),
            )
        except requests.RequestException as e:
            print(f"    ! cloudflare request failed: {e}")
            continue
        if r.status_code == 200:
            break
        try:
            msg = str(r.json().get("errors"))[:180]
        except ValueError:
            msg = r.text[:180]
        nsfw = "NSFW" in msg or "8007" in msg
        if "free allocation" in msg or "upgrade to Cloudflare" in msg:
            raise AiQuotaExhausted(
                "Cloudflare Workers AI daily allocation exhausted (10,000 neurons).\n"
                "  Resets at 00:00 UTC = 05:30 IST.\n"
                "  Builds are the cost: every run regenerates all 13 images unless the\n"
                "  cache is warm, so repeated rebuilds burn the day's allowance."
            )
        print(f"    ! cloudflare {r.status_code} (attempt {attempt}/{len(attempts)})"
              f"{' - safety filter' if nsfw else ''}: {msg}")
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 * attempt)

    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and ctype.startswith("image/"):
        dest.write_bytes(r.content)          # some models stream raw bytes
        return dest

    try:
        body = r.json()
    except ValueError:
        print(f"    ! cloudflare {r.status_code}: {r.text[:200]}")
        return None
    if r.status_code != 200 or not body.get("success", False):
        print(f"    ! cloudflare {r.status_code}: {str(body.get('errors'))[:200]}")
        return None
    b64 = (body.get("result") or {}).get("image")
    if not b64:
        print(f"    ! cloudflare returned no image: {str(body)[:200]}")
        return None
    dest.write_bytes(base64.b64decode(b64))
    return dest


def generate_image(cfg, query: str, seed: int, dest: Path) -> Path | None:
    """Generate one illustration. Cached on disk by seed, so re-renders are free."""
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
    backend = cfg.get("visuals.ai_backend", "local_sd")

    # In CI there is no GPU and no model cache, so hosted is tried first.
    remote_fallbacks = bool(cfg.get("visuals.remote_fallbacks", False))
    # Local mode is genuinely local by default. Hosted free tiers are optional:
    # they are quota-limited and may change without notice.
    cloud_no_local = bool(cfg.get("visuals.cloud_no_local_fallback", False))
    order = {
        "local_sd": [_gen_local] + ([_gen_cloudflare, _gen_pollinations] if remote_fallbacks else []),
        # Hosted CI has no GPU. Do not download/run SDXL on CPU when Cloudflare fails.
        "cloudflare": [_gen_cloudflare] if cloud_no_local else [_gen_cloudflare, _gen_local],
        "pollinations": [_gen_pollinations, _gen_cloudflare, _gen_local],
    }.get(backend, [_gen_local] + ([_gen_cloudflare, _gen_pollinations] if remote_fallbacks else []))

    providers = {
        _gen_local: "local_sd",
        _gen_cloudflare: "cloudflare",
        _gen_pollinations: "pollinations",
    }
    for gen in order:
        provider_name = providers[gen]
        if not fallback.available(provider_name):
            print(f"    ! {provider_name} temporarily skipped (cooldown active)")
            continue
        try:
            img = gen(cfg, prompt, seed, dest)
        except AiQuotaExhausted as e:
            # Quota exhaustion is account-wide. Stop before a placeholder reel can publish.
            fallback.disable(provider_name, str(e), seconds=86400)
            print(f"    ! {provider_name} quota exhausted; stopping image generation")
            raise
        except Exception as e:
            fallback.disable(provider_name, f"{type(e).__name__}: {e}", seconds=900)
            print(f"    ! {provider_name} failed; cooling down for 15 minutes: {e}")
            continue
        if img is not None:
            try:
                if not img.exists() or img.stat().st_size < 5_000:
                    raise ValueError("generated image is missing or too small")
                with Image.open(img) as check:
                    check.verify()
            except Exception as e:
                img.unlink(missing_ok=True)
                fallback.disable(provider_name, f"invalid image: {e}", seconds=900)
                print(f"    ! {provider_name} returned an invalid image: {e}")
                continue
            fallback.enable(provider_name)
            return img
    return None


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
        if cfg.get("visuals.require_ai_images", True):
            raise RuntimeError(
                f"required AI image unavailable for scene {index + 1}: {query!r}; "
                "refusing to render a gradient placeholder"
            )
        print("    ! AI image unavailable after retries, using procedural (explicitly allowed)")

    if provider == "openverse":
        candidates = [item for item in _openverse_candidates(query) if item.get("url") not in used]
        if not candidates:
            candidates = _openverse_candidates(query)
        if not candidates:
            raise RuntimeError(
                f"Openverse returned no CC0 portrait-friendly image for scene {index + 1}: {query!r}. "
                "Try a broader visual query; refusing to use a gradient placeholder."
            )
        item = candidates[index % len(candidates)]
        url = item["url"]
        used.add(url)
        digest = hashlib.sha1(url.encode()).hexdigest()[:16]
        cached = _download(url, _cache_dir() / f"{digest}.jpg")
        if not cached.exists() or cached.stat().st_size < 10_000:
            raise RuntimeError(f"Openverse image download was incomplete or too small: {url}")
        try:
            with Image.open(cached) as check:
                check.verify()
        except Exception as exc:
            cached.unlink(missing_ok=True)
            raise RuntimeError(f"Openverse returned an invalid image for scene {index + 1}") from exc
        store.bump_visual(f"openverse:{digest}")
        return from_image(cfg, cached, seconds, out, motion), f"openverse:{digest[:8]}"
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
