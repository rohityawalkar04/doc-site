# reelforge

Faceless short-form video factory. Script → voice → visuals → captions → 1080×1920 MP4,
then publish to YouTube Shorts and Instagram Reels.

Everything runs on your machine. The only recurring cost is $0; the only thing you
buy is your own time.

```
config/topics.yaml ──┐
                     ├─ ollama ──> script.json
config/channel.yaml ─┘                 │
                                      ├─ Kokoro → Piper ──> local audio + caption timings
                                      ├─ ffmpeg   ──> 1080x1920 b-roll per scene
                                      └─ Pillow   ──> caption cards
                                                     │
                              ffmpeg concat + overlay + ducked music + loudnorm
                                                     │
                                              out/<reel>.mp4
                                                     │
                              ┌──────────────────────┴───────────────────┐
                        YouTube Data API                       Instagram Graph API
```

## Quick start

```bash
./scripts/setup.sh          # venv + editable install
cp .env.example .env        # fill in later, stage by stage
reelforge doctor            # what works, what is missing
reelforge plan              # print the next script, render nothing
reelforge render --count 1  # build a reel
reelforge list              # recent reels + where they were published
open out/*.mp4
```

## The five stages

| Stage | Primary | Fallback | Execution |
|---|---|---|---|
| Script | Ollama Qwen2.5 7B | Ollama Gemma 3 4B → topic beat sheet | Local |
| Voice | Kokoro | Piper | Local, offline after model download |
| Visuals | Z-Image-Turbo via MFLUX/MLX | required local AI generation; no image search | Apple Silicon Mac |
| Render | FFmpeg + Pillow | — | Local |
| Publish | YouTube / Meta APIs | — | Internet and platform quotas apply |

The local pipeline does not depend on Cloudflare, Pollinations, Edge TTS, or a paid API.
The first model run downloads model weights; generation is then local. Model downloads
need disk space and initial internet access. Local inference is limited by the Mac's
16 GB unified memory and may be slower than hosted services.

## Local-first setup (Apple Silicon M4, 16 GB unified memory)

1. Install the system renderer: `brew install ffmpeg`.
2. Install Ollama, then download the ordered script models:

   ```bash
   ollama pull qwen2.5:7b-instruct
   ollama pull gemma3:4b
   ```

3. Install the base pipeline:

   ```bash
   ./scripts/setup.sh
   ```

4. Install the local MFLUX image generator (Apple Silicon only; downloads the model on first generation):

   ```bash
   source .venv/bin/activate
   pip install -r requirements-ai.txt
   ```

5. Generate one test reel before batching:

   ```bash
   reelforge doctor
   reelforge render --count 1
   ```

Kokoro and the quantized Z-Image-Turbo model download on first use. Piper is the voice
fallback and downloads its ONNX voice if it is not cached. The first setup needs
internet; inference is local after the required packages and model weights are
present. Keep batch concurrency at one reel at a time on 16 GB unified memory. The pipeline starts with 4-bit Z-Image-Turbo at 512×768; this is a conservative starting point, not a guarantee that every other app can remain open during generation. If memory pressure occurs, close memory-heavy apps before rendering.

The visual pipeline is intentionally local-only: each scene is generated from its prompt using Z-Image-Turbo through MFLUX (Apple MLX). There is no stock-image search, image picker, hosted inference API, or API key. If image generation fails, the render fails rather than silently substituting a gradient.

## Configuration

One file, `config/channel.yaml`. The fields that matter:

```yaml
lens / audience / tone     # what the channel is; feeds the script prompt
script.min_scenes          # 9-11 scenes of 10-14 words ≈ 35 s
llm.models                 # ordered Ollama model fallback list
voice.engine               # kokoro (primary) | piper (offline fallback)
voice.name                 # en-US-AndrewNeural, en-IN-PrabhatNeural, ...
visuals.provider           # ai_cartoon (local MFLUX Z-Image-Turbo only)
captions.mode              # auto | ass | png
audio.target_lufs          # -16; both platforms normalise, quiet loses
publish.youtube.privacy    # public | unlisted | private
```

`config/topics.yaml` is the queue. One reel per entry, in order, never repeated
(tracked in `work/reelforge.db`). Add topics; the `beats` list is the offline
fallback and is used verbatim, so write it well.

## Visual generation

The only configured AI visual backend is `mflux_z_image`, backed by Tongyi-MAI's Z-Image-Turbo model through MFLUX/Apple MLX. The model weights are Apache-2.0 licensed. The 4-bit quantized configuration is chosen as a cautious starting point for a 16 GB M4; actual memory and generation speed must be validated on the Mac. Install with `pip install -r requirements-ai.txt`. Images are cached in `assets/cache/ai/<seed>.jpg`, so rerenders reuse generated scenes.

The channel uses cinematic-realism prompts, one generated image per scene, and slow alternating zoom/pan motion. `visuals.require_ai_images: true` prevents a failed generation from becoming a gradient placeholder. Remote image services and image-search providers are not used by the active pipeline.

Z-Image-Turbo is distributed under Apache 2.0, which permits commercial use subject to the licence conditions. Keep the model licence notice with the project and re-check the upstream model card before commercial release. A permissive model licence reduces licensing friction but cannot guarantee that every generated image is free of third-party rights claims.

### Animation

`visuals.animate: true` applies a different slow motion to each scene — push in,
pull out, pan left, pan right, cycled. Static stills in sequence read as a
slideshow; alternating motion is what makes it read as video.

## Music

Drop `.mp3`/`.wav`/`.m4a` files into `assets/music/` and the first one is used,
ducked under the voice with a sidechain compressor. With an empty folder the
pipeline synthesises a quiet pad so previews are not silent — that is a
placeholder, not music. Use material you actually have the right to publish
(YouTube Audio Library, Pixabay Music, or a licence you bought).

## Publishing

### YouTube Shorts

1. Google Cloud Console → new project → enable **YouTube Data API v3**
2. OAuth consent screen → **External** → add yourself as a test user
3. **Publish the app** ("In production"). Unverified is fine for your own
   channel. *If you leave it in Testing, the refresh token expires every 7 days
   and the nightly job starts failing silently.*
4. Credentials → OAuth client ID → **Desktop app** → download JSON →
   `secrets/yt_client_secret.json`
5. `reelforge publish <reel_id> --yes` — first run opens a browser once and
   caches `secrets/yt_token.json`

A vertical video under 60 s is picked up as a Short automatically. Keep
`#Shorts` in the description as a belt-and-braces signal.

Quota: `videos.insert` costs **1,600 units** of a 10,000/day default budget, so
**~6 uploads/day**, resetting midnight Pacific.

On the audit question: the widely repeated rule is that uploads from an
unaudited project are forced **private**. Our first upload from a fresh project
came out **public**, so do not assume it either way — check the video's status
after uploading instead of planning around it. If it does land private, request
the API audit once from the API console.

### Meta: Instagram Reels + Facebook Reels

Both come from **one Meta app** and **one Facebook Page**. Instagram needs the
Page linked to a Business/Creator account; Facebook Reels needs the Page itself.

1. Convert Instagram to **Business or Creator**, then link it to a Facebook Page
   (Instagram app → Settings → Business tools).
2. [developers.facebook.com](https://developers.facebook.com) → **Create app** →
   type **Business** → add the **Instagram** product.
3. Open the app → **App roles** → add your own account as an **Administrator**
   (already true if you created it). You do *not* need App Review for publishing
   to accounts that hold a role on the app.
4. Open **Graph API Explorer**, select your app, and request:
   `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
   `publish_video`, `instagram_basic`, `instagram_content_publish`.
   Generate a **user** access token.
5. Derive everything else from that token:

   ```bash
   # put META_APP_ID / META_APP_SECRET (App settings > Basic) in .env first,
   # otherwise the token stays short-lived (1 hour instead of 60 days)
   python scripts/meta_setup.py <USER_ACCESS_TOKEN>
   ```

   It prints `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID`, `IG_ACCESS_TOKEN`.
   The Page token does not expire when derived from a long-lived user token.

6. Instagram still needs the MP4 at a **public URL** — it fetches the file, it
   does not accept an upload. Free option: Cloudflare **R2** (10 GB, zero
   egress), enable a public `r2.dev` URL, then set `IG_HOST=r2` plus the
   `R2_*` variables. `IG_HOST=gh_release` uses a GitHub release asset instead.
   **Facebook Reels does not need this** — it takes the bytes directly.

Rate limits: Instagram 25 API-published posts per rolling 24 h; Facebook Reels
are generous by comparison. Reel limits for both: 9:16, 3–90 s, ≤ 1 GB.

### Publish

```bash
reelforge publish <reel_id>                    # dry run, lists what would go where
reelforge publish <reel_id> --yes              # publish to all three
reelforge publish <reel_id> --platforms youtube --yes
```

Per-platform text is generated separately (`src/reelforge/meta.py`): YouTube gets
a title, tags and `#Shorts`; Instagram and Facebook get captions with their own
hashtag mixes. Cross-posting the same file is fine; cross-posting identical text
is not.

Re-running a publish is safe: each platform is recorded in `publications`, and
an already-published platform is skipped rather than posted twice. A platform
that fails does not stop the others — the summary reports which succeeded.

To make it fully unattended, `scripts/schedule.sh install 09:00` runs
`reelforge run --count 1 --publish` daily: generate, then publish to all three.

## Scheduling

```bash
./scripts/schedule.sh install     # daily at 09:00 via launchd
./scripts/schedule.sh remove
```

The job runs `reelforge run --count 1 --publish` and logs to `work/cron.log`.
macOS must be awake: the plist pairs with `caffeinate`, and a laptop that is
asleep at 09:00 simply misses the run. If that matters, move to a $5 VPS.

## Traps this pipeline already handles

| Trap | What happens | Handled by |
|---|---|---|
| SDXL at 832×1216 on MPS | **110 s/image** vs 1.4 s at 512×768 — a memory cliff, not a slope | default is 512×768 + Lanczos upscale; see `visuals.ai_width` |
| Width/height not divisible by 8 | `ValueError: height and width have to be divisible by 8` | snapped to multiples of 8 before inference |
| Several reels requesting TTS at once | a hung request blocks the whole reel (observed: 13 min on one scene) | 70 s hard timeout per attempt, 6 retries |
| Fresh YouTube upload shows "Your browser can't play this video" | looks like a codec fault; it is transcoding - no playable stream exists yet | wait a few minutes; use `/watch?v=` while `/shorts/` lags |
| Voice change silently changes reel length | 10 scenes at 2.13 wps (Prabhat) runs ~51 s; the same script at 2.90 wps (Andrew) runs ~35 s | recalibrate `script.words_per_second` whenever `voice.name` changes |
| `edge-tts` rate-limits ~40% of calls | `NoAudioReceived` | 6 jittered retries + 0.45 s gap between scenes |
| `edge-tts` defaults to **SentenceBoundary** | captions would be per-sentence, silently | `boundary="WordBoundary"` passed explicitly |
| No word boundaries returned | captions would fail the reel | falls back to even spacing across real duration |
| Homebrew ffmpeg 8 has **no libass/freetype** | `ass`, `subtitles`, `drawtext` do not exist | captions rendered as PNG cards with Pillow |
| A 3B model invents statistics | "for every 3 late payments…" | validator + one corrective retry + beat-sheet fallback |
| Music louder than the voice | dialogue unintelligible | sidechain ducking + loudnorm to −16 LUFS |
| Duplicate posting on retry | same reel published twice | `publications` table is checked first |
| Hosted image API throttled | 4 of 5 requests fail, 40 s each | local diffusion is the default backend |
| Turbo checkpoints look washed out | grey, flat images | `guidance_scale=0.0`, 1–4 steps, as the checkpoints require |

## Batch mode

```bash
PYTHONPATH=src .venv/bin/python scripts/batch.py <topic-id> <topic-id> ... --jobs 3
```

Two phases, because they need opposite things:

- **Phase A** loads the diffusion model once and generates every image for every
  reel into `assets/cache/ai/`. Measured: **50 images in 84 s** (1.4 s each once
  the pipeline is warm).
- **Phase B** runs each reel as a separate process, `--jobs` at a time. Every
  image is now a cache hit, so *no* render process needs the GPU — that is what
  makes parallel rendering safe with 24 GB of unified memory.

Keep `--jobs` at 3 or below: `edge-tts` degrades badly under concurrency, and a
hung request used to stall a whole reel.

Measured end-to-end: **5 reels (10 scenes each, ~35 s) in ~19 minutes**, versus
~20 minutes for a single reel before the resolution fix.

## GitHub Actions on your M4 Mac

The GitHub Actions workflow uses a **self-hosted macOS ARM64 runner** labelled
`gita-grit-m4`. It can be started manually or daily at 09:00 India time. It
generates one reel locally, runs the technical video checks, and automatically
publishes a passing reel to YouTube. It does not run on GitHub-hosted Linux
runners and does not publish on pushes or pull requests.

Follow [the self-hosted M4 runner setup](docs/FREE_CI_SETUP.md) before enabling
the workflow. The Mac must be awake and the runner online. YouTube OAuth
credentials are stored as encrypted GitHub Actions secrets and materialized
temporarily with restrictive file permissions during the job.

The macOS `launchd` scheduler remains an alternative if you prefer to schedule
directly on the Mac rather than through GitHub Actions.

## Layout

```
src/reelforge/
  config.py       YAML + .env
  state.py        sqlite ledger (reels, publications, topics, visual reuse)
  llm.py          Ollama call, validation, beat-sheet fallback
  voice.py        edge-tts / kokoro, word boundaries, retries
  visuals.py      procedural / pexels / local → 1080x1920 scene mp4
  captions.py     ASS karaoke (only with libass)
  captions_png.py Pillow caption cards (works on any ffmpeg)
  render.py       per-scene parts, concat, overlay, mix, loudnorm
  meta.py         per-platform titles / descriptions / hashtags
  pipeline.py     the six steps
  publish/        youtube.py, instagram.py, host (r2 / gh_release)
work/<reel>/      intermediates, kept for debugging
out/<reel>.mp4    the deliverable
out/<reel>.json   script + per-scene timings, for review
```
