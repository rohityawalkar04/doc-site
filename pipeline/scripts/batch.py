"""Batch renderer: pre-generate every AI image once, then render in parallel.

Why this exists
---------------
Loading a 7 GB diffusion model per reel, or letting three render processes each
load one, either wastes minutes or exhausts 24 GB of unified memory. So:

  Phase A  one process, one loaded pipeline, every image for every reel
  Phase B  N render processes in parallel; every image is a cache hit, so the
           GPU is never needed and there is no memory contention

TTS and x264 are the slow parts of phase B and both parallelise well: one is
waiting on a network, the other is CPU-bound.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import os  # noqa: E402

os.environ.setdefault("REELFORGE_ROOT", str(ROOT))  # so image cache paths match the render subprocess

from reelforge import config, llm, visuals  # noqa: E402


def plan(cfg, topic: dict) -> dict:
    """Resolve the script without touching the GPU or the voice."""
    script, source = llm.generate(cfg, topic)
    return {"topic": topic, "script": script, "source": source}


def phases(plans, cfg, jobs: int) -> int:
    t_all = time.time()

    # ---------------- phase A: images ----------------
    print(f"\n=== phase A: generating images for {len(plans)} reels ===", flush=True)
    t0 = time.time()
    cache = visuals._ai_cache()
    made = failed = hit = 0
    for p in plans:
        for i, scene in enumerate(p["script"]["scenes"]):
            query = scene["visual_query"]
            seed = int(hashlib.sha1(f"{query}|{i}".encode()).hexdigest()[:8], 16)
            dest = cache / f"{seed:08x}.jpg"
            existed = dest.exists() and dest.stat().st_size > 5000
            t = time.time()
            img = visuals.generate_image(cfg, query, seed, dest)
            if existed:
                hit += 1
            elif img is not None:
                made += 1
                print(f"  {p['topic']['id'][:18]:18} {i+1:02d} {time.time()-t:4.1f}s {query[:44]}", flush=True)
            else:
                failed += 1
                print(f"  {p['topic']['id'][:18]:18} {i+1:02d} FAILED {query[:40]}", flush=True)
    print(f"  images: {made} new, {hit} cached, {failed} failed in {time.time()-t0:.0f}s", flush=True)

    # ---------------- phase B: render ----------------
    print(f"\n=== phase B: rendering {len(plans)} reels, {jobs} at a time ===", flush=True)
    t1 = time.time()

    def run(p):
        tid = p["topic"]["id"]
        t = time.time()
        res = subprocess.run(
            [str(ROOT / "reelforge"), "render", "--topic", tid, "--from-beats"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        tail = [l for l in res.stdout.splitlines() if l.strip().startswith("->")]
        ok = res.returncode == 0 and bool(tail)
        print(f"  {'OK  ' if ok else 'FAIL'} {tid[:20]:20} {time.time()-t:5.0f}s {tail[-1] if tail else ''}", flush=True)
        if not ok:
            print(res.stdout[-600:] + res.stderr[-600:], flush=True)
        return ok

    with ThreadPoolExecutor(max_workers=jobs) as ex:
        results = list(ex.map(run, plans))

    print(f"\n=== done: {sum(results)}/{len(results)} reels in {time.time()-t_all:.0f}s total ===")
    return 0 if all(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("topics", nargs="+", help="topic ids from config/topics.yaml")
    ap.add_argument("--jobs", type=int, default=3)
    args = ap.parse_args()

    cfg = config.load()
    cfg.override("script.mode", "beats")     # deterministic scripts, exact scene counts
    cfg.override("visuals.provider", "ai_cartoon")

    by_id = {str(t["id"]): t for t in config.load_topics()}
    missing = [t for t in args.topics if t not in by_id]
    if missing:
        raise SystemExit(f"unknown topic ids: {missing}")

    plans = [plan(cfg, by_id[t]) for t in args.topics]
    for p in plans:
        print(f"  {p['topic']['id']:20} {len(p['script']['scenes'])} scenes  ~{llm.estimated_seconds(p['script'], cfg):.0f}s  [{p['source']}]")
    return phases(plans, cfg, args.jobs)


if __name__ == "__main__":
    sys.exit(main())
