# CONCORDE — Synthetic Voice Detection System

**Status**: Production deployment active at https://getconcorde.tech  
**Model**: concorde-b-2 (trained behavioral features + ONNX inference)  
**Accuracy**: Balanced 0.864 (71-call validation set); AUC 0.944  
**Latency**: p99 ≤ 585ms (< 1s hard limit)

## Thesis

Behavioral/conversational features are the **primary signal**, not acoustic. A lightweight Rust service provides:
- Defensive parsing (4 input shapes per FR-003, always HTTP 200)
- Real-time VAD (own implementation, not from /turns.json)
- Feature extraction (23 behavioral features, fc-1 contract frozen)
- ONNX inference (no deep learning, LightGBM export)

## Repository Structure

- **`product/`** — Rust API, deployment, infrastructure
  - `api/` — Axum service, WAV parsing, feature extraction, ONNX inference
  - `deploy/` — Vultr deployment, snapshot/standby runbook, Caddy config
  - `ml/` — Python reference feature extractor (parity validated against Rust)
  - `console/` — React dashboard (SPA served by Caddy)
  - `artifacts/` — ONNX model binary, feature contract (gitignored audio)

- **`ml/`** — ML pipeline (offline, never in serving path)
  - `features/` — Feature extraction (fc-1 contract)
  - `train/` — LightGBM training with speaker-grouped CV
  - `export/` — ONNX export
  - `validation/` — VAD F1 validation, parity checks
  - `eda/`, `semantic/` — Exploratory work, Gemini layer (optional)

- **`docs/`** — Specification, reports, runbooks
  - `CONCORDE_Especificacion_Tecnica_v1_0.pdf` — Full technical spec (§1–18)
  - `latency-report.md` — Final p50/p95/p99 measurements
  - `pre-judging-checklist.md` — Pre-submission verification results
  - `design-reference.html` — Console design system
  - `escalation-protocol.md` — Human-in-the-loop escalation (Green/Review/Transfer)

- **`orchestration/`** — Cauce task board (coordination tooling only)
  - No product code here; keep it separate

## Quick Start

### Deployment

```bash
# First time only (on server)
orchestration/scripts/setup.sh

# Claim + work on a task
orchestration/scripts/work.sh [owner] [agent]

# Finish and merge to main
orchestration/scripts/finish.sh <task-id>

# Dashboard
orchestration/scripts/dashboard.sh
```

### API

```bash
# Verify deployment is live
curl -s https://getconcorde.tech/health | jq .

# Run fault-injection suite (all 4 input shapes)
bash product/api/tests/fault_injection_remote.sh https://getconcorde.tech

# Test /detect endpoint
curl -X POST https://getconcorde.tech/detect \
  -H 'Content-Type: application/json' \
  -d '{"call_id":"test","audio_base64":"<base64-wav>","sample_rate":8000,"channels":2}'
```

### ML Pipeline

```bash
# Extract features (Python reference)
python ml/features/extract.py <wav-path>

# Validate VAD against turns.json
python ml/validation/vad_validator.py --split val

# Train + cross-validate
python ml/train/train.py --split train --out model.pkl

# Export to ONNX
python ml/export/export_onnx.py --model model.pkl --out model.onnx
```

## Key Constraints (Non-Negotiable)

1. **`POST /detect` always returns HTTP 200** with exactly `{"is_synthetic": <bool>, "confidence": <float in [0,1]>}` — no extra keys (ADR-006, ADR-013)
2. **Feature contract fc-1 is frozen** — 23 behavioral features in exact order; silent reordering = wrong verdicts (§9, AGENTS.md)
3. **Own VAD required** — must validate against turns.json files (F1 ≥ 0.85) before training (ADR-003, R-01)
4. **No speaker identification** — ever (§13.4, NFR-011)
5. **Defensive parsing** — accepts raw base64, JSON (any audio field name), multipart, raw WAV without config (FR-003, ADR-009)

## Known Limitations

| Issue | Impact | Workaround / Plan |
|-------|--------|-------------------|
| **Semantic layer (Gemini) disabled** | Loses optional ASR/semantic vote (lower priority per MoSCoW §3:SHOULD); behavioral signal sufficient | Enabled via `CONCORDE_SEMANTIC_ENABLED=1` + `GEMINI_API_KEY` after judging; hard timeout configured for safety |
| **Console manual browser testing pending** | 3-view load times not directly timed; landing page only (85ms verified) | Detail/Exec views designed for < 5s each; total estimate ~385ms (well under 20s requirement) |
| **Acoustic signal not integrated** | Avoids spurious correlation to training TTS artifacts; single-signal design (ADR-001) | Additive-only path exists in `ml/semantic/`; can add if behavioral signal insufficient in practice |
| **Tiger Data logging disabled** | No event streaming to external warehouse during judging | In-process logs available; post-judging integration covered by escalation-protocol.md |
| **No MongoDB/Snowflake/Solana** | Out of scope per MoSCoW (§3:WON'T) | Architecture supports pluggable logging; not a blocker for F1/F2 |
| **Domain `.tech` not fully configured** | Currently on getconcorde.tech; not a .tech FQDN | Full DNS/routing verified; production routing post-judging |

## Deployment & Operations

- **Status**: ✓ Live at https://getconcorde.tech (Vultr Mexico City instance)
- **Model version**: concorde-b-2 (real model, confirmed 2026-09-13)
- **Uptime**: 1516s+ (verified); systemd auto-restart enabled
- **Latency**: p95 = 568ms, p99 = 585ms (validated on 71 val clips)
- **Fallback**: Always returns `{"is_synthetic": false, "confidence": 0.50}` on error (never 5xx)

### Emergency Procedures

See `product/deploy/RUNBOOK.md` for:
- **Snapshot** — Vultr snapshot after real-model deploy
- **Restore** — Cold-standby instance from snapshot
- **Health watch** — One-liner monitoring; `watch_health.sh` script
- **Escalation** — Paul (infra) → Diego (modeling) → Néstor (frontend)
- **Allowed during judging** — Restart, standby switch, DNS change only
- **Forbidden during judging** — Code changes, model swaps, binary rebuilds

## Documentation Links

- **Technical Spec**: `docs/CONCORDE_Especificacion_Tecnica_v1_0.pdf` (source of truth)
- **Latency Report**: `docs/latency-report.md` (p50/p95/p99 + accuracy metrics)
- **Pre-Judging Checklist**: `docs/pre-judging-checklist.md` (verification results)
- **Deploy Log**: `product/deploy/DEPLOY_LOG.md` (T024/T025 deployment history)
- **Runbook**: `product/deploy/RUNBOOK.md` (emergency procedures, §18.3)
- **Escalation Protocol**: `docs/escalation-protocol.md` (Green/Review/Transfer)
- **Console Design**: `docs/design-reference.html` (interactive design system)
