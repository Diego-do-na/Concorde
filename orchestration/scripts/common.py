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

import contextlib
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import fcntl  # POSIX only (macOS/Linux) — Cauce doesn't target Windows.
except ImportError:  # pragma: no cover
    fcntl = None

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


# REPO_ROOT is the git repository root (the whole Concorde repo, which also
# holds api/, ml/, console/, docs/, etc.) — git plumbing (pull/push/commit)
# always operates there. Everything that is purely Cauce's own state lives
# under ORCHESTRATION_ROOT instead, so the task board, scripts, dashboards
# and this tool's own secrets stay out of the product-code tree the agents
# work in.
REPO_ROOT = find_repo_root()
ORCHESTRATION_ROOT = REPO_ROOT / "orchestration"
TASKS_FILE = ORCHESTRATION_ROOT / "tasks.yaml"
DISCORD_MAP_FILE = ORCHESTRATION_ROOT / "discord_map.yaml"

# Every script and both apps import common.py, so this is the one place
# that needs to load .env — DISCORD_WEBHOOK_URL / DISCORD_BOT_TOKEN end up
# in os.environ from here on, exactly as if they'd been exported by hand.
# Missing .env is fine (nothing to load); an existing shell export always
# wins over .env (override=False) so a one-off `export ...=...` in your
# terminal still takes precedence for testing.
load_dotenv(ORCHESTRATION_ROOT / ".env", override=False)


# ---------------------------------------------------------------------------
# local (same-machine) mutual exclusion
# ---------------------------------------------------------------------------

_LOCAL_LOCK_FILE = ORCHESTRATION_ROOT / ".git.lock"

# Re-entrancy bookkeeping for local_repo_lock(): flock() on a *second* fd of
# the same file would block against our own first fd, so nested acquisitions
# inside one process (e.g. push_tasks_with_retry -> run_git, which now locks
# every ref-mutating git command on its own) must be counted, not re-taken.
_lock_depth = 0
_lock_fd = None

# git subcommands that only read: safe to run without the lock. Everything
# else (pull/fetch/push/merge/rebase/checkout/reset/commit/add/worktree add|
# remove/branch -d/clone-from-this-repo/...) mutates refs, the index or the
# object store and MUST hold the lock -- two autopilot loops (claude +
# cursor-agent under fleet.sh) share this one checkout and otherwise collide
# with "cannot lock ref" / "divergent branches" the moment they pull at once.
_READ_ONLY_GIT = {"config", "rev-parse", "status", "log", "rev-list", "symbolic-ref",
                  "diff", "show", "ls-files", "ls-remote", "cat-file", "describe"}


@contextlib.contextmanager
def local_repo_lock(timeout: float = 120.0):
    """Serializes the full pull -> mutate -> add -> commit -> push sequence
    across multiple Cauce processes running against the SAME local
    checkout (e.g. two terminals both cd'd into one clone, or autopilot.sh
    + fleet.sh workers sharing a machine).

    This is a different problem from a rejected push: git's own
    `.git/index.lock` only guards a single git invocation, not a multi-step
    sequence. Two processes on one checkout can otherwise interleave —
    e.g. process A stages tasks.yaml, then process B's own retry does
    `git reset --hard` before A commits, and A's commit silently has
    nothing to commit even though A believed it had just claimed a task.
    Cross-machine concurrency (separate checkouts) doesn't need this: the
    git remote itself already serializes that via push_tasks_with_retry's
    rebase-and-retry.

    Blocks (politely, with a bounded wait) rather than failing outright,
    since another local process holding this lock is expected to release
    it in well under a second.
    """
    global _lock_depth, _lock_fd
    if fcntl is None:  # pragma: no cover — non-POSIX fallback: no locking
        yield
        return
    if _lock_depth > 0:  # re-entrant: already held by this process
        _lock_depth += 1
        try:
            yield
        finally:
            _lock_depth -= 1
        return
    _LOCAL_LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    _LOCAL_LOCK_FILE.touch(exist_ok=True)
    fd = open(_LOCAL_LOCK_FILE, "w")
    start = time.monotonic()
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() - start > timeout:
                    raise CauceError(
                        f"timed out after {timeout}s waiting for another local "
                        f"Cauce process to finish ({_LOCAL_LOCK_FILE}) — if none "
                        f"is actually running, delete that file and retry"
                    )
                time.sleep(0.05)
        _lock_depth = 1
        _lock_fd = fd
        yield
    finally:
        _lock_depth = 0
        _lock_fd = None
        fcntl.flock(fd, fcntl.LOCK_UN)
        fd.close()


# ---------------------------------------------------------------------------
# git plumbing
# ---------------------------------------------------------------------------

# How long to wait (seconds) between retries of a git command that failed
# only because another local git process momentarily held .git/index.lock
# — this is a *local*, same-checkout contention distinct from the
# push-rejected race push_tasks_with_retry already handles: it happens the
# instant two processes on the same machine (e.g. two terminals running
# claim_task.py against the same working copy) issue git commands at truly
# the same moment. It clears in milliseconds once the other process's git
# call finishes, so a short bounded backoff is enough — no need to involve
# the higher-level pull/rebase/retry cycle for something this transient.
_INDEX_LOCK_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)


def run_git(args: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    """Run a git command, raising CauceError with clean stderr on failure
    instead of letting subprocess's own exception (or a raw traceback)
    surface to the user.

    Transparently retries a few times, with a short backoff, if the only
    reason git failed is a transient `.git/index.lock` collision with
    another git process on the same machine — otherwise identical
    concurrent claims from two terminals on one machine would crash
    outright instead of just being slightly delayed.
    """
    subcommand = next((a for a in args if not a.startswith("-") and a != "-C"), "")
    if subcommand in _READ_ONLY_GIT or (len(args) >= 2 and args[0] == "worktree" and args[1] == "list"):
        return _run_git_unlocked(args, cwd=cwd, check=check, timeout=timeout)
    with local_repo_lock():
        return _run_git_unlocked(args, cwd=cwd, check=check, timeout=timeout)


def _run_git_unlocked(args: list[str], cwd: Path | None = None, check: bool = True, timeout: int = 60) -> subprocess.CompletedProcess:
    result: subprocess.CompletedProcess | None = None
    for attempt, delay in enumerate((0.0, *_INDEX_LOCK_RETRY_DELAYS)):
        if delay:
            time.sleep(delay)
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
        if result.returncode == 0 or "index.lock" not in result.stderr:
            break
        if attempt < len(_INDEX_LOCK_RETRY_DELAYS):
            print(
                f"  (git {' '.join(args)}: .git/index.lock busy from another "
                f"local process, retrying...)",
                file=sys.stderr,
            )
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


def assert_main_checkout() -> None:
    """Raise CauceError if REPO_ROOT is not actually the main worktree.

    REPO_ROOT is computed from the CURRENT working directory (`git
    rev-parse --show-toplevel`), so if any of claim_task.py / finish_task.py
    / add_task.py / poller.py is invoked from inside a task's own worktree
    instead of the main checkout, every git operation here (pull/commit/
    push, `git checkout main`) would silently happen on THAT WORKTREE'S
    OWN BRANCH — not main. Before this check existed that meant a claim or
    finish could land its commit on `task/<id>` instead of `main` without
    any error at all; now it's refused up front with a clear fix instead.

    `git worktree list`'s first line is always the main/original worktree
    (a git guarantee, not a convention this repo happens to follow), so
    that's what REPO_ROOT is compared against.
    """
    result = run_git(["worktree", "list", "--porcelain"], check=False)
    if result.returncode != 0 or not result.stdout.strip():
        return  # can't tell; let the real operation fail on its own if it's going to
    first_line = result.stdout.splitlines()[0]
    if not first_line.startswith("worktree "):
        return
    main_worktree = Path(first_line[len("worktree "):]).resolve()
    if main_worktree != REPO_ROOT.resolve():
        raise CauceError(
            f"this must be run from the main checkout ({main_worktree}), "
            f"not from {REPO_ROOT} — use work.sh/autopilot.sh/fleet.sh/finish.sh "
            f"(they already cd to the main checkout for you), or `cd {main_worktree}` "
            f"and run it from there directly"
        )


def push_tasks_with_retry(mutate_fn, commit_message: str, max_attempts: int = 5) -> dict:
    """The core "atomic edit of tasks.yaml" loop used by claim_task.py,
    finish_task.py, and add_task.py.

    `mutate_fn(tasks_data) -> tasks_data` receives the freshly-pulled
    tasks.yaml content, mutates it, and returns it. It is re-invoked from
    scratch on every retry, since the state it decided against (e.g.
    "which task is first eligible") may be stale after a rebase.

    Returns the tasks_data that was ultimately committed and pushed.

    The whole pull -> mutate -> add -> commit -> push cycle (all internal
    retries included) runs under `local_repo_lock()`: two Cauce processes
    sharing one local checkout take turns instead of interleaving git
    commands against the same working tree. Concurrent processes on
    DIFFERENT checkouts (different machines, or different clones on one
    machine) are unaffected — they're already correctly serialized by the
    push-rejection-retry below, which is a race over the shared remote,
    not over local files.
    """
    with local_repo_lock():
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
            # This is the expected, correct outcome of two people/processes
            # racing to claim at the same time — print it so it's visible in
            # the console instead of silently disappearing into a retry.
            print(
                f"  (tasks.yaml push race lost — someone else pushed first; "
                f"rebasing and retrying, attempt {attempt + 1}/{max_attempts})",
                file=sys.stderr,
            )
            run_git(["reset", "--hard", "HEAD~1"], check=False)
            run_git(["pull", "--rebase", "--quiet"], check=False)
    raise CauceError("unreachable: push_tasks_with_retry exhausted attempts")


def _default_base_branch() -> str:
    """Same intent as check_merge.py's own helper, but against the real
    origin (not a throwaway clone)."""
    for candidate in ("main", "master"):
        exists = run_git(["rev-parse", "--verify", f"origin/{candidate}"], check=False)
        if exists.returncode == 0:
            return candidate
    head = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], check=False)
    if head.returncode == 0:
        return head.stdout.strip().rsplit("/", 1)[-1]
    raise CauceError("could not determine the base branch (tried main, master, origin/HEAD)")


def branch_has_real_work(branch: str, base: str | None = None) -> bool:
    """True if `branch` has at least one commit beyond where it diverged
    from the base branch's current tip on origin. Used to refuse finishing
    a task whose worktree never actually got any work committed — without
    this, a task can be marked done (its branch merges "cleanly" because
    there's nothing to conflict with) with zero real content."""
    run_git(["fetch", "origin", "--quiet"], check=False)
    base = base or _default_base_branch()
    result = run_git(["rev-list", "--count", f"origin/{base}..{branch}"], check=False)
    if result.returncode != 0:
        # Can't tell (e.g. branch not found locally yet) -- don't block
        # finish on an inconclusive check; check_merge_conflict and the
        # merge step below will surface any real problem.
        return True
    try:
        return int(result.stdout.strip()) > 0
    except ValueError:
        return True


def merge_branch_to_main(branch: str, commit_message: str, max_attempts: int = 5) -> None:
    """Actually merges `branch` into the base branch (main) in THIS
    checkout and pushes it for real -- as opposed to check_merge_conflict,
    which only tests in a throwaway clone and never touches real history.

    Runs under local_repo_lock() with the same fetch/merge/push-with-retry
    shape as push_tasks_with_retry: a push rejected because someone else
    merged first is expected and retried (fresh fetch, redo the merge,
    push again). A real merge conflict is NOT retried -- retrying can't
    fix content that actually collides; it's raised immediately so a human
    resolves it by hand. That should be rare given tasks.yaml's
    non-overlapping scopes, and check_merge_conflict already screened for
    it against a slightly earlier snapshot of main.
    """
    with local_repo_lock():
        base = _default_base_branch()
        for attempt in range(1, max_attempts + 1):
            run_git(["fetch", "origin", "--quiet"])
            run_git(["checkout", "--quiet", base])
            run_git(["reset", "--hard", f"origin/{base}"])
            merge = run_git(["merge", "--no-ff", f"origin/{branch}", "-m", commit_message], check=False)
            if merge.returncode != 0:
                run_git(["merge", "--abort"], check=False)
                raise CauceError(
                    f"merging {branch} into {base} produced a real conflict "
                    f"(unexpected — check_merge_conflict said it was clean "
                    f"against an earlier snapshot of {base}). Resolve by hand: "
                    f"git checkout {base} && git merge {branch}"
                )
            push = run_git(["push", "origin", base], check=False)
            if push.returncode == 0:
                return
            if attempt == max_attempts:
                raise CauceError(
                    f"could not push {base} after merging {branch}, "
                    f"after {max_attempts} attempts (someone keeps winning the race)"
                )
            print(
                f"  ({base} push race lost — someone else merged first; "
                f"re-fetching and retrying, attempt {attempt + 1}/{max_attempts})",
                file=sys.stderr,
            )
            run_git(["reset", "--hard", f"origin/{base}"], check=False)
    raise CauceError("unreachable: merge_branch_to_main exhausted attempts")


def cleanup_task_worktree(task_id: str, branch: str) -> None:
    """Best-effort cleanup after a task's branch has already been merged
    into main: remove its worktree, delete the local branch, delete the
    remote branch. Call this AFTER merge_branch_to_main succeeds, never
    before -- git refuses to delete a branch that's still checked out in
    a worktree, so the worktree has to go first.

    Every step is independent and never raises: a failure here is printed
    as a warning and left for manual cleanup, but the caller's finish flow
    (marking the task done) must still complete. Worktree/branch cleanup
    is a courtesy, not a condition of the task being finished. In
    particular the local branch delete uses `-d`, not `-D`: if git thinks
    the branch isn't fully merged (it should be, right after a successful
    merge -- but never force past its judgment here), this warns and
    leaves the branch alone rather than discarding history.
    """
    worktree_path = REPO_ROOT.parent / f"task-{task_id}"
    with local_repo_lock():
        if worktree_path.exists():
            remove = run_git(["worktree", "remove", str(worktree_path), "--force"], check=False)
            if remove.returncode != 0:
                print(
                    f"  (warning: could not remove worktree {worktree_path}: "
                    f"{remove.stderr.strip()} — left in place for manual cleanup)",
                    file=sys.stderr,
                )

        branch_delete = run_git(["branch", "-d", branch], check=False)
        if branch_delete.returncode != 0:
            print(
                f"  (warning: could not delete local branch {branch}: "
                f"{branch_delete.stderr.strip()} — left in place for manual review)",
                file=sys.stderr,
            )

        remote_delete = run_git(["push", "origin", "--delete", branch], check=False)
        if remote_delete.returncode != 0:
            print(
                f"  (warning: could not delete remote branch {branch}: "
                f"{remote_delete.stderr.strip()} — left in place)",
                file=sys.stderr,
            )


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
        "# NEVER edit this file by hand outside of orchestration/scripts/*.py.\n"
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
