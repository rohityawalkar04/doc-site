"""The pipeline proper: topic -> script -> voice -> visuals -> captions -> mp4."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from . import captions, captions_png, config, llm, render, state, visuals, voice


def log(msg: str) -> None:
    """Flushed, stamped output. CI captures stdout, so an unflushed print reads
    as a frozen build - and a long step with no output reads as a hang."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def slug(text: str, limit: int = 52) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if len(s) <= limit:
        return s
    cut = s[:limit]
    # Do not end on a half word: "capture-decisio" reads like a bug.
    return cut.rsplit("-", 1)[0] if "-" in cut else cut


def next_topic(cfg, store: state.Store) -> dict:
    topics = config.load_topics()
    if not topics:
        raise SystemExit("config/topics.yaml has no topics")
    for t in topics:
        if not store.topic_used(str(t["id"])):
            return t
    raise SystemExit("all topics in config/topics.yaml are used - add more")


def build(cfg, store: state.Store, topic: dict, keep_work: bool = True) -> dict:
    reel_id = slug(topic["title"])
    work = cfg.work / reel_id
    for sub in ("audio", "visual", "parts"):
        (work / sub).mkdir(parents=True, exist_ok=True)

    log(f"1/6 script  ({topic['title']})")
    t = time.time()
    script, source = llm.generate(cfg, topic)
    if source == "fallback":
        print(f"      ! ollama unavailable ({script.get('_fallback_reason')}) - using beat sheet")
    log(f"    script done in {time.time() - t:.1f}s via {source}")
    meta = {
        "reel_id": reel_id,
        "topic_id": topic["id"],
        "script_source": source,
        "fallback_reason": script.get("_fallback_reason"),
        "advisory": script.get("_advisory"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        **{k: v for k, v in script.items() if not k.startswith("_")},
    }
    store.add_reel(reel_id, str(topic["id"]), script["title"], meta)
    store.mark_topic(str(topic["id"]), reel_id)
    for i, s in enumerate(script["scenes"], 1):
        print(f"      {i:02d}. {s['narration']}")

    log(f"2/6 voice - {len(script['scenes'])} scenes, engine={cfg.get('voice.engine')}")
    t = time.time()
    scenes = voice.synthesise(cfg, script["scenes"], work / "audio")

    tail = float(cfg.get("audio.tail_pad", 0.30))
    offset = 0.0
    for scene in scenes:
        spoken = voice.duration(scene["audio_path"])
        scene["spoken"] = round(spoken, 3)
        scene["seconds"] = round(spoken + tail, 3)
        scene["offset"] = round(offset, 3)
        offset = round(offset + scene["seconds"], 3)
    total = round(offset, 3)
    log(f"    voice done: {total:.1f}s of audio in {time.time() - t:.1f}s")

    log("3/6 visuals")
    t = time.time()
    audio_parts, video_parts = [], []
    used_visuals: set[str] = set()
    for i, scene in enumerate(scenes, 1):
        tv = time.time()
        log(f"    scene {i}/{len(scenes)}: {scene['visual_query']!r}")
        vid, who = visuals.for_scene(
            cfg, store, i - 1, scene["seconds"], scene["visual_query"],
            work / "visual" / f"s{i:02d}.mp4", used=used_visuals,
        )
        log(f"      -> {who} ({time.time() - tv:.1f}s)")
        scene["visual"] = who
        video_parts.append(vid)
        audio_parts.append(
            render.scene_audio(scene["audio_path"], scene["seconds"], work / "parts" / f"a{i:02d}.m4a")
        )

    log(f"    done in {time.time() - t:.1f}s")

    log("4/6 assemble")
    t = time.time()
    video_track = render.concat(video_parts, work / "video_track.mp4", work / "v.txt", with_audio=False)
    voice_track = render.concat(audio_parts, work / "voice_track.m4a", work / "a.txt", with_audio=True)
    log(f"    done in {time.time() - t:.1f}s")

    log("5/6 captions")
    t = time.time()
    captions_obj = None
    if cfg.get("captions.enabled", True):
        mode = cfg.get("captions.mode", "auto")
        use_ass = mode == "ass" or (mode == "auto" and render.has_filter("ass"))
        if use_ass:
            captions_obj = captions.build(cfg, scenes, work / "captions.ass")
            print("      mode=ass (libass available, per-word karaoke)")
        else:
            captions_obj = captions_png.render(cfg, scenes, work / "caps")
            print(f"      mode=png ({len(captions_obj)} caption cards)")
    log(f"    done in {time.time() - t:.1f}s")

    log("6/6 final render")
    t = time.time()
    music = render.find_music(cfg) or render.ensure_music_bed(work)
    out = cfg.out / f"{reel_id}.mp4"
    out.parent.mkdir(parents=True, exist_ok=True)
    render.final(video_track, voice_track, captions_obj, music, out, cfg, total)

    meta["duration"] = render.probe_duration(out)
    meta["video_path"] = str(out)
    meta["scenes_rendered"] = [
        {"n": i, "narration": s["narration"], "seconds": s["seconds"], "visual": s["visual"]}
        for i, s in enumerate(scenes, 1)
    ]
    (cfg.out / f"{reel_id}.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    store.set_status(reel_id, "rendered", str(out), meta=meta)

    if not keep_work:
        import shutil
        shutil.rmtree(work, ignore_errors=True)
    else:
        print(f"      work kept at {work}")
    log(f"    done in {time.time() - t:.1f}s")
    log(f"    -> {out.name}  ({meta['duration']:.1f}s)")
    return meta
