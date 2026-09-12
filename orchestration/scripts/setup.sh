#!/usr/bin/env bash
# setup.sh — one-time setup, run once per machine (Paul, Nestor, Diego each
# run this once after cloning the repo).
#
# Usage:
#   ./orchestration/scripts/setup.sh
set -euo pipefail

# This script lives at <repo>/orchestration/scripts/setup.sh — everything
# Cauce-only (venv, deps, task board) is kept self-contained under
# orchestration/, out of the way of api/, ml/, console/ etc.
ORCH_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ORCH_ROOT"

if [ ! -d ".venv" ]; then
  echo "Creating virtualenv (orchestration/.venv)..."
  python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt

NAME="$(git config user.name || true)"
if [ -z "$NAME" ]; then
  read -r -p "Your name (used as the Cauce task owner, e.g. Paul/Nestor/Diego): " NAME
  git config user.name "$NAME"
fi

echo ""
echo "Setup complete for '$NAME'."
echo ""
echo "Next steps:"
echo "  ./orchestration/scripts/work.sh                # claim the next task and launch Claude Code"
echo "  ./orchestration/scripts/work.sh '' cursor-agent  # ...or launch Cursor instead"
echo "  ./orchestration/scripts/finish.sh <task-id>    # mark a task done once its worktree's work is pushed"
echo "  ./orchestration/scripts/dashboard.sh           # (one person only) run the shared board monitor"
