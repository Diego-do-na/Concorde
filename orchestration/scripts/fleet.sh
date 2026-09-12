#!/usr/bin/env bash
# fleet.sh — run one autopilot.sh loop per agent CLI, in parallel, each
# claiming and finishing tasks independently against the same tasks.yaml.
# When one agent finishes its task before the other, it grabs the next
# eligible one right away instead of waiting on its sibling.
#
# This is safe by construction, not by luck: Cauce's scope-conflict check
# (on every claim) and its optimistic push-with-retry (on every tasks.yaml
# write) already handle concurrent claims correctly, whether from 2 agents
# on one machine or 3 people across 3 machines. What keeps it collision-free
# is tasks.yaml itself being designed with non-overlapping scopes /
# dependency chains — see AGENTS.md's Coordination rules section.
#
# Each agent gets its own Cauce "owner" identity (<owner>-<agent>) so the
# two loops each track their OWN in-flight claim and never mistake the
# other's claimed task for their own.
#
# Usage:
#   ./orchestration/scripts/fleet.sh                          # auto-detects claude/cursor-agent on PATH
#   ./orchestration/scripts/fleet.sh Diego                     # explicit base owner, auto-detect agents
#   ./orchestration/scripts/fleet.sh Diego claude cursor-agent # explicit owner + explicit agent list
#
# On macOS this opens one Terminal.app window per agent so each stays
# fully interactive (you still approve every action, same as running
# autopilot.sh by hand — nothing here bypasses permission prompts). On any
# other OS it just prints the exact commands for you to run yourself in
# separate terminals.
set -euo pipefail

BASE_OWNER="${1:-}"
if [ "$#" -gt 0 ]; then shift; fi
AGENTS=("$@")

MAIN_ROOT="$(git worktree list --porcelain | awk 'NR==1{sub(/^worktree /,""); print; exit}')"

if [ -z "$BASE_OWNER" ]; then
  BASE_OWNER="$(git -C "$MAIN_ROOT" config user.name || true)"
fi
if [ -z "$BASE_OWNER" ]; then
  echo "error: no owner given and \`git config user.name\` is empty. Run ./orchestration/scripts/setup.sh first." >&2
  exit 1
fi

if [ "${#AGENTS[@]}" -eq 0 ]; then
  # The real binary is `cursor-agent`, not `cursor` -- `which cursor`
  # doesn't resolve even when Cursor CLI is installed and authenticated.
  for candidate in claude cursor-agent; do
    if command -v "$candidate" >/dev/null 2>&1; then
      AGENTS+=("$candidate")
    fi
  done
fi

if [ "${#AGENTS[@]}" -eq 0 ]; then
  echo "error: neither 'claude' nor 'cursor-agent' found on PATH, and none given explicitly." >&2
  exit 1
fi

# Drop anything explicitly requested that isn't actually installed, rather
# than opening a terminal window that just fails.
CHECKED_AGENTS=()
for agent in "${AGENTS[@]}"; do
  if command -v "$agent" >/dev/null 2>&1; then
    CHECKED_AGENTS+=("$agent")
  else
    echo "warning: '$agent' not found on PATH, skipping it." >&2
  fi
done
AGENTS=("${CHECKED_AGENTS[@]}")

if [ "${#AGENTS[@]}" -eq 0 ]; then
  echo "error: none of the requested agents are installed." >&2
  exit 1
fi

if [ "${#AGENTS[@]}" -eq 1 ]; then
  echo "Only one agent CLI available (${AGENTS[0]}) — running a single autopilot loop, nothing to parallelize."
  exec "$MAIN_ROOT/orchestration/scripts/autopilot.sh" "${BASE_OWNER}-${AGENTS[0]}" "${AGENTS[0]}"
fi

echo "Starting one autopilot loop per agent: ${AGENTS[*]}"
echo "Each gets its own Cauce owner identity so they never confuse each other's claim:"
for agent in "${AGENTS[@]}"; do
  echo "  - ${BASE_OWNER}-${agent}  ->  $agent"
done
echo ""

if [ "$(uname -s)" = "Darwin" ] && command -v osascript >/dev/null 2>&1; then
  for agent in "${AGENTS[@]}"; do
    WORKER_OWNER="${BASE_OWNER}-${agent}"
    # No ".sh" suffix on the template: BSD mktemp (macOS) only substitutes
    # a trailing run of X's -- anything after them (like ".sh") stops the
    # substitution and it creates the literal, unrandomized filename
    # instead, which then collides on every subsequent call.
    LAUNCHER="$(mktemp /tmp/cauce-fleet-XXXXXX)"
    cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
cd "$MAIN_ROOT"
exec "$MAIN_ROOT/orchestration/scripts/autopilot.sh" "$WORKER_OWNER" "$agent"
EOF
    chmod +x "$LAUNCHER"
    osascript -e "tell application \"Terminal\" to do script \"$LAUNCHER\"" >/dev/null
    echo "Opened a Terminal window for $agent (owner: $WORKER_OWNER)"
  done
  echo ""
  echo "Each window runs its own autopilot loop. Approve actions in each as usual;"
  echo "when a window's session ends, answer its [f]inish/[r]eopen/[q]uit prompt there."
else
  echo "Not on macOS (or osascript unavailable) — open one terminal per line below and run each yourself:"
  echo ""
  for agent in "${AGENTS[@]}"; do
    WORKER_OWNER="${BASE_OWNER}-${agent}"
    echo "  cd \"$MAIN_ROOT\" && ./orchestration/scripts/autopilot.sh \"$WORKER_OWNER\" \"$agent\""
  done
fi
