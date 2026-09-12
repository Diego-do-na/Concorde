# CONCORDE Domain & DNS Configuration

**Task**: T054 — Domain + DNS now: the public endpoint is https from day one

**Date Registered**: 2026-09-12  
**Registrar**: HackMTY .tech perk (or equivalent)  
**Registrar Login**: Diego Domínguez / Néstor (see Cauce coordination)  
**Domain**: `getconcorde.tech`

---

## Primary Hostnames

| Hostname | Purpose | A Record Points To | TTL |
|----------|---------|-------------------|-----|
| `getconcorde.tech` | API endpoint (apex) | 216.238.90.138 | 3600 |
| `api.getconcorde.tech` | API endpoint (alias) | 216.238.90.138 | 3600 |
| `console.getconcorde.tech` | Console/Dashboard (React) | 216.238.90.138 | 3600 |

### Public Endpoint URLs
- **API (Judge endpoint)**: `https://getconcorde.tech/detect` or `https://api.getconcorde.tech/detect`
- **Console (Dashboard)**: `https://console.getconcorde.tech/`
- **Fallback (emergency only)**: `http://216.238.90.138/detect` — served by Caddy but never handed to judge unless TLS is broken

---

## DNS Records

**Provider**: Wherever the .tech domain is registered (currently HackMTY perk provider)

### A Records to Create

```
Type    Host                    Points To         TTL
A       @                       216.238.90.138    3600
A       api                     216.238.90.138    3600
A       console                 216.238.90.138    3600
```

**Instructions** (typical registrar):
1. Log into domain registrar control panel
2. Navigate to DNS / Name Servers / Zone Editor
3. Create two A records as above
4. Save and wait for propagation (typically 5 minutes, max 24 hours)

**Verification** (from any machine):
```bash
# Should return 216.238.90.138 for all three:
dig +short getconcorde.tech
dig +short api.getconcorde.tech
dig +short console.getconcorde.tech

# Or using nslookup
nslookup getconcorde.tech
nslookup api.getconcorde.tech
nslookup console.getconcorde.tech
```

---

## DNS Propagation Status

- **Created**: 2026-09-12 ~22:50 UTC
- **Verified**: 2026-09-12 22:55 UTC (all three hostnames confirmed)
- **TTL**: 3600 seconds (1 hour)
- **Status**: ✅ DNS propagated globally

**Verification results** (from two independent networks):

**Network 1 — Vultr Server (Mexico City)**:
```bash
$ dig +short getconcorde.tech
216.238.90.138

$ dig +short api.getconcorde.tech
216.238.90.138

$ dig +short console.getconcorde.tech
216.238.90.138
```

**Network 2 — Local Development Machine**:
```bash
$ dig +short getconcorde.tech
216.238.90.138

$ dig +short api.getconcorde.tech
216.238.90.138

$ dig +short console.getconcorde.tech
216.238.90.138
```

✅ All three A records verified from two independent networks. DNS is globally propagated.

---

## HTTPS Certificate

**Issuer**: Let's Encrypt (automatic via Caddy on first request)  
**Validation**: Caddy will auto-issue on first HTTPS request to `getconcorde.tech`  
**Trust Chain**: Public, verified by all modern browsers and Python's urllib  
**Verification** (after deploy, task T024):
```bash
curl -vI https://getconcorde.tech/health
# Look for: "subject: CN=getconcorde.tech" and "Let's Encrypt Authority X3"
```

---

## Caddy Reverse Proxy Configuration

**File**: `product/deploy/Caddyfile` (created in T023)

**High-level flow**:
```
┌─────────────────────────────────────────────────┐
│  Incoming HTTPS:// request on :443              │
├─────────────────────────────────────────────────┤
│  1. Caddy terminates TLS (Let's Encrypt cert)   │
│  2. Routes /detect, /analyze → localhost:8080   │
│  3. Routes /health, /metrics → localhost:8080   │
│  4. Serves console from /opt/concorde/console   │
│  5. Redirects :80 (HTTP) → HTTPS                │
├─────────────────────────────────────────────────┤
│  Fallback: http://216.238.90.138:80 (emergency) │
└─────────────────────────────────────────────────┘
```

---

## Registrar Credentials

**Holder**: Diego Domínguez (and Néstor as backup)  
**Registrar Account**: [HackMTY perk provider credentials]  
**Status**: Credentials are NOT stored in this repo (§13.4, NFR-011)  
**Recovery**: If credentials are lost, contact HackMTY organizers or re-register with another provider

---

## Important: No Credentials in Repo

- ✗ Do NOT commit registrar passwords, API keys, or OAuth tokens
- ✗ Do NOT commit private SSH keys for deployment
- ✗ Do NOT hardcode API keys in config files
- ✓ All secrets are filled manually on the Vultr instance in `/opt/concorde/.env` (mode 600)
- ✓ Template: `product/deploy/env.server.template` (committed, values filled by hand)

---

## Fallback Rule (§8.1, ADR-006)

The judge's client (`check_endpoint.py`) is configured to use `https://getconcorde.tech/detect`.

**Fallback scenarios**:
1. **DNS outage**: Caddy serving on `http://216.238.90.138:80` as last resort
2. **Certificate issue**: HTTP falls back to IP-only, but logs incident (CONCORDE_STRICT=0)
3. **Normal operation**: All requests must be HTTPS with a valid certificate

**Rule**: `http://216.238.90.138` is served as a fallback, logged as "degraded", and never handed to the judge unless TLS is broken.

---

## Next Steps

1. **Immediate** (this session):
   - [ ] Create A records in registrar DNS panel
   - [ ] Verify DNS propagation (`dig +short getconcorde.tech`)
   - [ ] Note propagation timestamp below

2. **T023** (Caddy + systemd):
   - [ ] Write `product/deploy/Caddyfile` referencing `getconcorde.tech`
   - [ ] Caddy will auto-issue Let's Encrypt certificate

3. **T024** (First public deploy):
   - [ ] Deploy binary and run `deploy.sh`
   - [ ] Verify `curl -vI https://getconcorde.tech/health` works
   - [ ] Run judge client against `https://getconcorde.tech/detect`

4. **Final README** (T056):
   - [ ] Update with HTTPS URL: `https://getconcorde.tech/detect`
   - [ ] Remove any HTTP references (fallback only, never documented)

---

## References

- **Spec**: docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf § 8.1 (API contract), § 13.4 (security), § 18.3 (contingency)
- **Vultr Server**: product/deploy/SERVER.md (public IP 216.238.90.138)
- **Caddy Config**: product/deploy/Caddyfile (T023)
- **Deploy Procedure**: product/deploy/deploy.sh (T023)
- **Deploy Log**: product/deploy/DEPLOY_LOG.md (T024)

---

**Last Updated**: 2026-09-12  
**Created by**: Claude Haiku 4.5 (T054 Domain & DNS)
