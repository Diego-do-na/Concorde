#!/usr/bin/env bash
# Simple health watcher: polls a URL every 15s, beeps + prints when non-200.
# Usage: watch_health.sh <url|host[:port]> [duration_seconds]
# Examples:
#   ./watch_health.sh http://1.2.3.4:8080/health 120   # run 2 minutes
#   ./watch_health.sh 1.2.3.4:8080 60                 # will prepend http:// if no scheme

set -euo pipefail
URL="$1"
DURATION="${2:-0}"

# Normalize URL: if it looks like host:port, add http:// and /health if missing
if [[ ! "$URL" =~ ^https?:// ]]; then
  # if it contains '/', assume path present; else append /health
  if [[ "$URL" =~ / ]]; then
    URL="http://$URL"
  else
    URL="http://$URL/health"
  fi
fi

INTERVAL=15
ELAPSED=0
EXIT_CODE=0

echo "Watching health: $URL (every ${INTERVAL}s)${DURATION:+ for ${DURATION}s}"
while true; do
  if curl -s -f "$URL" > /dev/null 2>&1; then
    printf "%s: OK\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  else
    # audible beep and message for operator
    printf "\a"    # terminal bell
    printf "%s: NON-200 from %s\n" "$(date -u +"%Y-%m-%dT%H:%M:%SZ")" "$URL"
    EXIT_CODE=2
  fi

  if [ "$DURATION" -ne 0 ]; then
    ELAPSED=$((ELAPSED + INTERVAL))
    if [ "$ELAPSED" -ge "$DURATION" ]; then
      echo "Completed ${DURATION}s watch; exiting."
      break
    fi
  fi
  sleep "$INTERVAL"
done

exit $EXIT_CODE

