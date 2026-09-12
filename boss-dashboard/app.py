"""
boss-dashboard — read-only monitor.

This app never writes to git or tasks.yaml. It keeps its own local
"mirror" clone (separate from anyone's working checkout) so that
watching the board never interferes with someone's in-progress work, and
so it's safe to leave running unattended.

Run:
    uvicorn app:app --port 8000 --app-dir boss-dashboard
(or `cd boss-dashboard && uvicorn app:app --port 8000`)

Config (env vars, both optional):
    CAUCE_REPO_URL    where to clone the mirror from. Defaults to the
                       local repo this file lives in, which is enough for
                       one person watching their own team's local remote
                       setup; point it at the real GitHub URL for a
                       dashboard that runs somewhere else.
    CAUCE_MIRROR_PATH where to keep the mirror clone. Defaults to
                       boss-dashboard/_mirror (gitignored).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

APP_DIR = Path(__file__).resolve().parent
REPO_ROOT = APP_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from common import CauceError, dependencies_satisfied, load_tasks, tasks_by_id  # noqa: E402

MIRROR_PATH = Path(os.environ.get("CAUCE_MIRROR_PATH", APP_DIR / "_mirror")).resolve()
REPO_URL = os.environ.get("CAUCE_REPO_URL", str(REPO_ROOT))

app = FastAPI(title="Cauce Boss Dashboard")


def _run_git(args: list[str], cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _ensure_mirror() -> None:
    """Clone the mirror once if it doesn't exist yet. Never raises past
    here — a broken mirror should surface as a clean JSON error, not a 500
    with a traceback."""
    if (MIRROR_PATH / ".git").exists():
        return
    MIRROR_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = _run_git(["clone", "--quiet", REPO_URL, str(MIRROR_PATH)], cwd=MIRROR_PATH.parent)
    if result.returncode != 0:
        raise CauceError(f"could not clone mirror from {REPO_URL!r}: {result.stderr.strip()}")


def _default_base_branch() -> str:
    """Prefer 'main', fall back to 'master' — don't trust the mirror's
    local HEAD symref, which can go stale (e.g. if the remote's default
    branch changed after the mirror was first cloned)."""
    for candidate in ("main", "master"):
        exists = _run_git(["rev-parse", "--verify", f"origin/{candidate}"], cwd=MIRROR_PATH)
        if exists.returncode == 0:
            return candidate
    raise CauceError("could not find an origin/main or origin/master branch in the mirror")


def _refresh_mirror() -> None:
    _ensure_mirror()
    # Pull in every branch ref, so we can report "last commit" per task
    # branch as well as read tasks.yaml off the base branch.
    fetch = _run_git(["fetch", "--quiet", "origin", "+refs/heads/*:refs/remotes/origin/*"], cwd=MIRROR_PATH)
    if fetch.returncode != 0:
        raise CauceError(f"mirror `git fetch` failed: {fetch.stderr.strip()}")
    base = _default_base_branch()
    # Hard-reset onto origin/<base> instead of `git pull`: robust even if
    # the mirror's checked-out branch or tracking info doesn't match the
    # remote's current default (this is a read-only mirror, so discarding
    # any local state here is always safe).
    checkout = _run_git(["checkout", "--quiet", "-B", base, f"origin/{base}"], cwd=MIRROR_PATH)
    if checkout.returncode != 0:
        raise CauceError(f"mirror checkout of {base!r} failed: {checkout.stderr.strip()}")


def _last_commit_relative(branch: str) -> str | None:
    for ref in (f"origin/{branch}", branch):
        result = _run_git(["log", "-1", "--format=%cr", ref], cwd=MIRROR_PATH)
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    return None


def _recent_commits(branch: str, limit: int = 5) -> list[dict[str, str]]:
    for ref in (f"origin/{branch}", branch):
        result = _run_git(
            ["log", f"-{limit}", "--format=%h|%cr|%s", ref],
            cwd=MIRROR_PATH,
        )
        if result.returncode == 0 and result.stdout.strip():
            commits = []
            for line in result.stdout.strip().splitlines():
                parts = line.split("|", 2)
                if len(parts) == 3:
                    commits.append({"hash": parts[0], "when": parts[1], "message": parts[2]})
            return commits
    return []


@app.get("/api/state")
def get_state() -> JSONResponse:
    try:
        _refresh_mirror()
        data = load_tasks(MIRROR_PATH / "tasks.yaml")
    except CauceError as exc:
        # A monitor that can't reach git should say so plainly, not crash.
        return JSONResponse(status_code=503, content={"error": str(exc)})

    tasks: list[dict[str, Any]] = data.get("tasks", [])
    by_id = tasks_by_id(data)
    total = len(tasks)
    done_count = sum(1 for t in tasks if t.get("status") == "done")
    percent_complete = round((done_count / total) * 100, 1) if total else 0.0

    columns: dict[str, list[dict[str, Any]]] = {"todo": [], "claimed": [], "done": []}
    activity: list[dict[str, Any]] = []

    for task in tasks:
        status = task.get("status", "todo")
        entry = dict(task)
        entry["ready"] = status == "todo" and dependencies_satisfied(task, by_id)
        columns.setdefault(status, []).append(entry)

        branch = task.get("branch")
        if branch and status in ("claimed", "done"):
            activity.append(
                {
                    "task_id": task["id"],
                    "title": task["title"],
                    "owner": task.get("owner"),
                    "branch": branch,
                    "status": status,
                    "last_commit": _last_commit_relative(branch),
                    "recent_commits": _recent_commits(branch),
                }
            )

    return JSONResponse(
        {
            "percent_complete": percent_complete,
            "total": total,
            "done_count": done_count,
            "columns": columns,
            "activity": activity,
        }
    )


@app.get("/")
def index() -> FileResponse:
    return FileResponse(APP_DIR / "static" / "index.html")


app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")
