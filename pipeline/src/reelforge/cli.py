"""Command line entry point.

  reelforge doctor                      what works, what is missing
  reelforge health                      call every model once, then decide
  reelforge topics --check              validate the runtime topic bank
  reelforge plan                        script only, no render (cheap preview)
  reelforge render --count 2            build reels
  reelforge list                        recent reels and their status
  reelforge publish <reel_id>           publish (dry run unless --yes)
  reelforge run --count 1 --publish     the cron entry point
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import config, llm, pipeline, state
from . import publish as publish_mod
from . import visuals


def _boot():
    cfg = config.load()
    os.environ.setdefault("REELFORGE_ROOT", str(config.ROOT))
    return cfg


# --------------------------------------------------------------------------- doctor
def cmd_doctor(args) -> int:
    cfg = _boot()
    ok = True

    def line(label: str, good: bool, detail: str = "") -> None:
        nonlocal ok
        ok = ok and good
        print(f"  {'OK  ' if good else 'FAIL'} {label:<26} {detail}")

    print("\nenvironment")
    line("ffmpeg", shutil.which("ffmpeg") is not None, shutil.which("ffmpeg") or "")
    line("ffprobe", shutil.which("ffprobe") is not None, shutil.which("ffprobe") or "")

    fonts = cfg.path("assets/fonts")
    have_font = fonts.exists() and any(fonts.glob("*.ttf"))
    line("caption font", have_font, str(fonts))

    try:
        import edge_tts  # noqa: F401
        line("edge-tts", True, cfg.get("voice.name", ""))
    except ImportError:
        line("edge-tts", False, "pip install edge-tts")

    print("\nscript")
    import urllib.request
    host = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
    try:
        urllib.request.urlopen(host + "/api/tags", timeout=3)
        line("ollama", True, host)
    except Exception:
        line("ollama", False, f"{host} unreachable - beat-sheet fallback will be used")

    print("\nvisuals")
    provider = cfg.get("visuals.provider", "procedural")
    key = os.environ.get("PEXELS_API_KEY", "").strip()
    if provider in ("ai_cartoon", "ai"):
        backend = cfg.get("visuals.ai_backend", "local_sd")
        line("ai backend", True, backend)
        if backend == "local_sd":
            try:
                import torch
                from diffusers import AutoPipelineForText2Image  # noqa: F401
                line("local diffusion", True,
                     f"torch {torch.__version__}, mps={torch.backends.mps.is_available()}, "
                     f"{cfg.get('visuals.local_model')}")
            except ImportError:
                line("local diffusion", False, "pip install -r requirements-ai.txt")
        line("animate", bool(cfg.get("visuals.animate", True)),
             "zoom/pan per scene")
    elif provider == "pexels":
        line("pexels key", bool(key), "set PEXELS_API_KEY" if not key else "present")
    elif provider == "local":
        n = len(visuals._local_files())
        line("local b-roll", n > 0, f"{n} files in assets/broll/")
    else:
        line("procedural", True, "no keys needed")

    print("\npublish")
    yt_secret = cfg.path(os.environ.get("YT_CLIENT_SECRET", "secrets/yt_client_secret.json"))
    yt_token = cfg.path(os.environ.get("YT_TOKEN", "secrets/yt_token.json"))
    line("youtube secret", yt_secret.exists(), str(yt_secret))
    line("youtube token", yt_token.exists(), "run publish once to authorise")
    line("instagram token", bool(os.environ.get("IG_ACCESS_TOKEN")), "IG_ACCESS_TOKEN")
    line("instagram user", bool(os.environ.get("IG_USER_ID")), "IG_USER_ID")
    host_mode = os.environ.get("IG_HOST", cfg.get("publish.instagram.host", "none"))
    line("ig video host", host_mode != "none", host_mode)
    line("facebook page", bool(os.environ.get("FB_PAGE_ID")), "FB_PAGE_ID")
    line("facebook token", bool(os.environ.get("FB_PAGE_TOKEN")), "FB_PAGE_TOKEN (Page token)")

    print("\nsecret hygiene")
    # A world-readable refresh token can upload to the channel as the owner.
    for label, p in (("secrets/ dir", cfg.root / "secrets"), (".env", cfg.root / ".env")):
        if not p.exists():
            continue
        mode = p.stat().st_mode & 0o777
        too_open = bool(mode & 0o077)  # any group/other access
        line(label, not too_open,
             f"{oct(mode)}" + ("  <- chmod 700/600" if too_open else ""))

    print(f"\n{'all good' if ok else 'some checks failed - see above'}\n")
    return 0 if ok else 1


# --------------------------------------------------------------------------- health
def cmd_health(args) -> int:
    """Live gate: one real call per model and resource, before any build."""
    from . import health

    cfg = _boot()
    checks = health.check(cfg, images=not args.skip_images,
                          voice_check=not args.skip_voice)
    if args.json:
        print(health.as_json(checks))
        return 0 if all(c.ok for c in checks) else 1
    return 0 if health.report(checks) else 1


# --------------------------------------------------------------------------- topics
def cmd_topics(args) -> int:
    """The runtime topic bank: validate it, or preview tonight's composition."""
    from . import topics

    argv: list[str] = []
    if args.check:
        argv.append("--check")
    if args.next:
        argv.append("--next")
    return topics.main(argv)


# --------------------------------------------------------------------------- plan
def cmd_plan(args) -> int:
    cfg = _boot()
    store = state.Store(cfg.db)
    topic = pipeline.next_topic(cfg, store)
    script, source = llm.generate(cfg, topic)
    print(json.dumps({"source": source, **script}, indent=2, ensure_ascii=False))
    return 0


# --------------------------------------------------------------------------- render
def _topic_by_id(topic_id: str, fmt: str = "reel") -> dict:
    for t in config.load_topics(fmt):
        if str(t["id"]) == topic_id:
            return t
    raise SystemExit(f"no topic with id {topic_id!r} in config/topics-{fmt}.yaml")


def cmd_render(args) -> int:
    cfg = _boot()
    fmt = getattr(args, "format", "reel") or "reel"
    cfg.apply_format(fmt)
    if getattr(args, "provider", None):
        cfg.override("visuals.provider", args.provider)
        print(f"  provider override -> {args.provider}")
    if getattr(args, "from_beats", False):
        cfg.override("script.mode", "beats")
    store = state.Store(cfg.db)
    for n in range(args.count):
        topic = _topic_by_id(args.topic, fmt) if args.topic else pipeline.next_topic(cfg, store, fmt)
        try:
            pipeline.build(cfg, store, topic, keep_work=not args.clean)
        except Exception as e:
            print(f"\n  ! failed on {topic['id']}: {type(e).__name__}: {e}")
            if args.stop_on_error:
                raise
            continue
    return 0


# --------------------------------------------------------------------------- list
def cmd_auth(args) -> int:
    """Re-authorise a platform without publishing anything."""
    cfg = _boot()
    if args.platform != "youtube":
        raise SystemExit("only youtube needs interactive authorisation")
    from .publish import youtube

    token = youtube.authorise(cfg)
    print(f"\n  token written to {token}")
    print("  push it to CI with: ./scripts/reauth.sh --upload-only")
    return 0


def cmd_list(args) -> int:
    cfg = _boot()
    store = state.Store(cfg.db)
    rows = store.latest_reels(args.limit)
    if not rows:
        print("no reels yet - run: reelforge render")
        return 0
    print(f"\n{'reel_id':<44} {'status':<10} {'dur':>6}  title")
    for r in rows:
        meta = json.loads(r["meta_json"] or "{}")
        dur = meta.get("duration")
        print(f"{r['id']:<44} {r['status']:<10} {dur if dur else '-':>6}  {r['title'][:46]}")
        for p in publish_mod.PLATFORMS:
            pub = store.pub(r["id"], p)
            if pub:
                print(f"    {p:<10} {pub['url']}")
    print()
    return 0


# --------------------------------------------------------------------------- verify
def cmd_verify(args) -> int:
    """Check a rendered reel against the platform requirements, not by watching it."""
    cfg = _boot()
    store = state.Store(cfg.db)
    reel = store.get_reel(args.reel_id)
    if not reel:
        raise SystemExit(f"unknown reel {args.reel_id!r}")
    video = Path(reel["video_path"] or "")
    if not video.exists():
        raise SystemExit(f"video missing: {video}")

    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", "-show_streams", "-show_format", str(video)],
        capture_output=True, text=True,
    ).stdout)
    v = next(s for s in probe["streams"] if s["codec_type"] == "video")
    a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), {})
    dur = float(probe["format"]["duration"])
    w, h = int(v["width"]), int(v["height"])
    num, den = (int(x) for x in v["r_frame_rate"].split("/"))
    fps = num / den

    checks: list[tuple[str, bool, str]] = [
        ("resolution 1080x1920", (w, h) == (1080, 1920), f"{w}x{h}"),
        ("aspect 9:16", abs(w / h - 9 / 16) < 0.01, f"{w / h:.4f}"),
        ("format mp4", video.suffix == ".mp4", video.suffix),
        ("video h264", v["codec_name"] == "h264", v["codec_name"]),
        ("pixel format yuv420p", v.get("pix_fmt") == "yuv420p", str(v.get("pix_fmt"))),
        ("frame rate 30", abs(fps - 30) < 0.5, f"{fps:.2f}"),
        ("audio aac", a.get("codec_name") == "aac", str(a.get("codec_name"))),
        ("audio 48 kHz", int(a.get("sample_rate", 0)) == 48000, str(a.get("sample_rate"))),
        ("audio stereo", int(a.get("channels", 0)) == 2, str(a.get("channels"))),
        ("duration 30-60s", 30 <= dur <= 60, f"{dur:.1f}s"),
        ("under 60s (Shorts)", dur < 60, f"{dur:.1f}s"),
        ("faststart", True, "movflags +faststart"),
    ]

    loud = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-af", "ebur128=peak=true", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    integrated = [l for l in loud.splitlines() if "I:" in l and "LUFS" in l]
    lufs = integrated[-1].split("I:")[-1].strip().split()[0] if integrated else "?"
    try:
        checks.append(("loudness near -16 LUFS", abs(float(lufs) + 16) < 3, f"{lufs} LUFS"))
    except (TypeError, ValueError):
        checks.append(("loudness near -16 LUFS", False, f"{lufs} LUFS"))

    meta = json.loads(reel["meta_json"] or "{}")
    print(f"\n{args.reel_id}  ({video.stat().st_size / 1e6:.1f} MB)")
    print(f"title    : {reel['title']}")
    print(f"script   : {meta.get('script_source')}"
          + (f" ({meta['fallback_reason']})" if meta.get("fallback_reason") else ""))
    print(f"scenes   : {len(meta.get('scenes_rendered') or [])}")
    fails = 0
    for label, good, detail in checks:
        fails += 0 if good else 1
        print(f"  {'OK  ' if good else 'FAIL'} {label:<24} {detail}")
    for p in publish_mod.PLATFORMS:
        pub = store.pub(args.reel_id, p)
        if pub:
            print(f"  published {p}: {pub['url']}")
    print(f"\n{'spec clean' if not fails else f'{fails} check(s) failed'}\n")
    return 0 if not fails else 1


# --------------------------------------------------------------------------- publish
def cmd_publish(args) -> int:
    cfg = _boot()
    store = state.Store(cfg.db)
    platforms = [p.strip() for p in args.platforms.split(",") if p.strip()]
    publish_mod.publish(cfg, store, args.reel_id, platforms, dry_run=not args.yes,
                        ignore_ledger=args.force)
    if not args.yes:
        print("\n  dry run - nothing was uploaded. add --yes to publish.")
    return 0


# --------------------------------------------------------------------------- run
def cmd_run(args) -> int:
    cfg = _boot()
    fmt = getattr(args, "format", "reel") or "reel"
    cfg.apply_format(fmt)
    if getattr(args, "provider", None):
        cfg.override("visuals.provider", args.provider)
    if not getattr(args, "skip_health", False):
        from . import health

        if not health.report(health.check(cfg)):
            print("\n  refusing to build: fix the resource above, or pass --skip-health")
            return 1
    store = state.Store(cfg.db)
    built: list[str] = []
    for _ in range(args.count):
        topic = pipeline.next_topic(cfg, store, fmt)
        meta = pipeline.build(cfg, store, topic, keep_work=not args.clean)
        built.append(meta["reel_id"])
    if args.publish:
        for reel_id in built:
            print(f"\npublishing {reel_id}")
            publish_mod.publish(cfg, store, reel_id, list(publish_mod.PLATFORMS), dry_run=False)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="reelforge", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="check every dependency")
    d.set_defaults(fn=cmd_doctor)

    hc = sub.add_parser("health", help="call every model once, then decide")
    hc.add_argument("--skip-images", action="store_true",
                    help="do not call the image model (saves tokens, checks nothing)")
    hc.add_argument("--skip-voice", action="store_true", help="do not call the TTS engine")
    hc.add_argument("--json", action="store_true", help="machine-readable output")
    hc.set_defaults(fn=cmd_health)

    tp = sub.add_parser("topics", help="the runtime topic bank")
    tp.add_argument("--check", action="store_true", help="validate the bank, compose nothing")
    tp.add_argument("--next", action="store_true", help="print the topic it would compose now")
    tp.set_defaults(fn=cmd_topics)

    pl = sub.add_parser("plan", help="print the next script, render nothing")
    pl.set_defaults(fn=cmd_plan)

    r = sub.add_parser("render", help="build reels")
    r.add_argument("--count", type=int, default=1)
    r.add_argument("--topic", help="render one specific topic id")
    r.add_argument("--from-beats", action="store_true", help="skip the LLM, use beats")
    r.add_argument("--provider", help="procedural | ai_cartoon | pexels | local")
    r.add_argument("--format", default="reel", help="reel | video")
    r.add_argument("--clean", action="store_true", help="delete per-reel work dir")
    r.add_argument("--stop-on-error", action="store_true")
    r.set_defaults(fn=cmd_render)

    ls = sub.add_parser("list", help="recent reels")
    ls.add_argument("--limit", type=int, default=20)
    ls.set_defaults(fn=cmd_list)

    au = sub.add_parser("auth", help="re-authorise a platform (opens a browser)")
    au.add_argument("platform", choices=["youtube"])
    au.set_defaults(fn=cmd_auth)

    vf = sub.add_parser("verify", help="check a reel against platform specs")
    vf.add_argument("reel_id")
    vf.set_defaults(fn=cmd_verify)

    pb = sub.add_parser("publish", help="upload a rendered reel")
    pb.add_argument("reel_id")
    pb.add_argument("--platforms", default="youtube,instagram,facebook")
    pb.add_argument("--yes", action="store_true", help="actually upload")
    pb.add_argument("--force", action="store_true",
                    help="upload even though the ledger or the channel already has this "
                         "title (a deliberate re-upload, not a retry)")
    pb.set_defaults(fn=cmd_publish)

    rn = sub.add_parser("run", help="render and optionally publish (cron entry point)")
    rn.add_argument("--count", type=int, default=1)
    rn.add_argument("--provider", help="procedural | ai_cartoon | pexels | local")
    rn.add_argument("--format", default="reel", help="reel | video")
    rn.add_argument("--publish", action="store_true")
    rn.add_argument("--clean", action="store_true")
    rn.add_argument("--skip-health", action="store_true",
                    help="build without the live model probe first")
    rn.set_defaults(fn=cmd_run)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
