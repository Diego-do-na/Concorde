#!/usr/bin/env bash
set -euo pipefail
# Replay N validation clips by POSTing them to the /detect endpoint on the server.
# Usage:
#   ./replay_val.sh /path/to/val_dataset HOST [N]
# Example:
#   ./replay_val.sh ~/datasets/concorde/val getconcorde.tech 20

DATASET_DIR="${1:-}"
HOST="${2:-getconcorde.tech}"
N="${3:-20}"

if [ -z "$DATASET_DIR" ]; then
  echo "Usage: $0 /path/to/val_dataset HOST [N]" >&2
  exit 2
fi

if [ ! -d "$DATASET_DIR" ]; then
  echo "Dataset dir not found: $DATASET_DIR" >&2
  exit 1
fi

echo "Replaying up to $N clips from $DATASET_DIR -> https://${HOST}/detect"

count=0
shopt -s nullglob
for f in "$DATASET_DIR"/*.{wav,WAV}; do
  if [ "$count" -ge "$N" ]; then
    break
  fi
  if [ ! -f "$f" ]; then
    continue
  fi
  echo "[$((count+1))] POST $f"
  # Send as multipart/form-data file upload; API accepts many shapes per ADR-009
  curl -sS -w "\nHTTP_CODE:%{http_code}\n" -X POST "https://${HOST}/detect" \
    -F "file=@${f}" \
    -H "Accept: application/json" || true
  count=$((count+1))
  sleep 0.25
done

echo "Replayed $count clips."

