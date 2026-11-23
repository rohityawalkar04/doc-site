"""One-off smoke test for the local diffusion backend. Prints timings."""
import time
from pathlib import Path

from reelforge import config, visuals

c = config.load()
import torch
print("torch", torch.__version__, "| mps available:", torch.backends.mps.is_available(), flush=True)

d = Path("/tmp/rf_anim/local"); d.mkdir(parents=True, exist_ok=True)
queries = [
    "an inbox overflowing with paper letters",
    "a calendar page with one day circled in red",
    "two hands passing a small folded note",
]
for i, q in enumerate(queries):
    seed = visuals.image_seed("local-sd-smoke", q, i)
    dest = d / f"{seed:08x}.jpg"
    t = time.time()
    img = visuals.generate_image(c, q, seed, dest)
    dt = time.time() - t
    if img:
        from PIL import Image
        print(f"  {i+1} {dt:5.1f}s OK {Image.open(img).size} {img.stat().st_size//1024}KB  {q}", flush=True)
    else:
        print(f"  {i+1} {dt:5.1f}s FAILED  {q}", flush=True)
