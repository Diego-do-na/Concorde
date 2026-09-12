#!/bin/bash
# CONCORDE Vultr bootstrap script (idempotent)
# Run as root: ssh -p 2222 root@100.93.147.55 'bash -s' < bootstrap.sh
# Or: curl -s https://REPO_URL/product/deploy/bootstrap.sh | bash

set -e
set -x  # Log commands for verification

# ---- System updates and dependencies ----
apt-get update
apt-get install -y \
    build-essential \
    pkg-config \
    libssl-dev \
    cmake \
    libclang-dev \
    caddy \
    git \
    ufw

# ---- Swap: create 4GB swapfile if it doesn't exist ----
if [ ! -f /swapfile ]; then
    # If a swapfile already exists at a different size, we assume it's been set up manually
    # For idempotency, check if swap is active and skip if so
    if swapon --show | grep -q swapfile; then
        echo "Swap already configured (skipping swapfile creation)"
    else
        fallocate -l 4G /swapfile
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
    fi
fi

# Add to /etc/fstab if not already present
if ! grep -q '/swapfile' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

# ---- System user: concorde ----
if ! id concorde &>/dev/null; then
    useradd -r -s /bin/bash -d /opt/concorde -m concorde
fi

# ---- Directory structure ----
mkdir -p /opt/concorde/{bin,artifacts,console}
chown -R concorde:concorde /opt/concorde

# ---- Rustup for the concorde user ----
if [ ! -d /opt/concorde/.cargo ]; then
    sudo -u concorde bash -c 'curl --proto "=https" --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --default-toolchain stable'
fi

# ---- UFW firewall configuration ----
# Reset to known state first (idempotent)
ufw --force enable

# Allow SSH (keep port 22 open as it's already allowed and we're using 2222 for Tailscale)
ufw allow 22/tcp
ufw allow 2222/tcp
ufw allow 80/tcp
ufw allow 443/tcp

echo "UFW configured with ports: 22/tcp, 2222/tcp, 80/tcp, 443/tcp"

# ---- Create .env from template with mode 600 ----
if [ -f /opt/concorde/.env ] && grep -q "^CONCORDE_BIND=" /opt/concorde/.env; then
    echo ".env already exists, backing up to .env.bak"
    cp /opt/concorde/.env /opt/concorde/.env.bak
fi

# Copy template if it exists locally, otherwise skip (values will be filled by hand)
if [ -f /tmp/env.server.template ]; then
    cp /tmp/env.server.template /opt/concorde/.env
fi

# Ensure correct permissions on .env
chmod 600 /opt/concorde/.env
chown concorde:concorde /opt/concorde/.env

# ---- Verification ----
echo ""
echo "=== Bootstrap verification ==="
echo "Memory:"
free -h
echo ""
echo "Swap status:"
swapon --show
echo ""
echo "User concorde:"
id concorde
echo ""
echo "Caddy version:"
caddy version
echo ""
echo "CMake version:"
cmake --version | head -1
echo ""
echo "Public IP:"
curl -4 -s ifconfig.me
echo ""
echo "UFW status:"
ufw status numbered
echo ""
echo "Directory structure:"
ls -la /opt/concorde/
echo ""
echo "=== Bootstrap complete ==="
