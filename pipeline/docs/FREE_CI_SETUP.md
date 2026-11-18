# Local M4 Shorts pipeline

Gita & Grit now generates every scene image locally on the owner's Apple Silicon Mac.
The GitHub-hosted Linux runner cannot access that Mac's GPU, so the old cloud image
search workflow is intentionally disabled.

| Stage | Tool | Execution |
|---|---|---|
| Script | Ollama Qwen2.5 / Gemma 3 | Local |
| Visuals | Z-Image-Turbo via MFLUX / Apple MLX | Local M4 Mac, 4-bit quantized |
| Voice | Kokoro / Piper | Local |
| Captions + video | Pillow + FFmpeg | Local |
| Publishing | YouTube / Meta APIs | Local after review |

## One-time setup

Follow the Apple Silicon setup in the main README. Install the base project, then
run `pip install -r requirements-ai.txt` inside the project's virtual environment.
The first image-generation run downloads the model weights. No image API key,
stock-image account, or hosted inference service is used.

## Build and review

```bash
reelforge doctor
reelforge render --count 1
open out/*.mp4
```

Only publish after watching the complete MP4 and checking the audio, captions, visuals,
and rights for any music or other assets included in the reel.

## Scheduling

The macOS `launchd` scheduler can run the pipeline while the Mac is awake:

```bash
./scripts/schedule.sh install 09:00
./scripts/schedule.sh remove
```

The GitHub Actions reel workflow is informational only. It does not generate images,
download stock images, call hosted image APIs, or publish videos.
