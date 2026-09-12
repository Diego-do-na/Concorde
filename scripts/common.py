"""
Shared helpers for every Cauce script and both FastAPI apps.

Everything here is intentionally boring: thin wrappers around `git` and
`tasks.yaml` so that claim_task.py / finish_task.py / add_task.py /
poller.py / the two dashboards all agree on one behavior instead of five
slightly different reimplementations.

Why a subprocess wrapper instead of a library like GitPython: Cauce has
exactly one hard dependency it needs from git (pull/push/worktree/merge),
none of it exotic, and shelling out to the same `git` every contributor
already has avoids a dependency that behaves differently across
platforms. subprocess + explicit timeouts + captured stderr is enough.
"""

from __future__ import annotations

import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


class CauceError(Exception):
    """Any error we want to show the user as a clean one-liner, never a traceback."""


def fail(message: str) -> "NoReturn":  # noqa: F821 - typing convenience only
    """Print a clean error (no traceback) and exit non-zero."""
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Repo / paths
# ---------------------------------------------------------------------------

def find_repo_root(start: Path | None = None) -> Path:
    """Locate the repo root via `git rev-parse`, so scripts work no matter
    which subdirectory (or worktree) they're invoked from."""
    cwd = start or Path.cwd()
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CauceError(f"could not locate git repo root: {exc}") from exc
    if out.returncode != 0:
        raise CauceError(
            "not inside a git repository (or git is not installed): "
            + out.stderr.strip()
        )
    return Path(out.stdout.strip())


REPO_ROOT = find_repo_root()
TASKS_FILE = REPO_ROOT / "tasks.yaml"
DISCORD_MAP_FILE = REPO_ROOT / "discord_map.yaml"

# Every script and both apps import common.py, so this is the one place
# that needs to load .env — DISCORD_WEBHOOK_URL / DISCORD_BOT_TOKEN end up
# in os.environ from here on, exactly as if they'd been exported by hand.
# Missing .env is fine (nothing to load); an existing shell export always
# wins over .env (override=False) so a one-off `export ...=...` in your
# terminal still takes precedence for testing.
load_dotenv(REPO_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

def run_git(args: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command, raising CauceError with clean stderr on failure
    instead of letting subprocess's own exception (or a raw traceback)
    surface to the user."""
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CauceError(f"git {' '.join(args)} failed to run: {exc}") from exc
    if check and result.returncode != 0:
        raise CauceError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result


def git_pull(cwd: Path | None = None) -> None:
    run_git(["pull", "--quiet"], cwd=cwd)


def git_pull_rebase(cwd: Path | None = None) -> None:
    run_git(["pull", "--rebase", "--quiet"], cwd=cwd)


def git_current_branch(cwd: Path | None = None) -> str:
    return run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd).stdout.strip()


def get_git_user_name() -> str:
    """Best-effort default owner name when --owner is not passed."""
    result = run_git(["config", "user.name"], check=False)
    name = result.stdout.strip()
    return name or "unknown"


def push_tasks_with_retry(mutate_fn, commit_message: str, max_attempts: int = 5) -> dict:
    """The core "atomic edit of tasks.yaml" loop used by claim_task.py,
    finish_task.py, and add_task.py.

    `mutate_fn(tasks_data) -> tasks_data` receives the freshly-pulled
    tasks.yaml content, mutates it, and returns it. It is re-invoked from
    scratch on every retry, since the state it decided against (e.g.
    "which task is first eligible") may be stale after a rebase.

    Returns the tasks_data that was ultimately committed and pushed.
    """
    for attempt in range(1, max_attempts + 1):
        git_pull()
        data = load_tasks()
        data = mutate_fn(data)
        save_tasks(data)
        run_git(["add", str(TASKS_FILE)])
        # Nothing to commit can happen if mutate_fn is a no-op retry path;
        # guard so we don't hard-fail on an empty diff.
        status = run_git(["status", "--porcelain", "--", str(TASKS_FILE)], check=False)
        if not status.stdout.strip():
            return data
        commit = run_git(["commit", "-m", commit_message], check=False)
        if commit.returncode != 0:
            raise CauceError(f"git commit failed:\n{commit.stderr.strip()}")
        push = run_git(["push"], check=False)
        if push.returncode == 0:
            return data
        if attempt == max_attempts:
            raise CauceError(
                f"could not push tasks.yaml after {max_attempts} attempts "
                f"(someone keeps winning the race) — try again shortly"
            )
        # Someone else pushed to tasks.yaml first: rebase and retry the
        # whole decision (find-eligible-task / etc.) against fresh state.
        run_git(["reset", "--hard", "HEAD~1"], check=False)
        run_git(["pull", "--rebase", "--quiet"], check=False)
    raise CauceError("unreachable: push_tasks_with_retry exhausted attempts")


# ---------------------------------------------------------------------------
# tasks.yaml I/O
# ---------------------------------------------------------------------------

def load_tasks(path: Path | None = None) -> dict[str, Any]:
    p = path or TASKS_FILE
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError as exc:
        raise CauceError(f"{p} not found") from exc
    except yaml.YAMLError as exc:
        raise CauceError(f"{p} is not valid YAML: {exc}") from exc
    data.setdefault("tasks", [])
    return data


def save_tasks(data: dict[str, Any], path: Path | None = None) -> None:
    p = path or TASKS_FILE
    header = (
        "# tasks.yaml — the single source of truth for Cauce's task board.\n"
        "# NEVER edit this file by hand outside of scripts/*.py.\n"
        "# See git history / README.md for the full schema.\n"
    )
    with open(p, "w", encoding="utf-8") as f:
        f.write(header)
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False, allow_unicode=True)


def find_task(data: dict[str, Any], task_id: str) -> dict[str, Any] | None:
    for task in data.get("tasks", []):
        if task["id"] == task_id:
            return task
    return None


def tasks_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {t["id"]: t for t in data.get("tasks", [])}


def dependencies_satisfied(task: dict[str, Any], by_id: dict[str, dict[str, Any]]) -> bool:
    for dep_id in task.get("depends_on") or []:
        dep = by_id.get(dep_id)
        if dep is None or dep.get("status") != "done":
            return False
    return True


def generate_task_id(data: dict[str, Any]) -> str:
    """Next sequential id, e.g. T001, T002, ... T123."""
    max_n = 0
    for task in data.get("tasks", []):
        tid = str(task.get("id", ""))
        if tid.startswith("T") and tid[1:].isdigit():
            max_n = max(max_n, int(tid[1:]))
    return f"T{max_n + 1:03d}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# scope overlap
# ---------------------------------------------------------------------------

def _normalize(path: str) -> str:
    return path.strip().replace("\\", "/").rstrip("/")


def _paths_overlap(a: str, b: str) -> bool:
    """Two repo-relative paths "overlap" if they're equal, or one is a
    parent directory of the other. This treats scope entries as either
    files or directory prefixes, e.g. "src/" overlaps "src/api/health.py".
    """
    a, b = _normalize(a), _normalize(b)
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def scopes_overlap(scope_a: list[str], scope_b: list[str]) -> str | None:
    """Returns the first (path_a, path_b) collision as a string, or None
    if the two scopes don't touch."""
    for a in scope_a or []:
        for b in scope_b or []:
            if _paths_overlap(a, b):
                return f"{a!r} overlaps {b!r}"
    return None


def find_scope_conflict(scope: list[str], other_tasks: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Given a candidate scope and a list of tasks to check against
    (typically all status=claimed tasks), return the first conflicting
    task, or None."""
    for other in other_tasks:
        collision = scopes_overlap(scope, other.get("scope") or [])
        if collision:
            return other
    return None


@dataclass
class GitIdentity:
    name: str
