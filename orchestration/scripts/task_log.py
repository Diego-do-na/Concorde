"""
task_log.py — deterministic, no-LLM summary of a Cauce task's agent
session.

Parses Claude Code's own per-session transcript directly — no model call,
no network, pure JSON parsing. Claude Code already writes one .jsonl file
per session to:

    ~/.claude/projects/<worktree-path-with-slashes-as-dashes>/<session-id>.jsonl

e.g. worktree /Users/x/Proyects/task-C-001 -> directory
~/.claude/projects/-Users-x-Proyects-task-C-001/. This script computes
that same directory from the task's worktree path (../task-<id>, per
claim_task.py's own convention), picks the most recently modified .jsonl
in it, and extracts: session start/end timestamps, every tool call (Bash
command / Write or Edit file path), and Claude Code's own away_summary
line if one exists in the transcript.

cursor-agent sessions are NOT covered. cursor-agent does not write to
~/.claude/projects (that's Claude Code's own local session store), and no
equivalent transcript location could be confirmed for it (cursor-agent
required `agent login` on the machine this was built on, which wasn't
done, so its logging behavior couldn't be inspected empirically). For a
task worked with cursor-agent, this prints a clear explanation instead of
silently finding nothing or guessing.

Usage:
    python orchestration/scripts/task_log.py <task-id>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from common import CauceError, REPO_ROOT, fail, find_task, load_tasks


def _claude_projects_dir_for_worktree(worktree: Path) -> Path:
    """Reproduces Claude Code's own directory-naming scheme: the absolute
    worktree path with every "/" replaced by "-". Verified against real
    session directories on this machine (both the main repo checkout and
    a task worktree matched this exact transformation)."""
    sanitized = str(worktree.resolve()).replace("/", "-")
    return Path.home() / ".claude" / "projects" / sanitized


def _most_recent_transcript(claude_dir: Path) -> Path | None:
    if not claude_dir.is_dir():
        return None
    transcripts = sorted(
        claude_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return transcripts[0] if transcripts else None


def _summarize_tool_use(name: str, tool_input: dict[str, Any]) -> str:
    if name == "Bash":
        return f"Bash: {tool_input.get('command', '')}"
    if name in ("Write", "Edit", "NotebookEdit"):
        return f"{name}: {tool_input.get('file_path') or tool_input.get('notebook_path', '')}"
    if name == "Read":
        return f"Read: {tool_input.get('file_path', '')}"
    # Fallback: any other tool, keep it short.
    compact = json.dumps(tool_input, ensure_ascii=False)
    if len(compact) > 120:
        compact = compact[:117] + "..."
    return f"{name}: {compact}"


def summarize_transcript(path: Path) -> dict[str, Any]:
    """Pure parsing of one .jsonl transcript. Returns a dict with
    start/end timestamps, an ordered list of tool-call summary strings,
    and the last away_summary text found (or None)."""
    first_ts: str | None = None
    last_ts: str | None = None
    tool_calls: list[str] = []
    away_summary: str | None = None

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            ts = entry.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

            entry_type = entry.get("type")
            if entry_type == "assistant":
                content = entry.get("message", {}).get("content")
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "tool_use":
                            tool_calls.append(
                                _summarize_tool_use(
                                    block.get("name", "?"), block.get("input", {}) or {}
                                )
                            )
            elif entry_type == "system" and entry.get("subtype") == "away_summary":
                away_summary = entry.get("content")

    return {
        "start": first_ts,
        "end": last_ts,
        "tool_calls": tool_calls,
        "away_summary": away_summary,
    }


def print_summary(task_id: str) -> None:
    data = load_tasks()
    task = find_task(data, task_id)
    if task is None:
        raise CauceError(f"no task with id {task_id!r} in tasks.yaml")

    worktree = REPO_ROOT.parent / f"task-{task_id}"
    claude_dir = _claude_projects_dir_for_worktree(worktree)
    transcript = _most_recent_transcript(claude_dir)

    if transcript is None:
        agent_hint = ""
        # A cheap heuristic, not a hard fact: if the task's own scope work
        # was ever done with cursor-agent, there is no known transcript
        # location for it (see module docstring) -- say so plainly rather
        # than just reporting "not found".
        print(f"No Claude Code transcript found for task {task_id}.")
        print(f"  expected worktree: {worktree}")
        print(f"  expected under:    {claude_dir}")
        print(
            "  If this task was worked with `claude`, check the worktree path "
            "above is right and that a session actually ran there."
        )
        print(
            "  If it was worked with `cursor-agent`: cursor-agent does not write "
            "to ~/.claude/projects (that's Claude Code's own session store), and "
            "no equivalent log location for cursor-agent has been confirmed yet — "
            "there is currently no way for this script to summarize a cursor-agent "
            "session."
        )
        return

    summary = summarize_transcript(transcript)

    print(f"Task {task_id}: {task['title']}")
    print(f"  transcript: {transcript}")
    print(f"  start:      {summary['start'] or '(unknown)'}")
    print(f"  end:        {summary['end'] or '(unknown)'}")
    print(f"  tool calls: {len(summary['tool_calls'])}")
    for call in summary["tool_calls"]:
        print(f"    - {call}")
    if summary["away_summary"]:
        print(f"  summary:    {summary['away_summary']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Deterministic summary of a Cauce task's agent session (no model call)."
    )
    parser.add_argument("task_id")
    args = parser.parse_args()

    try:
        print_summary(args.task_id)
    except CauceError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
