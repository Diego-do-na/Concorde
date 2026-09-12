"""
finish_task.py — close out a claimed task.

Usage:
    python scripts/finish_task.py --task-id T002

Run this from the main repo checkout (not from inside the task's own
worktree) — it needs to update tasks.yaml on main, which every task
branch shares, so that update always happens against main directly
rather than through whatever branch a worktree happens to be on.

Flow:
    1. Look up the task, make sure it's status=claimed and has a branch.
    2. check_merge.py: would `branch` merge cleanly into main?
       - CONFLICT: push nothing, tasks.yaml is left untouched, notify the
         task's owner specifically, exit non-zero.
       - CLEAN: push the work branch, then atomically flip the task to
         status=done/done_at=now in tasks.yaml (same retry pattern as
         claim_task.py), notify Discord, and print any tasks that just
         became unblocked (their depends_on are now all satisfied) — this
         script never auto-claims them, it only surfaces them.
"""

from __future__ import annotations

import argparse
from typing import Any

from check_merge import check_merge_conflict
from common import (
    CauceError,
    dependencies_satisfied,
    fail,
    find_task,
    load_tasks,
    now_iso,
    push_tasks_with_retry,
    run_git,
    tasks_by_id,
)
from notify_discord import notify


def _newly_unblocked(data: dict[str, Any], just_finished_id: str) -> list[dict[str, Any]]:
    by_id = tasks_by_id(data)
    unblocked = []
    for task in data.get("tasks", []):
        if task.get("status") != "todo":
            continue
        if just_finished_id not in (task.get("depends_on") or []):
            continue
        if dependencies_satisfied(task, by_id):
            unblocked.append(task)
    return unblocked


def main() -> None:
    parser = argparse.ArgumentParser(description="Finish a claimed Cauce task.")
    parser.add_argument("--task-id", required=True)
    args = parser.parse_args()
    task_id = args.task_id

    try:
        run_git(["pull", "--quiet"])
        data = load_tasks()
        task = find_task(data, task_id)
        if task is None:
            fail(f"no task with id {task_id!r} in tasks.yaml")
            return
        if task.get("status") != "claimed":
            fail(f"task {task_id} is status={task.get('status')!r}, expected 'claimed'")
            return
        branch = task.get("branch")
        if not branch:
            fail(f"task {task_id} has no branch recorded")
            return

        print(f"Checking whether {branch} merges cleanly into main...")
        conflict = check_merge_conflict(branch)
    except CauceError as exc:
        fail(str(exc))
        return

    owner = task.get("owner") or "unknown"

    if conflict:
        print(f"CONFLICT: {branch} does not merge cleanly into main.")
        print("Nothing was pushed and the task was NOT marked done.")
        print(f"Resolve the conflict manually (rebase/merge {branch} onto main), then re-run:")
        print(f"    python scripts/finish_task.py --task-id {task_id}")
        notify("conflict", task, owner)
        raise SystemExit(1)

    try:
        # Push the work branch itself first. It already exists as a ref in
        # this repo (worktrees share the object store), so this works even
        # if finish_task.py is run from the main checkout, not the worktree.
        push_branch = run_git(["push", "origin", f"{branch}:{branch}"], check=False)
        if push_branch.returncode != 0:
            raise CauceError(f"could not push {branch}: {push_branch.stderr.strip()}")

        def mutate(data: dict[str, Any]) -> dict[str, Any]:
            current = find_task(data, task_id)
            if current is None:
                raise CauceError(f"task {task_id} disappeared from tasks.yaml during finish")
            current["status"] = "done"
            current["done_at"] = now_iso()
            return data

        data = push_tasks_with_retry(mutate, commit_message=f"chore(tasks): {task_id} done")
    except CauceError as exc:
        fail(str(exc))
        return

    print(f"Done: {task_id} ({task['title']}) pushed and marked done.")
    notify("done", task, owner)

    unblocked = _newly_unblocked(data, task_id)
    if unblocked:
        print("\nNewly unblocked tasks (not auto-claimed):")
        for t in unblocked:
            print(f"  - {t['id']}: {t['title']}")


if __name__ == "__main__":
    main()
