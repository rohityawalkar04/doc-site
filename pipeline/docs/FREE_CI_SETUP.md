# Free GitHub Actions Shorts pipeline

The hosted workflow uses only zero-cost components for the build:

| Stage | Tool | Cost |
|---|---|---|
| Workflow + CPU rendering | GitHub Actions hosted Ubuntu runner | Free-tier allowance |
| Visuals | Pexels API (portrait stock videos, photo fallback) | Free API access, subject to Pexels limits |
| Voice | Piper TTS (en_US-ryan-high) | Open source; model downloaded/cached |
| Captions + video | Pillow + FFmpeg | Open source |
| Script | Curated beats in config/topics.yaml | No inference API |
| Publishing | YouTube Data API | Platform quota; no paid image/voice provider |

**Important:** Pexels provides licensed stock footage, not AI-generated images. A standard GitHub-hosted runner has no GPU, so this is the reliable $0 hosted visual route. AI image generation remains available locally on the Mac through the optional SDXL setup.

## One-time setup

1. Create a free Pexels account and request an API key at https://www.pexels.com/api/.
2. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret**.
3. Add `PEXELS_API_KEY` with the key from Pexels.
4. No Cloudflare account or Cloudflare secrets are needed.

For a preview, open **Actions → reel → Run workflow**, leave mode as **dry-run**, and set count to **1**. This builds one MP4 and uploads it as the `reels` artifact; it does not publish to YouTube or Meta.

After watching and approving the preview, manually run the workflow with mode **publish**. Publishing additionally requires the existing `YT_CLIENT_SECRET_JSON` and `YT_TOKEN_JSON` secrets. The daily scheduled workflow creates a preview artifact only. It does not publish. YouTube secrets are required only when you manually choose **publish**.

## Quality and failure policy

- Each scene searches Pexels for portrait video first, then portrait photos.
- Portrait video renditions closest to 1080×1920 are preferred.
- Downloaded stock assets are cached between matching workflow runs.
- A missing key, empty search result, failed download, or unusable asset fails the build. The pipeline will not quietly substitute gradients.
- The first run may take longer because stock footage and the Piper voice model must be downloaded.
- Pexels results are selected from the scene's visual query. Review the MP4 before publishing because stock footage can be semantically approximate.