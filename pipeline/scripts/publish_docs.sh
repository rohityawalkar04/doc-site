#!/usr/bin/env bash
# Publish docs/ to GitHub Pages and print the live URLs.
# Requires: gh auth login (with repo scope).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${1:-doc-site}"

if ! gh auth status >/dev/null 2>&1; then
  echo "gh is not authenticated. Run: gh auth login -h github.com" >&2
  exit 1
fi

OWNER="$(gh api user -q .login)"
echo "-> owner: $OWNER   repo: $REPO"

if ! gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "-> creating public repo $OWNER/$REPO"
  gh repo create "$OWNER/$REPO" --public \
    --description "Gita & Grit — app information, privacy policy, terms" >/dev/null
else
  echo "-> repo exists"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "https://github.com/$OWNER/$REPO.git" "$TMP" 2>/dev/null || {
  mkdir -p "$TMP"; git -C "$TMP" init -q
  git -C "$TMP" remote add origin "https://github.com/$OWNER/$REPO.git"
}
git -C "$TMP" checkout -q -B main 2>/dev/null || git -C "$TMP" checkout -q -b main

cp "$ROOT/docs/"*.html "$TMP/"
[ -f "$ROOT/docs/logo-120.png" ] && cp "$ROOT/docs/logo-120.png" "$TMP/"

# CONTACT_EMAIL must be a real address or reviewers treat the policy as boilerplate.
if grep -q "CONTACT_EMAIL" "$TMP"/*.html 2>/dev/null; then
  echo
  echo "  !! CONTACT_EMAIL is still a placeholder."
  echo "     Fix with: sed -i '' 's/CONTACT_EMAIL/you@gmail.com/g' docs/*.html"
  echo "     Then re-run this script. Aborting."
  exit 1
fi

git -C "$TMP" add -A
git -C "$TMP" -c user.name="$OWNER" -c user.email="$OWNER@users.noreply.github.com" \
  commit -q -m "Publish app information, privacy policy, terms of service" || echo "  (nothing new to commit)"
git -C "$TMP" push -q -u origin main

echo "-> enabling Pages (branch main, root)"
echo '{"source":{"branch":"main","path":"/"}}' \
  | gh api -X POST "repos/$OWNER/$REPO/pages" --input - >/dev/null 2>&1 \
  || echo '{"source":{"branch":"main","path":"/"}}' \
     | gh api -X PUT "repos/$OWNER/$REPO/pages" --input - >/dev/null 2>&1 \
  || true

BASE="https://$OWNER.github.io/$REPO"
echo
echo "  Pages build takes 1-2 minutes, then check:"
echo "    $BASE/"
echo "    $BASE/privacy.html"
echo "    $BASE/terms.html"
echo
echo "  Authorized domain for the OAuth consent screen:"
echo "    $OWNER.github.io"
