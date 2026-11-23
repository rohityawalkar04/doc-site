"""Live gate: call every model and every resource once, before a build.

`reelforge doctor` answers "is it installed": binaries on PATH, imports resolve,
env vars set. This answers "does it answer": one real request per dependency, so
a run cannot reach scene 9 of 13 and only then find out that the image model is
out of allocation or that the voice endpoint is throttling this IP.

Cost of a full pass: one cheap illustration (about 45 of Cloudflare's 10,000
daily neurons), one short spoken phrase, and about ten seconds. It runs before
the first scene, so the cost of skipping it - a forty-minute wasted render and,
if the failure is a quota, the rest of the day's allowance - is far higher.

Publish credentials are deliberately not here: `cloud_run.preflight()` already
checks the YouTube token, and it runs before this gate. This file is about the
build.

    PYTHONPATH=src python -m reelforge.health
    PYTHONPATH=src python -m reelforge.health --skip-images
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from . import captions_png, config, ledger, render, topics, visuals, voice


@dataclass
class Check:
    group: str
    label: str
    ok: bool
    detail: str
    seconds: float = 0.0


def _timed(group: str, label: str, fn) -> Check:
    t = time.time()
    try:
        ok, detail = fn()
    except Exception as e:  # noqa: BLE001 - the exception is the finding
        ok, detail = False, f"{type(e).__name__}: {str(e)[:160]}"
    return Check(group, label, ok, detail, round(time.time() - t, 1))


# --------------------------------------------------------------------------- resources
def _ffmpeg_check(tmp: Path):
    def run():
        enc = tmp / "probe.mp4"
        res = subprocess.run(
            ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-f", "lavfi", "-i", "testsrc=s=256x448:d=0.2",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", str(enc)],
            capture_output=True, text=True,
        )
        if res.returncode != 0:
            return False, (res.stderr.strip()[-200:] or "ffmpeg exited non-zero")
        probed = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(enc)],
            capture_output=True, text=True,
        )
        if probed.returncode != 0:
            return False, f"ffprobe failed: {probed.stderr.strip()[-160:]}"
        return True, f"encoded and probed {probed.stdout.strip()[:5]}s, {enc.stat().st_size // 1024} KB"
    return run


def _captions_check(cfg):
    def run():
        path = captions_png.font_path(cfg)
        mode = cfg.get("captions.mode", "auto")
        if mode == "ass" or (mode == "auto" and render.has_filter("ass")):
            return True, f"{path.name}, mode=ass"
        return True, f"{path.name}, mode=png"
    return run


def _music_check(cfg, tmp: Path):
    def run():
        track = render.find_music(cfg)
        if track is not None:
            return True, track.name
        bed = render.ensure_music_bed(tmp)
        if bed is None:
            return False, "no track and the synthesised bed failed"
        return True, f"none in assets/music - synthesised a bed ({bed.stat().st_size // 1024} KB)"
    return run


def _disk_check(cfg):
    def run():
        wrote = []
        for label, d in (("work", cfg.work), ("out", cfg.out)):
            d.mkdir(parents=True, exist_ok=True)
            probe = d / ".health-write"
            probe.write_bytes(b"x")
            probe.unlink()
            wrote.append(label)
        return True, "writable: " + ", ".join(wrote)
    return run


def _script_check(cfg):
    def run():
        if str(cfg.get("script.mode", "auto")).lower() == "beats":
            return True, "beats - no model needed"
        host = (os.environ.get("OLLAMA_HOST") or "http://127.0.0.1:11434").rstrip("/")
        model = cfg.get("llm.model", "llama3.2:latest")
        with urllib.request.urlopen(host + "/api/tags", timeout=5) as r:
            tags = json.load(r)
        names = {m.get("name") for m in tags.get("models", [])}
        if model in names:
            return True, f"{model} at {host}"
        return False, f"{model} is not pulled at {host} (have: {', '.join(sorted(n for n in names if n))[:80]})"
    return run


def _image_checks(cfg, tmp: Path) -> list[Check]:
    """Every backend a build would try, stopping at the first that answers."""
    out: list[Check] = []
    for name, ok, detail in visuals.probe(cfg, tmp):
        out.append(Check("images", name, ok, detail))
        if ok:
            break
    if not out:
        out.append(Check("images", "backend", False, "no backend configured"))
    return out


def _voice_check(cfg, tmp: Path) -> Check:
    engine, ok, detail = voice.probe(cfg, tmp)
    return Check("voice", engine, ok, detail)


def _topics_check(cfg):
    """The subject matter is a resource too - but only when it is the one in use."""
    def run():
        done = set(ledger.load())
        left = [t for t in config.load_topics() if str(t["id"]) not in done]
        if left:
            return True, f"{len(left)} written topic(s) queued - the bank is not needed"
        problems = topics.check_bank(cfg)
        if problems:
            return False, f"{len(problems)} problem(s) in {topics.BANK}: {problems[0]}"
        return True, "queue empty, bank clean - a topic will be composed at runtime"
    return run


# --------------------------------------------------------------------------- entry points
def check(cfg, *, images: bool = True, voice_check: bool = True,
          tmp: Path | None = None) -> list[Check]:
    """Run every probe. Returns the findings; printing is `report`'s job."""
    created = tmp is None
    work = Path(tmp) if tmp is not None else Path(tempfile.mkdtemp(prefix="rf-health-"))
    work.mkdir(parents=True, exist_ok=True)

    try:
        checks: list[Check] = [
            _timed("environment", "ffmpeg + ffprobe", _ffmpeg_check(work)),
            _timed("disk", "work + out", _disk_check(cfg)),
            _timed("script", "language model", _script_check(cfg)),
            _timed("topics", "topic source", _topics_check(cfg)),
        ]
        if cfg.get("captions.enabled", True):
            checks.append(_timed("captions", "font", _captions_check(cfg)))
        checks.append(_timed("audio", "music bed", _music_check(cfg, work)))

        if images:
            if cfg.get("visuals.provider", "procedural") in ("ai_cartoon", "ai"):
                checks.extend(_image_checks(cfg, work))
            else:
                checks.append(Check("images", cfg.get("visuals.provider", "procedural"),
                                    True, "no model - explicit provider"))

        if voice_check:
            checks.append(_voice_check(cfg, work))
        return checks
    finally:
        if created:
            import shutil
            shutil.rmtree(work, ignore_errors=True)


def report(checks: list[Check], *, stream=None) -> bool:
    """Print the table, return True when every check passed."""
    out = stream or sys.stdout
    group = ""
    for c in checks:
        if c.group != group:
            group = c.group
            out.write(f"\n{group}\n")
        out.write(f"  {'OK  ' if c.ok else 'FAIL'} {c.label:<24} {c.detail}"
                  f"{f'  ({c.seconds:.1f}s)' if c.seconds else ''}\n")
    failed = [c for c in checks if not c.ok]
    out.write(
        f"\n{'health: every model and resource answered' if not failed else 'health: '
           + str(len(failed)) + ' check(s) failed - build refused'}\n"
    )
    out.flush()
    return not failed


def as_json(checks: list[Check]) -> str:
    """One serialisation, shared by the module entry point and the CLI."""
    return json.dumps([asdict(c) for c in checks], indent=2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="reelforge health", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-images", action="store_true",
                    help="do not call the image model (saves the tokens, checks nothing)")
    ap.add_argument("--skip-voice", action="store_true", help="do not call the TTS engine")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    cfg = config.load()
    os.environ.setdefault("REELFORGE_ROOT", str(config.ROOT))
    checks = check(cfg, images=not args.skip_images, voice_check=not args.skip_voice)
    if args.json:
        print(as_json(checks))
        return 0 if all(c.ok for c in checks) else 1
    return 0 if report(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
