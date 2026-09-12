#!/usr/bin/env bash
# work.sh — claim the next eligible task and drop straight into its
# worktree running the agent CLI, in one command.
#
# Usage:
#   ./orchestration/scripts/work.sh                # owner = `git config user.name`, launches `claude`
#   ./orchestration/scripts/work.sh Paul           # explicit owner, launches `claude`
#   ./orchestration/scripts/work.sh Paul cursor    # explicit owner, launches `cursor` instead
#   ./orchestration/scripts/work.sh '' cursor      # default owner, launches `cursor`
#
# Always runs claim_task.py from orchestration/ inside the main checkout
# (not from inside some other worktree you happen to be sitting in), then
# cd's into the new worktree (created as a sibling of the repo, per
# claim_task.py) and execs the agent there. When the agent exits, your
# shell lands back wherever it started.
set -euo pipefail

OWNER="${1:-}"
AGENT="${2:-claude}"

MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1{sub(/^worktree /,""); print; exit}')"
cd "$MAIN_ROOT/orchestration"

if [ -d ".venv" ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

if [ -z "$OWNER" ]; then
  OWNER="$(git config user.name || true)"
fi
if [ -z "$OWNER" ]; then
  echo "error: no owner given and \`git config user.name\` is empty. Run ./orchestration/scripts/setup.sh first, or pass a name: ./orchestration/scripts/work.sh <name>" >&2
  exit 1
fi

if ! OUTPUT="$(python scripts/claim_task.py --owner "$OWNER" 2>&1)"; then
  echo "$OUTPUT" >&2
  exit 1
fi
echo "$OUTPUT"

WORKTREE="$(echo "$OUTPUT" | sed -n 's/^ *worktree: *//p')"
if [ -z "$WORKTREE" ] || [ ! -d "$WORKTREE" ]; then
  echo "error: could not find the worktree path in claim_task.py's output" >&2
  exit 1
fi

if ! command -v "$AGENT" >/dev/null 2>&1; then
  echo ""
  echo "warning: '$AGENT' is not on PATH. Claimed the task and created the worktree at:"
  echo "  $WORKTREE"
  echo "cd there and launch your agent manually."
  exit 0
fi

echo ""
echo "Launching $AGENT in $WORKTREE ..."
cd "$WORKTREE"
exec "$AGENT"
