#!/usr/bin/env bash
# The unattended daily job. Safe to call as often as you like.
#
# Idempotent: publishes at most once per calendar day. That is what makes an
# interval-based schedule safe on a laptop - launchd does not fire a
# StartCalendarInterval job if the machine is asleep at that moment, so a 09:00
# job silently skips the day. Running every couple of hours and exiting when the
# day is already done means the run happens on the next wake instead of never.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="$ROOT/work/cron.log"
STAMP="$ROOT/work/.last_publish_date"
mkdir -p "$ROOT/work"

exec >>"$LOG" 2>&1
ts() { date '+%F %T'; }
notify() { /usr/bin/osascript -e "display notification \"$2\" with title \"$1\"" >/dev/null 2>&1 || true; }

echo
echo "=== $(ts) run start ==="

TODAY="$(date +%F)"
if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$TODAY" ]; then
  echo "already published today ($TODAY) - exiting"
  exit 0
fi

cd "$ROOT" || { echo "cannot cd to $ROOT"; exit 1; }

# Refuse early, and loudly, rather than burning 15 minutes to discover it.
topics_left() {
  PYTHONPATH="$ROOT/src" "$ROOT/.venv/bin/python" - <<'PY'
import sys
from reelforge import config, state
c = config.load(); s = state.Store(c.db)
used = {r["topic_id"] for r in s.conn.execute("SELECT topic_id FROM topics_used")}
sys.exit(0 if [t for t in config.load_topics() if t["id"] not in used] else 3)
PY
}
if ! topics_left; then
  echo "NO TOPICS LEFT in config/topics.yaml"
  notify "Gita & Grit" "Out of topics - add more to config/topics.yaml"
  exit 3
fi

./reelforge run --count 1 --publish
rc=$?

if [ "$rc" -eq 0 ]; then
  echo "$TODAY" > "$STAMP"
  echo "success at $(ts)"
else
  echo "FAILED rc=$rc at $(ts)"
  notify "Gita & Grit" "Daily run failed (rc=$rc) - see work/cron.log"
fi
exit "$rc"
