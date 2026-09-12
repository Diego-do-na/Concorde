# CONCORDE Vultr Deployment — Server Configuration

**Task**: T003 — Vultr: admin access via Tailscale, confirm public IP, team keys, swap, bootstrap script

**Date**: 2026-09-12  
**Instance ID**: 1ef4693f-0082-49fb-ae60-8dd8ba52cead  
**Instance Type**: vc2-2c-4gb  
**Region**: Mexico City  
**OS**: Ubuntu 24.04 LTS  

---

## Network Addresses (CRITICAL: do not confuse)

### Admin Access (Tailscale, Private)
- **Address**: `100.93.147.55` (private CGNAT, Tailscale only)
- **Port**: 2222 (SSH)
- **Access**: `ssh -p 2222 root@100.93.147.55`
- **Note**: Reachable only from devices in the Tailscale network. Diego's SSH keys are already configured.

### Public Endpoint (Judge's Entry Point)
- **Address**: `216.238.90.138` (public IPv4, Vultr-assigned)
- **Ports**: 80 (HTTP), 443 (HTTPS)
- **Role**: This is where the graded system sends `/detect` and `/analyze` requests.
- **Access**: HTTP fallback only; primary endpoint is domain-based (see `product/deploy/DOMAIN.md`).
- **Verification**: 
  ```bash
  curl -4 -s ifconfig.me  # Run on Vultr instance to confirm
  # Should return: 216.238.90.138
  ```

---

## System Configuration

### RAM and Swap
```
Total RAM:     3.8 GiB
Available:     3.2 GiB (after system overhead)
Swap File:     7.7 GiB
Swap Status:   Active, mounted at /swapfile
```

**Swap Configuration**:
- Location: `/swapfile` (4+ GiB, idempotent via bootstrap.sh)
- Entry in `/etc/fstab`: `/swapfile none swap sw 0 0`
- **Mandatory** before compiling Rust on 4 GiB RAM (prevents OOM during build).

### Firewall (UFW)
```
Status: active

Allowed ports:
  22/tcp        — SSH (fallback, keep open per task decisions)
  2222/tcp      — SSH via Tailscale (admin access)
  80/tcp        — HTTP (Caddy proxy)
  443/tcp       — HTTPS (Caddy proxy, certificate managed by Caddy)
```

**Configuration**:
```bash
ufw --force enable
ufw allow 22/tcp
ufw allow 2222/tcp
ufw allow 80/tcp
ufw allow 443/tcp
```

---

## Installed Software

| Package | Version | Purpose |
|---------|---------|---------|
| build-essential | latest | Rust compilation (gcc, make) |
| pkg-config | latest | Library configuration metadata |
| libssl-dev | latest | OpenSSL headers (Rust TLS) |
| cmake | 3.28.3 | whisper.cpp build (T045) |
| libclang-dev | latest | whisper-rs compilation support |
| caddy | 2.6.2 | Reverse proxy, HTTPS termination, certificate management |
| git | latest | Version control |
| ufw | latest | Firewall (Uncomplicated Firewall) |
| rustup | 1.29.1 | Rust toolchain manager (for `concorde` user) |

**Verification**:
```bash
ssh -p 2222 root@100.93.147.55 'free -h; swapon --show; id concorde; caddy version; cmake --version; curl -4 -s ifconfig.me'

# Output (2026-09-12):
# Mem:           3.8Gi       641Mi       692Mi       1.0Mi       2.8Gi       3.2Gi
# Swap:          7.7Gi       780Ki       7.7Gi
# Swap file:     7.7G        780K        -2
# uid=996(concorde) gid=987(concorde) groups=987(concorde)
# caddy version: 2.6.2
# cmake version 3.28.3
# 216.238.90.138
```

---

## System User: `concorde`

- **UID**: 996
- **GID**: 987
- **Shell**: `/bin/bash`
- **Home**: `/opt/concorde`
- **Ownership**: All files under `/opt/concorde` are owned by `concorde:concorde`

### Directory Structure

```
/opt/concorde/
├── bin/                    # Deployment binaries (concorde-api, etc.)
├── artifacts/              # Model files (model.onnx, semantic_fusion.json, etc.)
├── console/                # Dashboard assets (built from console/ repo)
├── .env                    # Environment variables (mode 600, concorde:concorde)
├── .env.bak                # Backup of previous .env (from bootstrap)
├── .cargo/                 # Rust toolchain (installed by rustup)
└── .rustup/                # Rust version manager
```

**Permissions**:
```bash
/opt/concorde/       755 (drwxr-xr-x) owner: concorde:concorde
/opt/concorde/.env   600 (-rw-------)  owner: concorde:concorde
```

---

## Environment Configuration

**File**: `/opt/concorde/.env`

- **Mode**: 600 (readable/writable by owner only)
- **Owner**: concorde:concorde
- **Template**: `product/deploy/env.server.template` (copied by bootstrap.sh)
- **Values**: Filled by hand on the server; **never committed to git**.
- **Loaded by**: systemd service `concorde-api.service` via `EnvironmentFile=`

**Key Variables** (spec §18.2):
```
CONCORDE_BIND=127.0.0.1:8080            # Caddy proxies :80/:443 → this
CONCORDE_MODEL_PATH=/opt/concorde/artifacts/model.onnx
CONCORDE_FEATURE_CONTRACT=fc-1
CONCORDE_THRESHOLD=                     # (empty = use model's meta.json)
CONCORDE_MAX_BODY_BYTES=16777216        # 16 MB
CONCORDE_HANDLER_TIMEOUT_MS=20000       # 20 sec
CONCORDE_STRICT=0                       # 0 in production
CONCORDE_SEMANTIC_ENABLED=false         # Until ASR budget is validated
CONCORDE_SEMANTIC_TIMEOUT_MS=1500       # Hard timeout on /detect
```

---

## Bootstrap Script

**File**: `product/deploy/bootstrap.sh`

**Idempotency**: Safe to run multiple times; skips already-completed steps.

**What it does**:
1. System updates (`apt update`)
2. Install build dependencies and tools
3. Create/enable 4 GiB swapfile if needed
4. Create `concorde` system user
5. Create `/opt/concorde/{bin,artifacts,console}` directories
6. Install Rust toolchain for `concorde` user via rustup
7. Configure UFW with required ports
8. Copy `.env` template with correct permissions
9. Verify all components via diagnostic output

**Usage**:
```bash
# Option A: Copy and run locally
scp -P 2222 product/deploy/bootstrap.sh root@100.93.147.55:/tmp/
ssh -p 2222 root@100.93.147.55 'bash /tmp/bootstrap.sh'

# Option B: Pipe over SSH (requires repo access)
curl -s https://REPO_URL/product/deploy/bootstrap.sh | ssh -p 2222 root@100.93.147.55 bash
```

---

## SSH Key Management

### Root (Admin) Access Team (Tailscale, private network):
- Diego: ✓ Keys already installed
- Paul: ⚠️ **TODO** — append public key to `/root/.ssh/authorized_keys`
- Néstor: ⚠️ **TODO** — append public key to `/root/.ssh/authorized_keys`

**To add a team member's key**:
```bash
ssh -p 2222 root@100.93.147.55
# On server:
echo "PUBLIC_KEY_HERE" >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
```

### GitHub Deploy Key (for `concorde` user)

**Generated**: 2026-09-12 (T024 deployment)  
**Type**: Ed25519 SSH key  
**Location on server**: `/opt/concorde/.ssh/id_ed25519` (private) and `/opt/concorde/.ssh/id_ed25519.pub` (public)  
**Purpose**: Allows deploy.sh to clone/update code from private GitHub repository

**Public key** (register on GitHub):
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIFxG4F15a27mfJcHX06IDi8QxFNVxA5FLqJbvQLys89Z concorde@getconcorde.tech
```

**GitHub registration** (Diego or Paul):
1. Log into GitHub account (Diego-do-na or Néstor's)
2. Navigate to Concorde repo → Settings → Deploy Keys
3. Click "Add deploy key"
4. Title: `CONCORDE Deploy (Vultr)`
5. Paste public key above
6. Check "Allow write access" (optional, only needed if deploy script must push)

**Verification** (after registration):
```bash
ssh -p 2222 root@100.93.147.55 'su - concorde -c "ssh -T git@github.com"'
# Should show: "Hi Diego-do-na/Concorde! You have read access to this repository."
```

**Known hosts**:
```bash
ssh -p 2222 root@100.93.147.55 'cat /opt/concorde/.ssh/known_hosts | grep github.com'
# Should show github.com's public host keys (added during T024 deploy)
```

---

## Caddy Configuration

**Caddy** (reverse proxy, installed as systemd service on many Ubuntu setups):
- **Version**: 2.6.2
- **Purpose**: Terminate HTTPS, proxy `/detect` and `/analyze` to `127.0.0.1:8080`
- **Cert Management**: Automatic (Let's Encrypt or manual per `product/deploy/DOMAIN.md`)
- **Config Location**: TBD (typically `/etc/caddy/Caddyfile` or managed by systemd)

**Note**: Caddy configuration file is NOT part of this task; see `product/deploy/DOMAIN.md` (T006 or later) for domain and certificate setup.

---

## Contingency & Recovery (§18.3 of spec)

### If instance becomes unresponsive:
1. **Check Tailscale connectivity**: Verify device is connected to Tailscale network
2. **Reboot via Vultr console**: Use Vultr web panel to force restart
3. **Verify network**: After reboot, confirm public IP hasn't changed (`curl -4 -s ifconfig.me` from Vultr instance)
4. **Re-run bootstrap**: Re-execute `bootstrap.sh` to ensure all services are active

### If we need to rebuild:
1. Create new Vultr instance (same specs: vc2-2c-4gb, Ubuntu 24.04, Mexico City)
2. Add to Tailscale network
3. Run `bootstrap.sh`
4. Update `product/deploy/SERVER.md` with new addresses
5. Notify all teammates of new admin address (if Tailscale IP changes)

### If `/opt/concorde/.env` is lost:
```bash
ssh -p 2222 root@100.93.147.55
# On server:
cp /opt/concorde/.env.bak /opt/concorde/.env
chmod 600 /opt/concorde/.env
# Re-fill secrets by hand
```

---

## Verification Checklist

✓ Tailscale admin access working (`ssh -p 2222 root@100.93.147.55`)  
✓ Public IP confirmed: **216.238.90.138**  
✓ RAM: 3.8 GiB  
✓ Swap: 7.7 GiB (active)  
✓ User `concorde` created  
✓ Caddy 2.6.2 installed  
✓ CMake 3.28.3 installed  
✓ Rustup 1.29.1 installed for `concorde` user  
✓ UFW firewall active (22, 2222, 80, 443/tcp)  
✓ Directory structure: `/opt/concorde/{bin,artifacts,console}`  
✓ `.env` copied with mode 600  

---

## Next Steps

1. **T004 (or later)**: Deploy React console to `/opt/concorde/console`
2. **T005 (or later)**: Deploy compiled Rust API binary to `/opt/concorde/bin/concorde-api`
3. **T006 (or later)**: Configure Caddy and domain via `product/deploy/DOMAIN.md`
4. **T045 (later)**: Measure whisper.cpp performance, finalize ASR setup
5. **Add team SSH keys**: Append Paul's and Néstor's public keys to `/root/.ssh/authorized_keys`

---

**Last Updated**: 2026-09-12  
**Created by**: Claude Haiku 4.5 (T003 Bootstrap)
