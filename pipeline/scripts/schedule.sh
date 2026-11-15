#!/usr/bin/env bash
# Daily scheduling via launchd.
#   ./scripts/schedule.sh install [HH:MM]
#   ./scripts/schedule.sh remove
#   ./scripts/schedule.sh run        # run once now, same command launchd uses
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.reelforge.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$ROOT/work/cron.log"

case "${1:-}" in
  install)
    TIME="${2:-09:00}"
    HOUR="${TIME%%:*}"; MIN="${TIME##*:}"
    mkdir -p "$ROOT/work" "$(dirname "$PLIST")"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string>
    <string>-i</string>
    <string>$ROOT/.venv/bin/reelforge</string>
    <string>run</string>
    <string>--count</string><string>1</string>
    <string>--publish</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>$HOUR</integer><key>Minute</key><integer>$MIN</integer></dict>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><false/>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "-> installed: daily at $TIME, logging to $LOG"
    ;;
  remove)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "-> removed"
    ;;
  run)
    "$ROOT/.venv/bin/reelforge" run --count 1 --publish
    ;;
  *)
    echo "usage: $0 {install [HH:MM]|remove|run}" >&2
    exit 2
    ;;
esac
