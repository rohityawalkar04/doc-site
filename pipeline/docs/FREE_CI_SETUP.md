# Free GitHub Actions Shorts pipeline

The hosted workflow uses only zero-cost components for the build:

| Stage | Tool | Cost |
|---|---|---|
| Workflow + CPU rendering | GitHub Actions hosted Ubuntu runner | Free-tier allowance |
| Visuals | Openverse anonymous API (CC0 images) | Free API access, subject to anonymous rate limits |
| Voice | Piper TTS (en_US-ryan-high) | Open source; model downloaded/cached |
| Captions + video | Pillow + FFmpeg | Open source |
| Script | Curated beats in config/topics.yaml | No inference API |
| Publishing | YouTube Data API | Platform quota; no paid image/voice provider |

**Important:** Openverse supplies openly licensed images, not generated AI images. Still images are animated with slow pan/zoom to create motion. A standard GitHub-hosted runner has no GPU, so this is the reliable $0 hosted visual route. AI image generation remains available locally on the Mac through the optional SDXL setup.

## One-time setup

No API key or repository secret is needed for visuals. Openverse supports anonymous API requests, subject to rate limits. The pipeline searches CC0-only images to avoid attribution requirements for monetized Shorts. No Cloudflare or Pexels secrets are needed.

For a preview, open **Actions → reel → Run workflow**, leave mode as **dry-run**, and set count to **1**. This builds one MP4 and uploads it as the `reels` artifact; it does not publish to YouTube or Meta.

After watching and approving the preview, manually run the workflow with mode **publish**. Publishing additionally requires the existing `YT_CLIENT_SECRET_JSON` and `YT_TOKEN_JSON` secrets. The daily scheduled workflow creates a preview artifact only. It does not publish. YouTube secrets are required only when you manually choose **publish**.

## Quality and failure policy

- Each scene searches Openverse for large portrait-friendly CC0 images.
- Images are animated with slow pan/zoom; this provides movement without a GPU or paid video API.
- Downloaded stock assets are cached between matching workflow runs.
- An empty search result, API rate limit, failed download, or unusable asset fails the build. The pipeline will not quietly substitute gradients.
- The first run may take longer because stock footage and the Piper voice model must be downloaded.
- Openverse results are selected from the scene's visual query. Review the MP4 before publishing because stock footage can be semantically approximate.