"""
notify_discord.py — best-effort Discord notifications for claim/done/conflict
events. Never raises: a Discord outage or missing config should never take
down claim_task.py / finish_task.py, so every failure here just prints a
warning and moves on.

Modes (checked in this order):
  1. DISCORD_WEBHOOK_URL set  -> POST the message to that channel webhook.
  2. DISCORD_BOT_TOKEN set    -> open/reuse a DM with the owner's Discord
                                 user id (looked up in discord_map.yaml)
                                 and message only them.
  3. neither set              -> print to console.

CLI (manual testing):
    python notify_discord.py --event claimed --task-id T001 --owner Alice
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Any

import requests
import yaml

from common import CauceError, DISCORD_MAP_FILE, fail, find_task, load_tasks

DISCORD_API = "https://discord.com/api/v10"


def _load_discord_map() -> dict[str, str]:
    if not DISCORD_MAP_FILE.exists():
        return {}
    try:
        with open(DISCORD_MAP_FILE, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError:
        # A broken discord_map.yaml shouldn't crash task automation.
        print("warning: discord_map.yaml is not valid YAML, ignoring it", file=sys.stderr)
        return {}


def _build_message(event: str, task: dict[str, Any], owner: str) -> str:
    task_id = task.get("id", "?")
    title = task.get("title", "(untitled)")
    if event == "claimed":
        return f"[Cauce] {owner} claimed {task_id}: {title}"
    if event == "done":
        return f"[Cauce] {task_id} ({title}) marked done by {owner}"
    if event == "conflict":
        return (
            f"[Cauce] Merge conflict on {task_id} ({title}) — "
            f"{owner}, please resolve manually and re-run finish_task.py"
        )
    return f"[Cauce] {event}: {task_id} ({title}) — {owner}"


def _post_webhook(message: str) -> None:
    url = os.environ["DISCORD_WEBHOOK_URL"]
    resp = requests.post(url, json={"content": message}, timeout=10)
    resp.raise_for_status()


def _post_dm(message: str, owner: str) -> None:
    token = os.environ["DISCORD_BOT_TOKEN"]
    discord_map = _load_discord_map()
    user_id = discord_map.get(owner)
    if not user_id:
        print(
            f"warning: no discord_map.yaml entry for owner {owner!r}, "
            f"cannot DM — falling back to console:\n{message}",
            file=sys.stderr,
        )
        return
    headers = {"Authorization": f"Bot {token}"}
    # Opening a DM channel is idempotent — Discord returns the existing
    # channel if one is already open with this user.
    channel_resp = requests.post(
        f"{DISCORD_API}/users/@me/channels",
        json={"recipient_id": user_id},
        headers=headers,
        timeout=10,
    )
    channel_resp.raise_for_status()
    channel_id = channel_resp.json()["id"]
    msg_resp = requests.post(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        json={"content": message},
        headers=headers,
        timeout=10,
    )
    msg_resp.raise_for_status()


def notify(event: str, task: dict[str, Any], owner: str) -> None:
    """Best-effort notification. Swallows all errors after logging them —
    callers should never have to wrap this in their own try/except."""
    message = _build_message(event, task, owner)
    try:
        if os.environ.get("DISCORD_WEBHOOK_URL"):
            _post_webhook(message)
            return
        if os.environ.get("DISCORD_BOT_TOKEN"):
            _post_dm(message, owner)
            return
        print(message)
    except requests.RequestException as exc:
        print(f"warning: Discord notification failed ({exc}); message was:\n{message}", file=sys.stderr)
    except Exception as exc:  # last-resort net: never let notify() crash a caller
        print(f"warning: Discord notification failed unexpectedly ({exc}); message was:\n{message}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send a Cauce Discord notification manually.")
    parser.add_argument("--event", required=True, choices=["claimed", "done", "conflict"])
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()

    try:
        data = load_tasks()
        task = find_task(data, args.task_id)
        if task is None:
            fail(f"no task with id {args.task_id!r} in tasks.yaml")
        notify(args.event, task, args.owner)
    except CauceError as exc:
        fail(str(exc))


if __name__ == "__main__":
    main()
