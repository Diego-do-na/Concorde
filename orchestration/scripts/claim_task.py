"""
claim_task.py — pick the next eligible task, claim it, and set you up a
worktree to do the work in.

Usage:
    python orchestration/scripts/claim_task.py [--owner "Name"]

Algorithm:
    1. git pull.
    2. Among tasks with status=todo whose depends_on are all done, take
       the first one (file order).
    3. Make sure its `scope` doesn't overlap the scope of any task that is
       currently status=claimed. If it does, abort with a clear error —
       we do NOT silently skip to the next candidate, since scope
       collisions usually mean the task list itself needs a human to
       re-scope it.
    4. Set owner/status/claimed_at and push. If the push is rejected
       because someone else claimed first, pull --rebase and restart the
       whole search from scratch (up to 5 attempts) — the "first eligible
       task" may have changed.
    5. On success, create a worktree at ../task-<id> on a new branch
       task/<id>, print the task details, and notify Discord.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from common import (
    CauceError,
    REPO_ROOT,
    assert_main_checkout,
    dependencies_satisfied,
    fail,
    find_scope_conflict,
    get_git_user_name,
    now_iso,
    push_tasks_with_retry,
    run_git,
    tasks_by_id,
)
from notify_discord import notify


def _find_eligible_task(data: dict[str, Any]) -> dict[str, Any] | None:
    by_id = tasks_by_id(data)
    for task in data.get("tasks", []):
        if task.get("status") == "todo" and dependencies_satisfied(task, by_id):
            return task
    return None


def claim_next_task(owner: str, max_attempts: int = 5) -> dict[str, Any]:
    """Runs the full claim flow and returns the claimed task dict (as it
    ended up committed). Raises CauceError on any unrecoverable failure,
    including "nothing to claim" and "scope conflict"."""
    claimed_holder: dict[str, Any] = {}

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        candidate = _find_eligible_task(data)
        if candidate is None:
            raise CauceError("no eligible task: nothing is status=todo with all dependencies done")

        claimed_tasks = [t for t in data.get("tasks", []) if t.get("status") == "claimed"]
        conflict = find_scope_conflict(candidate.get("scope") or [], claimed_tasks)
        if conflict is not None:
            raise CauceError(
                f"task {candidate['id']} ({candidate['title']!r}) has a scope "
                f"conflict with currently-claimed task {conflict['id']} "
                f"({conflict['title']!r}): scope {candidate.get('scope')} "
                f"overlaps {conflict.get('scope')}. Resolve the overlap in "
                f"tasks.yaml (via add_task.py / manual re-scoping) before claiming."
            )

        candidate["owner"] = owner
        candidate["status"] = "claimed"
        candidate["branch"] = f"task/{candidate['id']}"
        candidate["claimed_at"] = now_iso()
        claimed_holder["task"] = dict(candidate)
        return data

    push_tasks_with_retry(mutate, commit_message=f"chore(tasks): {owner} claims a task", max_attempts=max_attempts)
    return claimed_holder["task"]


def create_worktree(task_id: str) -> Path:
    branch = f"task/{task_id}"
    worktree_path = REPO_ROOT.parent / f"task-{task_id}"
    if worktree_path.exists():
        raise CauceError(f"worktree path already exists: {worktree_path}")
    run_git(["worktree", "add", str(worktree_path), "-b", branch])
    return worktree_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Claim the next eligible Cauce task.")
    parser.add_argument("--owner", default=None, help="Defaults to `git config user.name`.")
    args = parser.parse_args()

    owner = args.owner or get_git_user_name()

    try:
        assert_main_checkout()
        # No standalone pull here on purpose: claim_next_task() ->
        # push_tasks_with_retry() already pulls fresh under
        # local_repo_lock() before deciding what's eligible. An extra
        # unlocked pull here would race against another local process's
        # locked critical section instead of just waiting for it.
        task = claim_next_task(owner)
        worktree_path = create_worktree(task["id"])
    except CauceError as exc:
        fail(str(exc))
        return

    print(f"Claimed {task['id']}: {task['title']}")
    print(f"  description:     {task['description']}")
    print(f"  scope:           {task['scope']}")
    print(f"  suggested_model: {task['suggested_model']}")
    print(f"  worktree:        {worktree_path}")

    notify("claimed", task, owner)


if __name__ == "__main__":
    main()
