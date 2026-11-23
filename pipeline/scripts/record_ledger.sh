#!/usr/bin/env bash
# Commit and push state/published.json. Run after EVERY upload, not once per batch.
#
# Incident, 16-17 Nov: a run uploaded six reels, then made a single push at the
# end. actions/checkout leaves a detached HEAD for schedule and workflow_dispatch
# runs, a bare `git push` fails there, the ledger never landed - and the next
# night's run, reading the same stale ledger, uploaded the same three topics
# again. One failed push cost six duplicate uploads (three pairs, ~3,600 views
# split across copies).
#
# Two properties this script exists to give the caller:
#   * per reel  - one lost push costs one duplicate, not a night's batch
#   * fail closed - a non-zero exit tells the caller to STOP uploading, because
#     carrying on is exactly how the batch above grew
#
# Usage:
#   scripts/record_ledger.sh              commit + push, exit 1 if the push loses
#   BRANCH=main scripts/record_ledger.sh   override the target branch
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || { echo "record_ledger: cannot cd to $ROOT" >&2; exit 1; }

BRANCH="${BRANCH:-main}"
IDENT=(-c user.name=reelforge-bot -c user.email=reelforge-bot@users.noreply.github.com)

if [ ! -f state/published.json ]; then
  echo "record_ledger: state/published.json is missing - nothing to record" >&2
  exit 1
fi

git add state/ 2>/dev/null || true
# A topic composed at runtime is written by the build, so it travels with the
# ledger: the ledger says it published, this file says what it said. Only that
# one path is added - never config/ as a whole, which would sweep unrelated
# working changes into the bot's commit. Without this line the composed topic is
# written on the runner and thrown away with the workspace, so the channel's
# script history is a ledger entry with a title and nothing else.
if [ -f config/topics-generated.yaml ]; then
  git add config/topics-generated.yaml 2>/dev/null || true
fi
if git diff --cached --quiet; then
  echo "  ledger unchanged"
  exit 0
fi

if ! git "${IDENT[@]}" commit -q -m "chore: record published reel [skip ci]"; then
  echo "record_ledger: commit failed" >&2
  exit 1
fi

# HEAD:$BRANCH works on a detached HEAD. Plain `git push` does not, and that is
# the difference between a recorded reel and a duplicate one.
for attempt in 1 2 3; do
  if git push origin "HEAD:${BRANCH}" >/dev/null 2>&1; then
    echo "  ledger pushed (attempt $attempt)"
    exit 0
  fi
  echo "  push attempt $attempt failed; rebasing on origin/${BRANCH}" >&2
  git pull --rebase --autostash origin "$BRANCH" >/dev/null 2>&1 || true
  sleep $((attempt * 5))
done

echo "record_ledger: ledger NOT recorded after 3 attempts." >&2
echo "  Add the topic_id to state/published.json by hand before the next run," >&2
echo "  or that run will upload it again." >&2
exit 1
