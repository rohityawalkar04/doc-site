#!/usr/bin/env bash
# Publish a <owner>.github.io user site so the HOST ROOT resolves.
#
# Why: Google's OAuth branding check verifies ownership of the AUTHORIZED DOMAIN
# (rohityawalkar04.github.io). A Search Console "URL prefix" property for
# /gitaandgrit-site/ only proves the subpath, and the host root 404s - which reads
# to Google as a site that is not registered to you.
#
# Requires: gh auth login
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

gh auth status >/dev/null 2>&1 || { echo "run: gh auth login -h github.com" >&2; exit 1; }
OWNER="$(gh api user -q .login)"
REPO="$OWNER.github.io"

echo "-> owner: $OWNER   root repo: $REPO"

if ! gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
  echo "-> creating $OWNER/$REPO"
  gh repo create "$OWNER/$REPO" --public \
    --description "Site root for Search Console ownership verification" >/dev/null
else
  echo "-> repo exists"
fi

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
git clone --depth 1 "https://github.com/$OWNER/$REPO.git" "$TMP" 2>/dev/null || {
  mkdir -p "$TMP"; git -C "$TMP" init -q
  git -C "$TMP" remote add origin "https://github.com/$OWNER/$REPO.git"
}
git -C "$TMP" checkout -q -B main 2>/dev/null || git -C "$TMP" checkout -q -b main

# Search Console verification files must sit at the property root.
shopt -s nullglob
verify_files=("$ROOT"/docs/google*.html)
if [ ${#verify_files[@]} -eq 0 ]; then
  echo "  !! no docs/google*.html found - download the HTML file from Search Console first" >&2
  exit 1
fi
cp "${verify_files[@]}" "$TMP/"
echo "-> copied ${#verify_files[@]} verification file(s)"

cat > "$TMP/index.html" <<HTML
<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Gita &amp; Grit</title>
<meta name="google-site-verification" content="">
<link rel="canonical" href="https://$OWNER.github.io/gitaandgrit-site/">
<meta http-equiv="refresh" content="0; url=https://$OWNER.github.io/gitaandgrit-site/">
<style>body{margin:0;height:100vh;display:grid;place-items:center;background:#14121f;
color:#ece9f5;font:16px/1.6 -apple-system,sans-serif}a{color:#ffd84d}</style>
</head><body><p>Redirecting to
<a href="https://$OWNER.github.io/gitaandgrit-site/">Gita &amp; Grit</a>…</p></body></html>
HTML

git -C "$TMP" add -A
git -C "$TMP" -c user.name="$OWNER" -c user.email="$OWNER@users.noreply.github.com" \
  commit -q -m "Add site root and Search Console verification" || echo "  (nothing new)"
git -C "$TMP" push -q -u origin main

echo '{"source":{"branch":"main","path":"/"}}' \
  | gh api -X POST "repos/$OWNER/$REPO/pages" --input - >/dev/null 2>&1 \
  || echo '{"source":{"branch":"main","path":"/"}}' \
     | gh api -X PUT "repos/$OWNER/$REPO/pages" --input - >/dev/null 2>&1 || true

echo
echo "  In 1-2 minutes the host root must return 200:"
echo "    https://$OWNER.github.io/"
echo
echo "  Then in Search Console add a URL-prefix property for:"
echo "    https://$OWNER.github.io/"
echo "  and verify it the same way. This property covers the authorized domain."
