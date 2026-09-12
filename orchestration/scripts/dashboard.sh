#!/usr/bin/env bash
# dashboard.sh — start the read-only Cauce board monitor. Run this on ONE
# machine (whoever wants to watch the board live); it never writes to git
# and doesn't interfere with anyone's in-progress work.
#
# Usage:
#   ./orchestration/scripts/dashboard.sh              # serves http://localhost:8000
#   PORT=8080 ./orchestration/scripts/dashboard.sh    # different port
set -euo pipefail

MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1{sub(/^worktree /,""); print; exit}')"
cd "$MAIN_ROOT/orchestration"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export CAUCE_REPO_URL="${CAUCE_REPO_URL:-$(git remote get-url origin 2>/dev/null || echo "$MAIN_ROOT")}"
PORT="${PORT:-8000}"

echo "Mirroring: $CAUCE_REPO_URL"
echo "Dashboard: http://localhost:$PORT"

cd boss-dashboard
exec uvicorn app:app --host 0.0.0.0 --port "$PORT"
