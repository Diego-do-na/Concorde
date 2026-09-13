#!/usr/bin/env bash
set -euo pipefail
# Deploy the React console (product/console) to the server
# Usage:
#  ./deploy-console.sh [HOST] [SSH_PORT] [REMOTE_USER]
# Defaults: HOST=100.93.147.55 PORT=2222 USER=root

HOST="${1:-100.93.147.55}"
PORT="${2:-2222}"
USER="${3:-root}"

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CONSOLE_DIR="$ROOT_DIR/product/console"
DIST_DIR="$CONSOLE_DIR/dist"

if [ ! -d "$CONSOLE_DIR" ]; then
  echo "Console directory not found: $CONSOLE_DIR" >&2
  exit 1
fi

echo "Building console (VITE_API_BASE empty = same-origin; '/' would yield scheme-relative //health URLs)" 
cd "$CONSOLE_DIR"
export VITE_API_BASE=""
npm ci
npm run build

if [ ! -d "$DIST_DIR" ]; then
  echo "Build did not produce dist/ directory" >&2
  exit 1
fi

echo "Syncing $DIST_DIR -> ${USER}@${HOST}:/opt/concorde/console"
rsync -av --delete -e "ssh -p ${PORT}" "$DIST_DIR"/ "${USER}@${HOST}:/opt/concorde/console/"

echo "Reloading Caddy on remote host"
ssh -p "${PORT}" "${USER}@${HOST}" 'systemctl reload caddy || true'

echo "Done. Console deployed at https://${HOST}/ (served by Caddy)"

