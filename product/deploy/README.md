# CONCORDE Deployment Infrastructure (T023)

This directory contains production deployment configuration for the CONCORDE API service:
- Caddy reverse proxy with automatic HTTPS/Let's Encrypt
- systemd service unit for the Rust API binary
- Deployment script for building, installing, and activating the service

## File Inventory

| File | Purpose | Owner | Verification |
|------|---------|-------|---|
| `Caddyfile` | Caddy reverse proxy config (HTTPS, Let's Encrypt, routing to `127.0.0.1:8080`) | Paul (ops) | `caddy validate --config Caddyfile` |
| `concorde-api.service` | systemd unit file (User: `concorde`, Restart: always, Memory: 2500M) | Paul (ops) | `systemd-analyze verify concorde-api.service` |
| `deploy.sh` | Deployment script (build, install, restart, health check) | Paul (ops) | `bash -n deploy.sh` |
| `env.server.template` | Environment variable template (copied to `/opt/concorde/.env` on server) | Diego (modeling) | Manual setup on server |
| `DOMAIN.md` | Domain & DNS records for `getconcorde.tech` (Task T054) | Néstor (frontend) | Verified DNS propagation |
| `SERVER.md` | Vultr instance details, bootstrap, user setup (Task T003) | Paul (ops) | Manual verification |
| `bootstrap.sh` | One-time server setup (user, directories, dependencies, swap) | Paul (ops) | Idempotent; safe to re-run |

## Deployment Workflow

### Prerequisites (one-time setup)

1. **Server bootstrap** (Task T003, already done):
   ```bash
   # On the server (via Tailscale):
   # ssh -p 2222 root@100.93.147.55 'bash -s' < product/deploy/bootstrap.sh
   # This creates concorde user, /opt/concorde directories, Caddy, Rustup, etc.
   ```

2. **Domain & DNS** (Task T054, already done):
   - Domain: `getconcorde.tech` (registered)
   - A records: `@`, `api`, `console` → `216.238.90.138`
   - DNS propagated and verified globally

3. **Environment secrets** (manual, one-time):
   ```bash
   scp -P 2222 product/deploy/env.server.template root@100.93.147.55:/opt/concorde/.env
   ssh -p 2222 root@100.93.147.55
   # On server:
   nano /opt/concorde/.env  # Fill in TIGERDATA_URL, etc. (no external LLM keys: the semantic layer is fully local)
   chown concorde:concorde /opt/concorde/.env
   chmod 600 /opt/concorde/.env
   ```

### Deployment Steps (Task T024, the actual deploy)

Run this sequence once:

```bash
# 1. Copy Caddyfile to server
scp -P 2222 product/deploy/Caddyfile root@100.93.147.55:/etc/caddy/Caddyfile

# 2. Copy systemd service file
scp -P 2222 product/deploy/concorde-api.service root@100.93.147.55:/etc/systemd/system/

# 3. Run deployment script (builds binary, installs, restarts service)
# This runs: cargo build --release, copies binary + artifacts, updates GIT_SHA, systemctl restart
ssh -p 2222 root@100.93.147.55 'bash -s' < product/deploy/deploy.sh

# 4. Reload Caddy to pick up the new Caddyfile
ssh -p 2222 root@100.93.147.55 'systemctl reload caddy'

# 5. Verify the deployment
ssh -p 2222 root@100.93.147.55 'systemctl status concorde-api'
```

Or, equivalently, in one SSH session:
```bash
scp -P 2222 product/deploy/Caddyfile root@100.93.147.55:/etc/caddy/
scp -P 2222 product/deploy/concorde-api.service root@100.93.147.55:/etc/systemd/system/
ssh -p 2222 root@100.93.147.55 'bash -s' < product/deploy/deploy.sh
ssh -p 2222 root@100.93.147.55 'systemctl reload caddy && sleep 2 && systemctl status concorde-api'
```

### Subsequent Deployments (code updates)

For code changes (binary, artifacts, or environment tweaks), re-run only:
```bash
ssh -p 2222 root@100.93.147.55 'bash -s' < product/deploy/deploy.sh
# This rebuilds from latest origin/main, restarts the service, and checks /health
```

For Caddyfile or systemd unit changes:
```bash
# Update config files
scp -P 2222 product/deploy/Caddyfile root@100.93.147.55:/etc/caddy/
scp -P 2222 product/deploy/concorde-api.service root@100.93.147.55:/etc/systemd/system/

# Reload (systemd) and validate (Caddy)
ssh -p 2222 root@100.93.147.55 '
  systemctl daemon-reload
  systemctl restart concorde-api
  caddy reload --config /etc/caddy/Caddyfile
'
```

## Critical Rule: Judge URL & Fallback

### Judge's endpoint (MUST be HTTPS with a valid certificate):
```
https://getconcorde.tech/detect     (primary URL, given to judge)
https://api.getconcorde.tech/detect (alias, also valid)
```

### Fallback (emergency only, never given to judge):
```
http://216.238.90.138/detect        (IP-only, HTTP, fallback only)
```

**Why the fallback exists** (§8.1, ADR-006):
- If DNS fails or domain is inaccessible, the judge client *may* retry on the IP-only fallback.
- Caddy's systemd logs this as "degraded" when the fallback is used.
- This is never the URL handed to the official judge; it's a contingency (§18.3).

**Configuration**:
- Primary block in Caddyfile: `getconcorde.tech { tls { ... } ... }`
  - Automatically obtains Let's Encrypt cert on first request to any HTTPS endpoint
  - HTTP (:80) requests to the domain are redirected to HTTPS
  - Reverse-proxies `/detect`, `/analyze`, `/health`, `/metrics`, etc. to `127.0.0.1:8080`

- Fallback block in Caddyfile: `:80 { ... }`
  - Serves HTTP on all IPs (including `216.238.90.138`) as last resort
  - Sets header `X-Fallback-IP: true` so the API logs it as degraded (CONCORDE_STRICT=0)

## Environment Variables

**Location**: `/opt/concorde/.env` (mode 600, concorde:concorde)

**Template**: `product/deploy/env.server.template` (committed to repo, values filled by hand)

**Key variables** (spec §18.2):
- `CONCORDE_BIND=127.0.0.1:8080` — API listens here; Caddy proxies to it
- `CONCORDE_MODEL_PATH=/opt/concorde/artifacts/model.onnx` — Model file location
- `CONCORDE_FEATURE_CONTRACT=fc-1` — Feature vector version (must match model.onnx)
- `CONCORDE_THRESHOLD=` — Override model's threshold (leave empty to use model's default)
- `CONCORDE_MAX_BODY_BYTES=16777216` — 16 MB (FR-003 max with 4-min call)
- `CONCORDE_HANDLER_TIMEOUT_MS=20000` — 20 sec per /detect request (judge allows 30 s)
- `CONCORDE_STRICT=0` — Production: don't propagate errors, always return 200 + fallback verdict
- `CONCORDE_SEMANTIC_ENABLED=false` — Keep false until ASR budget is validated (T045, T057)
- `CONCORDE_SEMANTIC_TIMEOUT_MS=1500` — Hard timeout on /detect (ADR-008)
- `GIT_SHA=` — Filled by deploy.sh at deploy time; shown by /health and /version

**Secrets** (filled by hand, **never committed**):
- `TIGERDATA_URL` — PostgreSQL connection for structured logging (optional, COULD priority)

## Service Management

**Start/stop**:
```bash
ssh -p 2222 root@100.93.147.55
# On server:
systemctl start concorde-api         # Start the service
systemctl stop concorde-api          # Stop the service
systemctl restart concorde-api       # Restart (what deploy.sh does)
systemctl status concorde-api        # Check status
systemctl enable concorde-api        # Auto-start on reboot
```

**Logs**:
```bash
ssh -p 2222 root@100.93.147.55
# On server:
journalctl -u concorde-api -f        # Follow live logs
journalctl -u concorde-api -n 100    # Last 100 lines
journalctl -u concorde-api --since "2 hours ago"
```

**Health check**:
```bash
# From local machine:
curl -vI https://getconcorde.tech/health

# From the server (internal):
curl -I http://127.0.0.1:8080/health

# Response should be:
# HTTP/1.1 200 OK
# {"is_healthy": true, "version": "...", "git_sha": "..."}
```

## Troubleshooting

### Service won't start
```bash
systemctl status concorde-api
journalctl -u concorde-api -n 50
# Common issues:
#  - Binary not at /opt/concorde/bin/concorde-api (deploy.sh failed?)
#  - /opt/concorde/.env missing or not readable by concorde
#  - CONCORDE_MODEL_PATH file doesn't exist
#  - Port 8080 already in use (check: lsof -i :8080)
```

### /health endpoint not responding (HTTP 500 or timeout)
```bash
curl -vI http://127.0.0.1:8080/health
journalctl -u concorde-api -f
# Common issues:
#  - Model file missing or corrupted
#  - Feature contract mismatch (CONCORDE_FEATURE_CONTRACT != model.onnx metadata)
#  - Out of memory (check: free -h, MemoryMax=2500M)
#  - Semantic layer timeout (if CONCORDE_SEMANTIC_ENABLED=true and ASR is slow)
```

### Caddy errors (certificate, proxy)
```bash
systemctl status caddy
journalctl -u caddy -f
# Validate Caddyfile:
caddy validate --config /etc/caddy/Caddyfile
# Reload if config changed:
systemctl reload caddy
```

### Judge client fails to connect
```bash
# Test from judge's machine (or simulate with curl):
curl -vI https://getconcorde.tech/detect
# Should return 200 with valid Let's Encrypt certificate
openssl s_client -connect getconcorde.tech:443
# Should show: CN=getconcorde.tech, O=Let's Encrypt
```

## Specifications & References

- **CONCORDE spec**: `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf`
  - §8 — API contract (request/response format, timeouts)
  - §8.1 — POST /detect contract and scoring rules
  - §18.2 — Environment variables
  - §18.3 — Contingency & fallback rules

- **ADRs referenced**:
  - ADR-001: Rust + LightGBM stack (no deep learning)
  - ADR-002: Lightweight features, generalize to unseen TTS engines
  - ADR-003: Own VAD in inference path
  - ADR-006: Always return 200 on /detect, fallback verdict on error
  - ADR-008: Semantic timeout + unmarked degradation logging
  - ADR-009: Accept all four input shapes (raw base64, JSON variants, multipart, binary WAV)
  - ADR-013: Deploy.sh as single source of deploy truth

- **Related tasks**:
  - T003 — Server bootstrap & Vultr setup
  - T004 — React console deployment
  - T024 — First public deployment (run deploy.sh live)
  - T045 — Whisper.cpp ASR performance measurement
  - T054 — Domain & DNS (getconcorde.tech)
  - T056 — Final README

## Next Steps

1. **T024** — Run `deploy.sh` for the first time on the live Vultr instance
2. **T024** — Verify HTTPS certificate: `curl -vI https://getconcorde.tech/health`
3. **T024** — Run judge's test client against `https://getconcorde.tech/detect`
4. **T045** — Measure and tune ASR (whisper.cpp) on the server hardware
5. **T057** — Choose whisper.cpp model (tiny vs. base) based on T045 benchmarks

## Console (React) — deploy & verify

When the console (React SPA) is ready, deploy it to `/opt/concorde/console` and verify end-to-end:

Deploy:
```bash
./product/deploy/deploy-console.sh 100.93.147.55 2222 root
```

Feed the live table by replaying validation clips:
```bash
./product/deploy/replay_val.sh /path/to/val_wavs getconcorde.tech 20
```

Verification:
- Visit https://getconcorde.tech/ — the Live feed should show the replayed calls.
- Click a call → Detail view shows waveform, markers, trace, and /analyze factors.
- Upload a demo clip via the UI and confirm Exec / Detail show the two-key body and placeholder stats.
- All three views reachable within ~20s from landing on a modest laptop.


---

**Created**: 2026-09-12  
**Task**: T023 — Caddy + systemd + deploy.sh  
**Author**: Claude Haiku 4.5  
**Status**: Implementation complete; ready for T024 (first deploy)
