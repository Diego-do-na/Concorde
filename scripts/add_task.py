"""
add_task.py — append a new task to tasks.yaml, commit, and push.

This is the one place task-creation logic lives; model-requester's web
app imports `add_task()` directly instead of reimplementing this.

CLI:
    python scripts/add_task.py \\
        --title "Add pagination to /tasks" \\
        --description "..." \\
        --scope src/api/tasks.py --scope tests/test_tasks.py \\
        --depends-on T001 \\
        --suggested-model claude-sonnet
"""

from __future__ import annotations

import argparse
from typing import Any

from common import CauceError, fail, load_tasks, push_tasks_with_retry, tasks_by_id


def add_task(
    title: str,
    description: str,
    scope: list[str],
    depends_on: list[str] | None = None,
    suggested_model: str = "",
    max_attempts: int = 5,
) -> str:
    """Validates, appends, commits, and pushes a new task. Returns the new
    task's id. Raises CauceError on any validation or git failure."""
    depends_on = depends_on or []
    if not title.strip():
        raise CauceError("title is required")
    if not scope:
        raise CauceError("scope must have at least one path")

    new_id_holder: dict[str, str] = {}

    def mutate(data: dict[str, Any]) -> dict[str, Any]:
        by_id = tasks_by_id(data)
        missing = [d for d in depends_on if d not in by_id]
        if missing:
            raise CauceError(f"depends_on references unknown task id(s): {missing}")

        # Re-derive the id inside the retry loop (not before), since a
        # concurrent add_task could have taken the "next" id in the meantime.
        from common import generate_task_id, now_iso  # local import: avoid unused-at-top confusion

        new_id = generate_task_id(data)
        new_id_holder["id"] = new_id
        data.setdefault("tasks", []).append(
            {
                "id": new_id,
                "title": title,
                "description": description,
                "scope": list(scope),
                "depends_on": list(depends_on),
                "suggested_model": suggested_model,
                "status": "todo",
                "owner": None,
                "branch": None,
                "claimed_at": None,
                "done_at": None,
            }
        )
        return data

    push_tasks_with_retry(mutate, commit_message=f"chore(tasks): add {title!r}", max_attempts=max_attempts)
    return new_id_holder["id"]


def main() -> None:
    parser = argparse.ArgumentParser(description="Add a new Cauce task.")
    parser.add_argument("--title", required=True)
    parser.add_argument("--description", required=True)
    parser.add_argument("--scope", action="append", required=True, help="Repeatable: one path per flag.")
    parser.add_argument("--depends-on", action="append", default=[], help="Repeatable: one task id per flag.")
    parser.add_argument("--suggested-model", default="")
    args = parser.parse_args()

    try:
        new_id = add_task(
            title=args.title,
            description=args.description,
            scope=args.scope,
            depends_on=args.depends_on,
            suggested_model=args.suggested_model,
        )
    except CauceError as exc:
        fail(str(exc))
        return

    print(f"Added task {new_id}: {args.title}")


if __name__ == "__main__":
    main()
