# CONCORDE — Snapshot & Cold-Standby Runbook (NFR-002, §18.3)

This document describes the snapshot / cold-standby contingency for the CONCORDE Vultr instance and the exact operator steps during judging.

Summary
- Take a Vultr snapshot after the real-model deploy and record the snapshot ID in the Vultr panel.
- Restore snapshot to a second instance (standby) when needed.
- Use the provided helper scripts to verify health: `product/deploy/watch_health.sh`.
- `product/deploy/deploy.sh --standby <ip>` (dry-run) prints the plan. Use `--apply` to perform the copy and restart (or use the standalone helper `product/deploy/standby.sh`).

1) Snapshot (operator)
- After the "real-model" deploy completes, create a Vultr snapshot from the running instance (Vultr control panel → Instances → Snapshots → Create Snapshot).
- Record the snapshot id and time in `product/deploy/DEPLOY_LOG.md` and in your incident notes.

2) Restore snapshot to a second instance
- In Vultr panel, create a new instance from the snapshot. Wait until it gets a public IP.
- Note the standby public IP (we'll call it STANDBY_IP). Confirm SSH (root or provisioning key) works.

3) How to populate standby (two options)
- Option A — dry-run plan (recommended first): from the primary, run:
  - `product/deploy/deploy.sh --standby <STANDBY_IP>`
  - This prints the exact plan: which files will be copied, which commands will be run on the standby and how health will be validated. It does not make changes.
- Option B — apply (perform copy and restart):
  - `product/deploy/deploy.sh --standby <STANDBY_IP> --apply`
  - Behavior:
    - Copies `/opt/concorde/bin/concorde` (binary)
    - Copies `/opt/concorde/artifacts/` (model/artifact files) when present
    - Copies `/opt/concorde/.env`
    - Ensures permissions: `chown -R concorde:concorde /opt/concorde`, binary `755`, `.env` `600`
    - Runs: `systemctl daemon-reload && systemctl restart concorde-api` on the standby host
    - Validates standby via `http://<STANDBY_IP>:8080/health`

If SSH uses a non-standard port, add `--ssh-port <port>` to the `deploy.sh` invocation.

4) Allowed / Forbidden operations during judging
- Allowed:
  - Restart the service (`systemctl restart concorde-api`) on the primary or standby.
  - Switch traffic to standby (DNS, load balancer, or manual switch) and edit environment variables (`/opt/concorde/.env`) if an operator confirms the change.
  - Reboot, snapshot restore, and filesystem-level fixes on the instance.
- Forbidden:
  - Any code changes to the repository or binary builds during judging (no `git pull`, no compiling, no replacing the binary with a locally-built variant).
  - Uploading new models / artifacts that were not part of the judged snapshot.
  - Any operation that modifies the feature contract, inference behavior, or the judgeable artifact set.

5) One-liner health watch (operator on-call)
- Quick one-liner (runs forever, prints timestamped OK/FAIL and beeps on failures):
  - `while true; do curl -s -f http://<IP>:8080/health >/dev/null && echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) OK" || (echo -e "\a$(date -u +%Y-%m-%dT%H:%M:%SZ) FAIL";); sleep 15; done`
- Preferred: use the shipped watcher:
  - `product/deploy/watch_health.sh http://<IP>:8080/health 120`  # runs 2 minutes for verification

6) Who to call (escalation)
- Primary on-call (in order):
 1. Paul — infrastructure & deployment lead
 2. Diego — modeling lead
 3. Néstor — frontend / dashboard

7) Restart commands (exact)
- On the instance (primary or standby), the exact commands the runbook uses are:
  - `systemctl daemon-reload`
  - `systemctl restart concorde-api`
  - Check logs: `journalctl -u concorde-api -f`

8) Verification checklist (before confirming standby)
- Snapshot exists in Vultr and snapshot id is recorded.
- Standby VM created from snapshot and SSH is reachable.
- `deploy.sh --standby <ip>` dry-run printed expected plan.
- `deploy.sh --standby <ip> --apply` completed and `http://<ip>:8080/health` returned 200.
- `product/deploy/watch_health.sh http://<ip>:8080/health 120` ran for 120s with no failures.

9) Notes
- The `--apply` path uses SSH as root to copy files and restart the service. Ensure the target host accepts the same SSH key or has passwordless root SSH via the provisioned key.
- During the judged window, changing code or model artifacts is not allowed — restores and restarts only.

