"""
check_merge.py — will merging <branch> into main conflict?

This never touches the real working directory or its worktrees: it clones
the repo into a throwaway temp directory, does a --no-commit --no-ff test
merge there, reads the result, and deletes the clone. Safe to call from
finish_task.py right before actually pushing/merging anything for real.

CLI:
    python check_merge.py <branch>
Exits 0 and prints "CLEAN" if it would merge cleanly, exits 1 and prints
"CONFLICT" otherwise.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from common import CauceError, REPO_ROOT, run_git


def _default_base_branch(clone_dir: Path) -> str:
    """Prefer 'main', fall back to 'master', fall back to whatever HEAD
    of the remote's default is — keeps this working on older repos too."""
    for candidate in ("main", "master"):
        exists = run_git(["rev-parse", "--verify", f"origin/{candidate}"], cwd=clone_dir, check=False)
        if exists.returncode == 0:
            return candidate
    head = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=clone_dir, check=False)
    if head.returncode == 0:
        return head.stdout.strip().rsplit("/", 1)[-1]
    raise CauceError("could not determine the base branch (tried main, master, origin/HEAD)")


def check_merge_conflict(branch: str, repo_path: Path | None = None) -> bool:
    """Returns True if merging `branch` into the base branch would conflict,
    False if it would merge cleanly. Always cleans up after itself."""
    source = repo_path or REPO_ROOT
    tmp_dir = Path(tempfile.mkdtemp(prefix="cauce-check-merge-"))
    clone_dir = tmp_dir / "clone"
    try:
        clone = run_git(
            ["clone", "--quiet", "--no-hardlinks", str(source), str(clone_dir)],
            cwd=tmp_dir,
            check=False,
        )
        if clone.returncode != 0:
            raise CauceError(f"could not create test clone: {clone.stderr.strip()}")

        base = _default_base_branch(clone_dir)

        fetch = run_git(
            ["fetch", "--quiet", "origin", branch],
            cwd=clone_dir,
            check=False,
        )
        if fetch.returncode != 0:
            raise CauceError(
                f"could not fetch branch {branch!r} for the test merge "
                f"(has it been pushed anywhere check_merge can see it?): "
                f"{fetch.stderr.strip()}"
            )

        run_git(["checkout", "--quiet", base], cwd=clone_dir)

        merge = run_git(
            ["merge", "--no-commit", "--no-ff", "FETCH_HEAD"],
            cwd=clone_dir,
            check=False,
        )
        conflict = merge.returncode != 0

        # Whether the test merge succeeded or not, leave no half-applied
        # state behind (a clean --no-commit merge still leaves things
        # staged). We're about to delete the whole clone anyway, but
        # `merge --abort` is the documented way to unwind it.
        run_git(["merge", "--abort"], cwd=clone_dir, check=False)

        return conflict
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python check_merge.py <branch>", file=sys.stderr)
        sys.exit(2)
    branch = sys.argv[1]
    try:
        conflict = check_merge_conflict(branch)
    except CauceError as exc:
        print(f"error: {exc}", file=sys.stderr)
        sys.exit(2)
    if conflict:
        print("CONFLICT")
        sys.exit(1)
    print("CLEAN")
    sys.exit(0)


if __name__ == "__main__":
    main()
