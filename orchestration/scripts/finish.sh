#!/usr/bin/env bash
# finish.sh — mark a claimed task done, from wherever you happen to be
# (your own worktree included). Always runs finish_task.py from
# orchestration/ inside the main checkout, per its own requirement (it
# needs to update tasks.yaml on main).
#
# Usage:
#   ./orchestration/scripts/finish.sh T001
set -euo pipefail

TASK_ID="${1:?usage: ./orchestration/scripts/finish.sh <task-id>}"

MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1{sub(/^worktree /,""); print; exit}')"
cd "$MAIN_ROOT/orchestration"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python scripts/finish_task.py --task-id "$TASK_ID"
