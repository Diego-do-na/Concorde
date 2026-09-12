#!/usr/bin/env bash
# work.sh — claim the next eligible task and drop straight into its
# worktree running the agent CLI, in one command.
#
# Usage:
#   ./orchestration/scripts/work.sh                # owner = `git config user.name`, launches `claude`
#   ./orchestration/scripts/work.sh Paul           # explicit owner, launches `claude`
#   ./orchestration/scripts/work.sh Paul cursor-agent    # explicit owner, launches `cursor-agent` instead
#   ./orchestration/scripts/work.sh '' cursor-agent      # default owner, launches `cursor-agent`
#
# Always runs claim_task.py from orchestration/ inside the main checkout
# (not from inside some other worktree you happen to be sitting in), then
# cd's into the new worktree (created as a sibling of the repo, per
# claim_task.py) and execs the agent there. When the agent exits, your
# shell lands back wherever it started.
#
# Model selection: the claimed task's suggested_model (from tasks.yaml,
# already printed by claim_task.py on its own line) is passed to the
# agent via --model, resolved per-agent by resolve_model_flag() below.
#
# Initial prompt: the claimed task's title+description is passed as the
# agent's initial prompt (a trailing positional argument -- confirmed this
# keeps the session fully interactive and still gates every action on a
# real permission prompt, it's just no longer sitting at an empty input
# box). Without this, the agent opens with nothing to do and you'd have to
# find and paste the task description yourself.
set -euo pipefail

OWNER="${1:-}"
AGENT="${2:-claude}"

# resolve_model_flag AGENT SUGGESTED_MODEL -> echoes the --model value to
# launch with (possibly empty, meaning: no --model flag, agent's own
# default).
#
# claude: suggested_model values pass straight through as Claude Code's
# own --model aliases (haiku/sonnet/opus/fable) -- the alias itself was
# confirmed valid via a separate one-off headless check (`-p`), OUTSIDE
# this script; the actual launch below stays fully interactive, --model
# is the only thing added to it. Empty/unset falls back to "haiku": most
# Cauce tasks are mechanical and don't need a bigger model; tasks that do
# should set suggested_model explicitly.
#
# cursor-agent: uses a completely different model catalog from Claude
# (confirmed via `cursor-agent --list-models` once authenticated -- 200+
# ids like "gpt-5.4-nano-low", "claude-sonnet-5-high", "gemini-3.7-flash-low").
# Claude-style suggested_model values (haiku/sonnet/opus/fable) are NEVER
# forwarded to cursor-agent -- they're not valid ids in its catalog and
# could break the session outright. "gpt-5-mini" is used as the cheap
# default instead (confirmed working via `cursor-agent --model gpt-5-mini
# -p ...`), independent of whatever suggested_model says. Override via
# CAUCE_CURSOR_CHEAP_MODEL if a cheaper/better default is found later.
CAUCE_CURSOR_CHEAP_MODEL="${CAUCE_CURSOR_CHEAP_MODEL:-gpt-5-mini}"

resolve_model_flag() {
  local agent="$1" suggested="$2"
  case "$agent" in
    claude)
      echo "${suggested:-haiku}"
      ;;
    cursor-agent|cursor)
      echo "$CAUCE_CURSOR_CHEAP_MODEL"
      ;;
    *)
      echo "$suggested"
      ;;
  esac
}

# build_task_prompt TASK_ID -> echoes "title\n\ndescription" for the given
# task, straight from tasks.yaml (not by re-parsing claim_task.py's
# human-formatted output, which can span multiple lines for the
# description and isn't safe to sed out reliably). Empty output (and no
# error) if anything goes wrong -- the caller just launches without an
# initial prompt in that case, same as the old behavior.
build_task_prompt() {
  local task_id="$1"
  TASK_ID_ENV="$task_id" python3 - <<'PY'
import os, sys
sys.path.insert(0, "scripts")
from common import load_tasks, find_task
task_id = os.environ["TASK_ID_ENV"]
data = load_tasks()
task = find_task(data, task_id)
if task:
    print(f"{task['title']}\n\n{task['description']}")
PY
}

# sync_worktree_with_main WORKTREE -> rebases that worktree's branch onto
# the latest origin/main before the agent starts. Always run this right
# before launching, never skipped: finish_task.py now merges finished
# tasks straight into main, so any task whose worktree was created (or
# last synced) before a dependency finished needs this to actually see
# that code -- tasks.yaml saying a dependency is "done" isn't enough on
# its own, the files have to be pulled in too. Non-overlapping scopes
# make a real conflict here unlikely, but if one happens this stops and
# tells you to resolve it by hand rather than launching the agent on top
# of a half-rebased worktree.
sync_worktree_with_main() {
  local worktree="$1"
  python scripts/locked_git.py -C "$worktree" fetch origin --quiet && python scripts/locked_git.py -C "$worktree" rebase origin/main --quiet
}

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
SUGGESTED_MODEL="$(echo "$OUTPUT" | sed -n 's/^ *suggested_model: *//p')"
TASK_ID="$(echo "$OUTPUT" | sed -n 's/^Claimed \([A-Za-z0-9_-]*\):.*/\1/p')"
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

echo "Syncing worktree with the latest main..."
if ! sync_worktree_with_main "$WORKTREE"; then
  echo "error: could not rebase $WORKTREE onto origin/main (conflict?). Resolve by hand:" >&2
  echo "  cd $WORKTREE && git status" >&2
  echo "then launch the agent yourself once it's clean." >&2
  exit 1
fi

MODEL_VALUE="$(resolve_model_flag "$AGENT" "$SUGGESTED_MODEL")"
TASK_PROMPT="$(build_task_prompt "$TASK_ID" 2>/dev/null || true)"

AGENT_ARGS=("$AGENT")
if [ -n "$MODEL_VALUE" ]; then
  AGENT_ARGS+=(--model "$MODEL_VALUE")
fi
if [ -n "$TASK_PROMPT" ]; then
  AGENT_ARGS+=("$TASK_PROMPT")
fi

echo ""
echo "Launching ${AGENT_ARGS[0]} (model: ${MODEL_VALUE:-agent default}) in $WORKTREE ..."
if [ -z "$TASK_PROMPT" ]; then
  echo "warning: could not load the task prompt from tasks.yaml; the agent will open empty."
fi
echo "Tip: when the session ends, run 'python orchestration/scripts/task_log.py $TASK_ID'"
echo "     before finish_task.py to review what the agent actually did."
cd "$WORKTREE"
exec "${AGENT_ARGS[@]}"
