"""
poller.py — a per-person convenience loop, not a server.

Every N seconds (default 30): pull, look for a task that's ready to claim
and doesn't collide with any in-flight scope, and if you don't already
have a claim in progress, claim it for you automatically. It never starts
Claude Code or Cursor itself — a human still reads the "TASK READY"
announcement and starts the agent by hand in the printed worktree.

Usage:
    python orchestration/scripts/poller.py [--interval 30] [--owner "Name"]

Nothing here needs to run 24/7 on a shared machine — each teammate runs
their own poller (or doesn't, and just runs claim_task.py by hand).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import time

from common import (
    CauceError,
    ORCHESTRATION_ROOT,
    assert_main_checkout,
    dependencies_satisfied,
    find_scope_conflict,
    get_git_user_name,
    load_tasks,
    local_repo_lock,
    run_git,
    tasks_by_id,
)


def _has_ready_candidate(owner: str) -> bool:
    """True if there's a todo task with satisfied deps and no scope
    collision with anything currently claimed, and `owner` doesn't
    already have a claim in progress (so the poller doesn't pile up
    multiple in-flight tasks per person before they finish the current one)."""
    data = load_tasks()
    by_id = tasks_by_id(data)
    tasks = data.get("tasks", [])

    if any(t.get("status") == "claimed" and t.get("owner") == owner for t in tasks):
        return False

    claimed = [t for t in tasks if t.get("status") == "claimed"]
    for task in tasks:
        if task.get("status") != "todo":
            continue
        if not dependencies_satisfied(task, by_id):
            continue
        if find_scope_conflict(task.get("scope") or [], claimed) is not None:
            continue
        return True
    return False


def _announce(title: str, task_id: str, description: str) -> None:
    sys.stdout.write("\a")  # terminal bell
    print(f"TASK READY: {title.upper()} IN ../TASK-{task_id.upper()}, PROMPT: {description.upper()}")
    sys.stdout.flush()


def run_once(owner: str) -> None:
    try:
        # Locked so this read-before-decide doesn't race another local
        # process's own locked claim/finish critical section (e.g. a
        # second poller, or a manual claim_task.py, sharing this checkout).
        with local_repo_lock():
            run_git(["pull", "--quiet"])
    except CauceError as exc:
        print(f"warning: git pull failed this cycle ({exc}); will retry next interval", file=sys.stderr)
        return

    try:
        if not _has_ready_candidate(owner):
            return
    except CauceError as exc:
        print(f"warning: could not read tasks.yaml this cycle ({exc})", file=sys.stderr)
        return

    claim_script = ORCHESTRATION_ROOT / "scripts" / "claim_task.py"
    result = subprocess.run(
        [sys.executable, str(claim_script), "--owner", owner],
        cwd=ORCHESTRATION_ROOT,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Most likely: someone else claimed it between our check and now.
        # Not an error worth alarming over — just wait for the next cycle.
        print(f"(poller) claim attempt did not land this cycle: {result.stderr.strip() or result.stdout.strip()}")
        return

    print(result.stdout)
    data = load_tasks()
    claimed = next((t for t in data.get("tasks", []) if t.get("owner") == owner and t.get("status") == "claimed"), None)
    if claimed:
        _announce(claimed["title"], claimed["id"], claimed["description"])


def main() -> None:
    parser = argparse.ArgumentParser(description="Poll for and auto-claim the next eligible Cauce task.")
    parser.add_argument("--interval", type=int, default=30, help="Seconds between checks (default: 30).")
    parser.add_argument("--owner", default=None, help="Defaults to `git config user.name`.")
    args = parser.parse_args()

    owner = args.owner or get_git_user_name()

    try:
        assert_main_checkout()
    except CauceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"Cauce poller started for {owner!r} (interval={args.interval}s). Ctrl+C to stop.")

    try:
        while True:
            run_once(owner)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopping poller.")
        sys.exit(0)


if __name__ == "__main__":
    main()
