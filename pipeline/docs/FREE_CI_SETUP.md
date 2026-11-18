# GitHub Actions on the M4 Mac (self-hosted runner)

This setup makes GitHub Actions the orchestrator while the actual workload runs
on your own Apple Silicon M4. GitHub-hosted Linux runners cannot access the Mac's
MLX GPU, local model cache, or local Ollama service.

## What runs

A manual **Run workflow** or the daily schedule (09:00 India time) starts this
pipeline on the M4 runner:

1. Check the Mac runner, FFmpeg, Ollama, and local YouTube credentials.
2. Install/update Python dependencies and ensure the local Ollama models exist.
3. Generate one reel using local Ollama, MFLUX/MLX, local voice, and FFmpeg.
4. Run ReelForge's technical video validation checks.
5. Publish the validated reel to **YouTube only**.
6. Upload the MP4/JSON as a GitHub Actions artifact.

The workflow does not run on pushes or pull requests. A manual run or the daily
schedule is the only trigger. Publishing happens automatically only after render
and validation succeed. The publication ledger is intended to prevent successful
uploads from being posted again on a retry. Technical checks cannot judge every
creative, factual, or copyright issue.

## 1. Register the Mac as a repository runner

Keep the Mac awake and connected to the internet for scheduled jobs. In GitHub:

1. Open **rohityawalkar04/doc-site → Settings → Actions → Runners**.
2. Select **New self-hosted runner → macOS → ARM64**.
3. Follow GitHub's displayed download/configuration commands on the Mac.
4. During runner configuration, add the custom label **gita-grit-m4** (keep
   the default **self-hosted**, **macOS**, and **ARM64** labels).
5. Install and start the runner service using the commands GitHub displays.

Do not paste the runner registration token into chat, a commit, or a workflow
file. GitHub displays a short-lived token in the runner setup page.

## 2. Prepare the Mac once

Install system dependencies and models:

```bash
brew install ffmpeg ollama
ollama pull qwen2.5:7b-instruct
ollama pull gemma3:4b
```

Clone this repository into the runner user's home directory. In
`doc-site/pipeline`, run:

```bash
./scripts/setup.sh
source .venv/bin/activate
pip install -r requirements-ai.txt
```

Start Ollama and verify it responds:

```bash
ollama serve
curl http://127.0.0.1:11434/api/tags
```

The workflow attempts to start Ollama if it is not already responding.
The first MFLUX run downloads model weights; keep enough free disk space.
Image inference stays on the Mac. No paid image-generation API is used.

## 3. Set up YouTube OAuth locally

Complete the YouTube Data API v3 OAuth setup described in the main README.
Authorize the channel once on the Mac so these files exist:

- `pipeline/secrets/yt_client_secret.json`
- `pipeline/secrets/yt_token.json`

Keep these files on the runner machine only. The workflow checks that they exist,
restricts their permissions, and never prints their contents. Do not commit them
or upload them as artifacts. Ensure `pipeline/secrets/` is ignored by Git.

## 4. Enable the workflow

After this change is merged into the repository's default branch, open
**Actions → Gita & Grit — build and publish → Run workflow**.

The scheduled workflow runs daily at **09:00 Asia/Kolkata**. Both manual and
scheduled runs publish to YouTube automatically after technical validation
passes. The Mac must be awake, the self-hosted runner service online, and the
internet available. GitHub may delay scheduled jobs.

## Safety and operating limits

- Only manual dispatch and the daily schedule trigger publishing; pushes and PRs do not.
- One job at a time; concurrent runs are serialized.
- A failed render or failed `reelforge verify` prevents publishing.
- Only YouTube is enabled in this workflow; Instagram/Facebook are not configured.
- Keep the publication database and generated work/output directories on the
  persistent runner workspace. Do not run multiple runner instances against it.
- YouTube API quota, account restrictions, and upload processing still apply.
- Technical validation is not copyright clearance or a monetization guarantee.
  Use original/licensed music and review content and model licences.
- MFLUX loader compatibility and a complete first M4 render must be tested on
  the actual Mac before relying on the daily schedule.
