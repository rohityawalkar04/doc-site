# reelforge

Faceless short-form video factory. Script → voice → visuals → captions → 1080×1920 MP4,
then publish to YouTube Shorts and Instagram Reels.

Everything runs on your machine. The only recurring cost is $0; the only thing you
buy is your own time.

```
config/topics.yaml ──┐
                     ├─ ollama ──> script.json
config/channel.yaml ─┘                 │
                                      ├─ edge-tts ──> audio + word timings
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

| Stage | Tool | Cost | Notes |
|---|---|---|---|
| Script | Ollama (`qwen2.5:7b-instruct`) | $0 | Falls back to the hand-written beat sheet in `topics.yaml` when Ollama is down or the script fails validation |
| Voice | `edge-tts` | $0 | Returns exact word boundaries → no ASR alignment step needed |
| Visuals | ffmpeg `gradients` / Pexels / local | $0 | `procedural` needs no keys; `pexels` needs a free key |
| Render | ffmpeg + Pillow | $0 | 1080×1920, H.264 High, yuv420p, 30 fps, AAC 48 kHz |
| Publish | YouTube Data API v3, Instagram Graph API | $0 | Quota-limited, not money-limited |

Typical render: **~3.5 minutes** for a 32 s / 12-scene reel on an M4 Pro.
Measured breakdown, which is where the tuning effort went:

| Stage | Time | Note |
|---|---|---|
| script (qwen2.5:7b) | 35–70 s | 2 attempts when the first is short |
| voice (12 scenes) | 60–90 s | `edge-tts` rate-limits; ~40% of calls need a retry |
| visuals | 5–8 s | was ~110 s before removing the temporal `noise` filter |
| assemble + captions | ~1 s | concat is stream-copy; captions are Pillow |
| final encode | 35–55 s | one x264 pass, ~25–35 overlays |
| **total** | **~3.5 min** | a 7-scene reel was ~1.5 min |

The two slow stages are both bounded by things you cannot tune away: the local
model's tokens per second, and the TTS endpoint's rate limit.

## Configuration

One file, `config/channel.yaml`. The fields that matter:

```yaml
lens / audience / tone     # what the channel is; feeds the script prompt
script.min_scenes          # 9-11 scenes of 10-14 words ≈ 35 s
voice.engine               # edge (default) | kokoro (Apache-2.0, clean rights)
voice.name                 # en-US-AndrewNeural, en-IN-PrabhatNeural, ...
visuals.provider           # procedural | pexels | local
captions.mode              # auto | ass | png
audio.target_lufs          # -16; both platforms normalise, quiet loses
publish.youtube.privacy    # public | unlisted | private
```

`config/topics.yaml` is the queue. One reel per entry, in order, never repeated
(tracked in `work/reelforge.db`). Add topics; the `beats` list is the offline
fallback and is used verbatim, so write it well.

## Visual providers

| provider | needs | when |
|---|---|---|
| `ai_cartoon` | `pip install -r requirements-ai.txt` (local SD) | default; AI illustrations, animated with zoom/pan |
| `procedural` | nothing | zero-dependency fallback; looks identical across videos |
| `pexels` | `PEXELS_API_KEY` (free, 200 req/h) | real licensed b-roll |
| `local` | files in `assets/broll/` | your own footage |

### AI illustrations

`ai_cartoon` runs **local** Stable Diffusion (`stabilityai/sdxl-turbo`, ~6.9 GB,
downloaded once) on Apple Silicon MPS. Free, offline, no rate limit, ~2–5 s per
image. Swap `visuals.local_model` for `Lykon/dreamshaper-8-lcm` (~4 GB, SD1.5,
faster, more illustration-like) if disk is tight.

`visuals.ai_backend: pollinations` uses the hosted free tier instead. Do not rely
on it: measured in Nov 2026, `model=flux` returns HTTP 402 and `model=sana` is
throttled such that **1 of 5 requests succeeded**, each failure taking ~40 s.
It is wired as a secondary attempt, not the default.

Every illustration is cached in `assets/cache/ai/<seed>.jpg`, so re-rendering a
reel costs no generation time.

### Animation

`visuals.animate: true` applies a different slow motion to each scene — push in,
pull out, pan left, pan right, cycled. Static stills in sequence read as a
slideshow; alternating motion is what makes it read as video.

Pexels is the one to switch to before publishing anything real. Procedural
gradients across 50 reels is exactly the "mass-produced low-value" pattern that
platforms demote.

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

## Running it in the cloud, for free

The problem with scheduling this on a laptop: `launchd` does not fire if the Mac
is asleep, and a closed laptop is a channel that silently stops posting.

The free answer is **GitHub Actions + Cloudflare Workers AI**. No VPS, no GPU, no
card, no machine at home.

| Piece | Service | Free allowance |
|---|---|---|
| Schedule + compute | GitHub Actions | unmetered **on public repos** |
| Images | Cloudflare Workers AI (`flux-1-schnell`) | 10,000 Neurons/day, no card (~200 images) |
| Voice | `edge-tts` | unlimited |
| Render | ffmpeg on the runner | CPU only, fine |
| Publish | YouTube + Meta APIs | 6 YT uploads/day, 25 IG posts/day |
| Instagram video host | GitHub **release asset** | no bucket needed |

### Why these specific services

- **Scheduled workflows only run on public repositories** on the GitHub Free plan.
  Private repos need Pro. This is the single most common reason a free CI
  schedule "just stops".
- **Gemini's image API has no free tier** (quota stays at 0 until paid), so Google
  is not an option for images even though you already have the account.
- **Instagram fetches the video from a URL.** A GitHub release asset serves that
  purpose, so R2 is optional rather than required.

### Setup

1. Repo must be **public**.
2. Add secrets (Settings → Secrets and variables → Actions):

   | Secret | From |
   |---|---|
   | `YT_CLIENT_SECRET_JSON` | the whole OAuth JSON, as one line |
   | `YT_TOKEN_JSON` | `secrets/yt_token.json` after one local authorise |
   | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Cloudflare dashboard |
   | `IG_USER_ID`, `IG_ACCESS_TOKEN`, `FB_PAGE_ID`, `FB_PAGE_TOKEN` | `scripts/meta_setup.py` |

3. **One flow, sequential and fail-fast.** The `reel` workflow builds a reel,
   publishes it, then takes the next, and stops dead at the first failure:

   ```
   build announcing-the-plan  -> publish  LOST
   STOPPED after 10 published. 11 reel(s) still queued.
   ```

   `count` defaults to `0` = keep going until something fails. Set `count: 1` to
   publish a single episode. The topic that failed stays out of the ledger, so
   the next run retries it.
4. **The ledger is written after every reel, not once per run.** `cloud_run.py
   --record` commits and pushes `state/published.json` immediately after each
   upload, and **stops the batch** if that push fails. The 16-17 Nov runs pushed
   once at the end; the push failed on a detached `actions/checkout` HEAD, so the
   ledger never landed and the next night re-uploaded the same three topics. One
   lost push now costs at most one duplicate, and the run turns red instead of
   finishing green with an untrue ledger. Publishing also asks the ledger before
   uploading (bypass on purpose with `reelforge publish <reel> --force`), so a
   fresh checkout cannot re-upload something another machine already published.
5. **The channel itself is the last word.** Before a YouTube upload,
   `publish.youtube.channel_id` is used to read
   `youtube.com/feeds/videos.xml` and compare titles. Zero quota, no OAuth scope,
   no auth. This is the only check that survives a ledger which is wrong for a
   reason outside this code - a manual upload in Studio, or a push that lost
   after the video went live. The feed covers the last 15 uploads; the ledger
   covers everything older. If the feed cannot be read after three attempts the
   upload is **refused** and the run goes red: a missed night is recoverable, a
   second copy on the channel is not. `--force` is the human override.
6. Runs daily at `37 13 * * *` = **19:07 IST**, inside the evening posting
   window. `:37` because GitHub's scheduler is worst at the top of the hour and
   can drop runs entirely under load.
7. **Every run ends by reconciling the ledger with the channel.**
   `scripts/check_channel.py` reads the feed, maps each video back to a ledger
   topic, and fails the run if the channel holds anything the ledger does not
   know about - the exact state that produced the 16 Nov duplicates. It also
   reports titles that exist twice on the channel, and ledger topics with no
   video on the channel (deleted or unlisted: five mythology reels are in that
   state as of 18 Nov). Run it by hand any time:
   `cd pipeline && PYTHONPATH=src python scripts/check_channel.py`.

### What changes in the cloud

- **No LLM** — `script.mode: beats`, so scripts come from `config/topics.yaml`.
  Write them in batches; that part is still yours.
- **No sqlite** — the runner is wiped each time, so "what has been published"
  lives in `state/published.json`, committed back by the workflow.
- **Image cache, keyed per run** — scene images are named by a hash of
  (visual_query, scene index), so a scene that was rendered once is free to
  rebuild. The workflow restores the newest `ai-` snapshot and always writes a
  new one, including on a failed run, so a retry resumes instead of paying
  ~580 Neurons again. (The key used to be the topics.yaml hash alone, constant
  for weeks: actions/cache skips the save on an exact hit, so new scenes were
  generated and never stored. That is what exhausted the daily budget on
  17-18 Nov.)

### The catch that matters

In **Testing** mode the Google refresh token expires every **7 days**, so an
unattended job fails weekly and you would not notice. Moving the OAuth app to
**production** is no longer cosmetic — it is a precondition for this design.

Also: GitHub disables scheduled workflows after **60 days** of repository
inactivity. The ledger commit each run is what keeps it alive.

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
