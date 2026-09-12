"""
locked_git.py — run one git command while holding Cauce's local repo lock
(orchestration/.git.lock), so shell scripts (autopilot.sh, work.sh) share the
same mutual exclusion as claim_task.py / finish_task.py.

    python orchestration/scripts/locked_git.py pull --quiet
    python orchestration/scripts/locked_git.py -C ../task-T003 fetch origin --quiet

Why: fleet.sh runs one autopilot.sh loop per agent CLI on ONE machine, and
every loop iteration pulls the shared main checkout and fetches/rebases its
worktree (worktrees share .git, so their ref updates collide too). Without
this lock, two loops touching git within the same second fail with
"cannot lock ref" or "divergent branches". macOS has no flock(1), hence this
tiny Python wrapper around common.local_repo_lock(). Output passes straight
through; the exit code is git's.
"""

from __future__ import annotations

import subprocess
import sys

from common import CauceError, local_repo_lock


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print("usage: locked_git.py <git args...>", file=sys.stderr)
        sys.exit(2)
    try:
        with local_repo_lock():
            result = subprocess.run(["git", *args])
    except CauceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(1)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
