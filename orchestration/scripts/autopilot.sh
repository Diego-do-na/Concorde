#!/usr/bin/env bash
# autopilot.sh — the loop: keep claiming the next eligible task, drop you
# into its worktree with the agent running interactively (normal
# permission prompts, exactly like running it by hand), and when the
# agent's session ends, STOP and ask YOU to confirm the work actually
# matches this task's declared scope before finishing it and moving on.
#
# This never bypasses permission prompts (no --dangerously-skip-permissions)
# and never marks a task done without your explicit confirmation here — the
# only thing automated is the claim -> launch -> (your review) -> finish ->
# claim-next cycle, so you only have to invoke this one script.
#
# If a task turns out to need work outside its declared scope, don't let
# the agent touch those files — say no when it asks, then either extend
# the task's Definition of Done to stay within its own files, or create a
# proper follow-up task with `add_task.py` (depends_on this one) instead.
# That's what keeps scopes non-overlapping across parallel claims.
#
# Model selection: the claimed task's suggested_model (from tasks.yaml) is
# passed to the agent via --model, resolved per-agent by
# resolve_model_flag() below -- see its comment for what's verified vs. not.
#
# Initial prompt: the claimed task's title+description is passed as the
# agent's initial prompt (a trailing positional argument -- confirmed this
# keeps the session fully interactive and still gates every action on a
# real permission prompt, it's just no longer sitting at an empty input
# box). Re-sent on [r]eopen too, since each reopened session starts fresh
# with no memory of the earlier one.
#
# Before the finish/reopen/quit prompt, prints a deterministic summary of
# the session (task_log.py, no model call) so you don't have to scroll
# back through the whole conversation to decide.
#
# Usage:
#   ./orchestration/scripts/autopilot.sh                # owner = `git config user.name`, agent = claude
#   ./orchestration/scripts/autopilot.sh Paul            # explicit owner
#   ./orchestration/scripts/autopilot.sh Paul cursor-agent  # explicit owner + agent
set -euo pipefail

OWNER="${1:-}"
AGENT="${2:-claude}"

# See work.sh for the full rationale of this mapping (kept in sync there).
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
  echo "error: no owner given and \`git config user.name\` is empty. Run ./orchestration/scripts/setup.sh first, or pass a name: ./orchestration/scripts/autopilot.sh <name>" >&2
  exit 1
fi

if ! command -v "$AGENT" >/dev/null 2>&1; then
  echo "error: '$AGENT' is not on PATH." >&2
  exit 1
fi

find_in_flight_claim() {
  # Prints two lines if found: the task id, then its suggested_model
  # (possibly empty) -- nothing at all if this owner has no claim.
  OWNER_ENV="$OWNER" python3 - <<'PY'
import os, sys
sys.path.insert(0, "scripts")
from common import load_tasks
owner = os.environ["OWNER_ENV"]
data = load_tasks()
for t in data.get("tasks", []):
    if t.get("status") == "claimed" and t.get("owner") == owner:
        print(t["id"])
        print(t.get("suggested_model") or "")
        break
PY
}

# build_task_prompt TASK_ID -> echoes "title\n\ndescription" for the given
# task, straight from tasks.yaml. Empty output (no error) if anything goes
# wrong -- the caller just launches without an initial prompt then, same
# as the old behavior.
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

echo "Cauce autopilot started for '$OWNER' (agent: $AGENT)."
echo "Ctrl+C at any point leaves the current task exactly as it is."
echo ""

while true; do
  if ! git pull --quiet; then
    echo "warning: git pull failed, retrying in 10s..." >&2
    sleep 10
    continue
  fi

  IN_FLIGHT="$(find_in_flight_claim || true)"

  if [ -n "$IN_FLIGHT" ]; then
    TASK_ID="$(echo "$IN_FLIGHT" | sed -n '1p')"
    SUGGESTED_MODEL="$(echo "$IN_FLIGHT" | sed -n '2p')"
    WORKTREE="$(dirname "$MAIN_ROOT")/task-$TASK_ID"
    echo "Resuming your already-claimed task $TASK_ID at $WORKTREE"
  else
    echo "Looking for the next eligible task for '$OWNER'..."
    if ! OUTPUT="$(python scripts/claim_task.py --owner "$OWNER" 2>&1)"; then
      echo "Nothing claimable right now:"
      echo "$OUTPUT"
      echo "Retrying in 30s... (Ctrl+C to stop)"
      sleep 30
      continue
    fi
    echo "$OUTPUT"
    TASK_ID="$(echo "$OUTPUT" | sed -n 's/^Claimed \([A-Za-z0-9_-]*\):.*/\1/p')"
    WORKTREE="$(echo "$OUTPUT" | sed -n 's/^ *worktree: *//p')"
    SUGGESTED_MODEL="$(echo "$OUTPUT" | sed -n 's/^ *suggested_model: *//p')"
  fi

  if [ -z "$TASK_ID" ] || [ -z "$WORKTREE" ] || [ ! -d "$WORKTREE" ]; then
    echo "error: could not resolve a task id / worktree from claim_task.py's output, stopping." >&2
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
  else
    echo "warning: could not load the task prompt from tasks.yaml; the agent will open empty." >&2
  fi

  while true; do
    echo ""
    echo "=================================================================="
    echo " $TASK_ID -- launching ${AGENT_ARGS[0]} (model: ${MODEL_VALUE:-agent default}) in $WORKTREE"
    echo " Normal permission prompts apply. Approve/deny each action as usual."
    echo "=================================================================="
    echo ""

    ( cd "$WORKTREE" && "${AGENT_ARGS[@]}" ) || true   # agent's own exit code never stops the loop

    echo ""
    echo "------------------------------------------------------------------"
    echo " ${AGENT_ARGS[0]} session for $TASK_ID ended. Session summary:"
    echo ""
    python scripts/task_log.py "$TASK_ID" 2>&1 | sed 's/^/  /' || true
    echo ""
    echo " Before finishing: does the above stay inside this task's declared scope?"
    echo "   cd $WORKTREE && git status && git diff --stat"
    echo "------------------------------------------------------------------"
    read -r -p "[f]inish & push, [r]eopen the agent here, [q]uit autopilot (leaves $TASK_ID claimed): " CHOICE

    case "$CHOICE" in
      f|F)
        if python scripts/finish_task.py --task-id "$TASK_ID"; then
          echo "Finished $TASK_ID. Looking for the next task..."
        else
          echo "finish_task.py reported a problem (see above) — $TASK_ID is left claimed, not finished."
        fi
        break
        ;;
      r|R)
        continue
        ;;
      q|Q)
        echo "Stopping autopilot. $TASK_ID stays claimed — resume it later by re-running this script, or by cd-ing into $WORKTREE yourself."
        exit 0
        ;;
      *)
        echo "please answer f, r, or q"
        ;;
    esac
  done
done
