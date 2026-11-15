#!/usr/bin/env bash
# Install/remove the unattended job.
#
#   ./scripts/schedule.sh install        # every 2 hours, publishes once a day
#   ./scripts/schedule.sh remove
#   ./scripts/schedule.sh run            # run once now, exactly as launchd would
#   ./scripts/schedule.sh status
#
# Interval, not calendar: a StartCalendarInterval job that fires while the Mac is
# asleep is simply skipped, which looks identical to "the pipeline broke". The job
# is idempotent (scripts/daily.sh), so polling is harmless and self-healing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.reelforge.daily"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$ROOT/work/cron.log"
INTERVAL="${2:-7200}"   # seconds between checks

case "${1:-}" in
  install)
    mkdir -p "$ROOT/work" "$(dirname "$PLIST")"
    chmod +x "$ROOT/scripts/daily.sh"
    cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$ROOT/scripts/daily.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
  <key>RunAtLoad</key><true/>
  <key>LowPriorityIO</key><true/>
</dict>
</plist>
EOF
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "-> installed: checks every $((INTERVAL/60)) min, publishes at most once a day"
    echo "   log: $LOG"
    ;;
  remove)
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    echo "-> removed"
    ;;
  run)
    "$ROOT/scripts/daily.sh"
    ;;
  status)
    if launchctl list | grep -q "$LABEL"; then
      echo "-> loaded"; launchctl list | grep "$LABEL"
    else
      echo "-> not loaded"
    fi
    [ -f "$ROOT/work/.last_publish_date" ] && echo "last published: $(cat "$ROOT/work/.last_publish_date")" || echo "last published: never"
    ;;
  *)
    echo "usage: $0 {install [interval_seconds]|remove|run|status}" >&2
    exit 2
    ;;
esac
