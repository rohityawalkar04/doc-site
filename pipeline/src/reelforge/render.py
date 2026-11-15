"""Assembly. Each scene becomes its own audio+video part, the parts concat, then
one final pass burns captions and ducks music under the voice.

Splitting per scene costs one extra encode but makes a broken reel a visible
artifact (one bad scene) instead of an opaque 300-line filtergraph failure.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def _run(cmd: list[str], what: str) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{what} failed\n  err: {res.stderr.strip()[-1500:]}")


def probe_duration(path: str | Path) -> float:
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {res.stderr.strip()}")
    return float(res.stdout.strip())


def scene_audio(src: str | Path, seconds: float, out: Path, rate: int = 48000) -> Path:
    """Pad (never trim) to the scene length so word offsets stay valid."""
    _run([
        "ffmpeg", "-y", "-i", str(src),
        "-af", f"apad=whole_dur={seconds:.3f},aresample={rate}",
        "-t", f"{seconds:.3f}", "-ac", "2", "-ar", str(rate),
        "-c:a", "aac", "-b:a", "192k", str(out),
    ], "scene audio")
    return out


def concat(parts: list[Path], out: Path, listfile: Path, with_audio: bool) -> Path:
    listfile.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    _run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
        "-c", "copy", str(out),
    ], f"concat ({'av' if with_audio else 'video only'})")
    return out


def ensure_music_bed(work: Path, seconds: float = 30.0) -> Path | None:
    """Synthesise a quiet pad once, so previews are not silent.

    Replace with a real track: drop any .mp3/.wav/.m4a into assets/music/.
    Use only music you have the right to publish (YouTube Audio Library, etc.).
    """
    bed = work / "music_bed.wav"
    if bed.exists():
        return bed
    expr = (
        "0.14*sin(2*PI*110*t)*(0.55+0.45*sin(2*PI*0.05*t))"
        "+0.10*sin(2*PI*164.81*t)*(0.5+0.5*sin(2*PI*0.037*t+1.1))"
        "+0.07*sin(2*PI*220*t)*(0.5+0.5*sin(2*PI*0.061*t+2.3))"
        "+0.05*sin(2*PI*329.63*t)*(0.5+0.5*sin(2*PI*0.043*t))"
    )
    _run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"aevalsrc={expr}:s=48000:d={seconds:.1f}",
        "-af", "lowpass=f=900,tremolo=f=0.15:d=0.5,afade=t=in:d=1.5,"
               f"afade=t=out:st={seconds - 2:.1f}:d=2",
        "-ac", "2", str(bed),
    ], "music bed")
    return bed


def find_music(cfg) -> Path | None:
    d = cfg.path(cfg.get("audio.music_dir", "assets/music"))
    if not d.exists():
        return None
    tracks = sorted(p for p in d.iterdir() if p.suffix.lower() in {".mp3", ".wav", ".m4a", ".aac"})
    return tracks[0] if tracks else None


_FILTERS: str | None = None


def has_filter(name: str) -> bool:
    """Detect optional filters (ass/drawtext) instead of assuming a full build."""
    global _FILTERS
    if _FILTERS is None:
        res = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True)
        _FILTERS = res.stdout
    return f" {name} " in _FILTERS


def final(video: Path, voice: Path, captions, music: Path | None,
          out: Path, cfg, total_seconds: float) -> Path:
    """captions: list[{png,start,end}] | Path (.ass) | None."""
    w = int(cfg.get("visuals.width", 1080))
    h = int(cfg.get("visuals.height", 1920))
    fps = int(cfg.get("visuals.fps", 30))
    sr = int(cfg.get("audio.sample_rate", 48000))
    vol = float(cfg.get("audio.music_volume", 0.30))
    duck = bool(cfg.get("audio.duck", True))

    pngs = captions if isinstance(captions, list) else None
    ass = captions if isinstance(captions, Path) else None

    cmd = ["ffmpeg", "-y", "-i", str(video), "-i", str(voice)]
    nxt = 2
    music_idx = None
    if music:
        cmd += ["-stream_loop", "-1", "-i", str(music)]
        music_idx = nxt
        nxt += 1
    for item in pngs or []:
        cmd += ["-loop", "1", "-framerate", str(fps), "-i", item["png"]]
        item["_idx"] = nxt
        nxt += 1

    vchain = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1"

    if ass is not None:
        fonts_dir = cfg.path("assets/fonts")
        esc = str(ass).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
        prefix = ""
        if fonts_dir.exists():
            fesc = str(fonts_dir).replace("\\", "/").replace(":", r"\:").replace("'", r"\'")
            prefix = f"fontsdir='{fesc}':"
        vchain += f",ass={prefix}filename='{esc}'"

    if pngs:
        slide_px = int(cfg.get("captions.slide_px", 22))
        slide_s = float(cfg.get("captions.slide_s", 0.18))
        parts = [vchain + "[c0]"]
        for i, item in enumerate(pngs):
            label = f"[c{i}]"
            nxt_label = f"[c{i + 1}]"
            if slide_px and slide_s > 0:
                # Each card rises into place over its first ~0.18 s. Static cards
                # cut in hard and make the reel feel like a slideshow.
                s0 = item["start"]
                y = f"'({slide_px}*max(0,1-(t-{s0:.3f})/{slide_s:.2f}))'"
                pos = f"x=0:y={y}"
            else:
                pos = "0:0"
            parts.append(
                f"{label}[{item['_idx']}:v]overlay={pos}:"
                f"enable='between(t,{item['start']:.3f},{item['end']:.3f})'{nxt_label}"
            )
        vchain = ";".join(parts)
        vchain += f";[c{len(pngs)}]format=yuv420p[v]"
    else:
        vchain += ",format=yuv420p[v]"

    if music_idx is not None:
        if duck:
            achain = (
                f"[{music_idx}:a]volume={vol},atrim=0:{total_seconds:.3f}[m];"
                f"[m][1:a]sidechaincompress=threshold=0.03:ratio=12:attack=20:release=400[d];"
                f"[1:a][d]amix=inputs=2:duration=first:normalize=0[a0]"
            )
        else:
            achain = (
                f"[{music_idx}:a]volume={vol},atrim=0:{total_seconds:.3f}[m];"
                f"[1:a][m]amix=inputs=2:duration=first:normalize=0[a0]"
            )
    else:
        achain = "[1:a]anull[a0]"

    # Single-pass loudnorm to -16 LUFS / -1.5 dBTP: YouTube and Instagram both
    # normalise, and a quiet upload just loses to a loud neighbour.
    lufs = float(cfg.get("audio.target_lufs", -16))
    achain += f";[a0]loudnorm=I={lufs}:TP=-1.5:LRA=11[a]"

    cmd += [
        "-filter_complex", vchain + ";" + achain,
        "-map", "[v]", "-map", "[a]",
        "-t", f"{total_seconds:.3f}",
        "-r", str(fps),
        "-c:v", "libx264", "-preset", str(cfg.get("visuals.final_preset", "veryfast")),
        "-crf", str(cfg.get("visuals.final_crf", 19)),
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        # BT.709 tags must be set through x264: the -color_primaries/-color_trc
        # stream flags are silently dropped when a filtergraph is present, which
        # left the earlier files untagged.
        "-x264-params", "colorprim=bt709:transfer=bt709:colormatrix=bt709",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(sr), "-ac", "2",
        "-movflags", "+faststart",
        str(out),
    ]
    _run(cmd, "final render")
    return out


def ffmpeg_ok() -> bool:
    return shutil.which("ffmpeg") is not None
