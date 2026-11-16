#!/usr/bin/env bash
# Weekly YouTube re-authorisation, in one command.
#
# While the OAuth app is in Testing, the refresh token dies every 7 days. This
# does the whole chore: consent in the browser, then push the new token straight
# into the GitHub secret CI reads.
#
#   ./scripts/reauth.sh              # consent + upload the new token to GitHub
#   ./scripts/reauth.sh --upload-only # just re-push the existing token
#
# The real fix is publishing the OAuth app to production, which removes this
# entirely. Until then, this is four seconds of clicking instead of a checklist.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${REEL_REPO:-rohityawalkar04/doc-site}"
cd "$ROOT"

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated. Run: gh auth login -h github.com" >&2
  exit 1
fi

if [ "${1:-}" != "--upload-only" ]; then
  echo "-> opening the browser for consent"
  ./reelforge auth youtube
fi

TOKEN="$ROOT/secrets/yt_token.json"
if [ ! -s "$TOKEN" ]; then
  echo "no token at $TOKEN" >&2
  exit 1
fi

# Fail before touching the secret if the file isn't a refresh token.
python3 - "$TOKEN" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
if not d.get("refresh_token"):
    sys.exit("token file has no refresh_token")
print(f"   token ok (expires {d.get('expiry', 'unknown')})")
PY

echo "-> updating YT_TOKEN_JSON on $REPO"
gh secret set YT_TOKEN_JSON --repo "$REPO" < "$TOKEN"

cat <<'MSG'

  done. CI will use the new token on its next run.

  Verify with:
    gh workflow run daily.yml --repo REPO --field mode=dry-run
MSG
