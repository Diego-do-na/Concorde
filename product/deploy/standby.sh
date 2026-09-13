#!/usr/bin/env bash
# Standby population helper: copy current /opt/concorde install to a standby host.
# Usage: standby.sh --standby <ip> [--apply] [--ssh-port <port>]

set -euo pipefail

STANDBY_IP=""
STANDBY_APPLY="false"
SSH_PORT=22

while [[ $# -gt 0 ]]; do
  case "$1" in
    --standby)
      STANDBY_IP="$2"
      shift 2
      ;;
    --apply)
      STANDBY_APPLY="true"
      shift
      ;;
    --ssh-port)
      SSH_PORT="$2"
      shift 2
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

if [ -z "$STANDBY_IP" ]; then
  echo "Usage: $0 --standby <ip> [--apply] [--ssh-port <port>]"
  exit 2
fi

INSTALL_DIR="/opt/concorde"
BINARY_DST="${INSTALL_DIR}/bin/concorde"
ARTIFACTS_DST="${INSTALL_DIR}/artifacts"

REMOTE_ROOT="root@${STANDBY_IP}"
echo "Planned standby actions against ${REMOTE_ROOT}:"
cat <<EOF
  - Ensure /opt/concorde exists
  - Copy binary: ${BINARY_DST} -> /opt/concorde/bin/$(basename ${BINARY_DST})
  - Copy artifacts: ${ARTIFACTS_DST} -> /opt/concorde/artifacts/
  - Copy env: ${INSTALL_DIR}/.env -> /opt/concorde/.env
  - Set ownership: concorde:concorde
  - Run: systemctl daemon-reload && systemctl restart concorde-api
  - Validate: curl -s -f http://${STANDBY_IP}:8080/health
EOF

if [ "$STANDBY_APPLY" != "true" ]; then
  echo "Dry-run only. Re-run with --apply to execute."
  exit 0
fi

echo "Applying to ${REMOTE_ROOT}..."
ssh -p ${SSH_PORT} ${REMOTE_ROOT} "mkdir -p /opt/concorde/bin /opt/concorde/artifacts || true"

if [ -f "${BINARY_DST}" ]; then
  scp -P ${SSH_PORT} "${BINARY_DST}" "${REMOTE_ROOT}:/opt/concorde/bin/$(basename ${BINARY_DST})"
else
  echo "Warning: binary not found at ${BINARY_DST}"
fi

if [ -d "${ARTIFACTS_DST}" ]; then
  ( cd "${ARTIFACTS_DST}" && tar -czf - . ) | ssh -p ${SSH_PORT} ${REMOTE_ROOT} "tar -xzf - -C /opt/concorde/artifacts || true"
else
  echo "No artifacts directory at ${ARTIFACTS_DST}; skipping"
fi

if [ -f "${INSTALL_DIR}/.env" ]; then
  scp -P ${SSH_PORT} "${INSTALL_DIR}/.env" "${REMOTE_ROOT}:/opt/concorde/.env"
else
  echo "Warning: ${INSTALL_DIR}/.env not found"
fi

ssh -p ${SSH_PORT} ${REMOTE_ROOT} bash -s <<'EOF'
set -e
chown -R concorde:concorde /opt/concorde || true
chmod 755 /opt/concorde/bin/concorde || true
chmod 600 /opt/concorde/.env || true
systemctl daemon-reload || true
systemctl restart concorde-api || true
EOF

echo "Validating standby health..."
if curl -s -f "http://${STANDBY_IP}:8080/health" > /dev/null 2>&1; then
  echo "Standby healthy"
else
  echo "Standby /health failed; check journalctl -u concorde-api on the standby host"
  exit 3
fi

echo "Standby provisioning complete."

