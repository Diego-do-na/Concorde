# CONCORDE — Judge-facing README

This repository implements CONCORDE: a behavioral/conversational detector of
synthetic voices for Altur (HackMTY 2026). Read this file first.

## Thesis (what we built)
- Primary signal: conversational dynamics across both audio channels (caller
  and agent). We do *not* rely on a deep acoustic net because acoustic
  classifiers overfit small, TTS-specific artifacts and fail to generalize to
  new engines. Low-dimensional, turn-shaped behavioral features (F-01…F-22,
  frozen contract `fc-1`) capture structural recovery and turn-taking patterns
  that generalise to unseen TTS engines.
- Practical consequence: the server runs its own VAD at inference time (the
  repository's `turns/<id>.json` files exist only in the practice dataset).

## Architecture (high level)
- Rust + Axum HTTP service (`product/api`) handles parsing, WAV decode, VAD,
  fc-1 feature extraction, ONNX inference (ORT), Platt calibration, and the
  graded `/detect` response.
- Offline ML pipeline (`product/ml`) prepares training tables from the same VAD
  the server uses, trains LightGBM, calibrates, and exports `artifacts/model.onnx`.
- Console (`product/console`) is a React dashboard that calls `/analyze` for
  explainability; `/detect` remains the only scored artifact.

Architecture flow (text):

request bytes -> http parse -> decode WAV -> VAD (own) -> features (fc-1) -> ONNX inference + Platt -> verdict

## API contract (graded)
- Canonical request JSON (what Altur's judge client sends exactly):

```json
{"call_id": "...", "audio_base64": "<base64 of the complete WAV file bytes>", "sample_rate": 8000, "channels": 2}
```

- Fallback shapes tolerated (robust parser): JSON with keys `audio|wav|data|file|clip|content`, raw base64 body, raw WAV bytes, or multipart file. The parser accepts a `data:audio/wav;base64,` prefix and variably padded base64.

- Response: Exactly two keys, HTTP 200 always:

```json
{"is_synthetic": <bool>, "confidence": <float in [0,1]>}
```

- On any parse error, timeout, or internal failure the server returns the fallback
  verdict `{"is_synthetic": false, "confidence": 0.50}` (HTTP 200) and logs the
  incident. `CONCORDE_STRICT=1` is a dev switch that surfaces errors instead.

- Judge URL: https://getconcorde.tech/detect
  - Emergency fallback only: http://216.238.90.138/detect
  - See `product/deploy/DOMAIN.md` and `product/deploy/DEPLOY_LOG.md`.

## Feature contract (fc-1)
fc-1 is frozen. The exported ONNX model and the Rust extractor expect the exact
name/order below (23 floats; F-21 is stored as two values: mean & CV).

| ID | Feature |
|---:|---|
| F-01 | `resp_latency_mean` |
| F-02 | `resp_latency_median` |
| F-03 | `resp_latency_std` |
| F-04 | `resp_latency_cv` |
| F-05 | `latency_monotony_index` |
| F-06 | `resp_latency_min` |
| F-07 | `overlap_count` |
| F-08 | `overlap_rate_per_min` |
| F-09 | `overlap_total_dur` |
| F-10 | `caller_bargein_count` |
| F-11 | `recovery_delay_mean` |
| F-12 | `recovery_delay_cv` |
| F-13 | `recovery_abort_ratio` |
| F-14 | `caller_turn_dur_mean` |
| F-15 | `caller_turn_dur_std` |
| F-16 | `caller_turn_dur_cv` |
| F-17 | `short_turn_ratio` |
| F-18 | `fragmentation_rate` |
| F-19 | `caller_speech_ratio` |
| F-20 | `speech_balance` |
| F-21a | `silence_break_delay_mean` |
| F-21b | `silence_break_delay_cv` |
| F-22 | `turn_count_caller` |

Definitions, degenerate rules, and interpretation notes are in
`product/ml/README.md` (the extractor defaults empty-set statistics to `0.0` and
defines exact thresholding for F-05/F-12).

Operationalisation note (F-05 / F-12)
- F-05 (`latency_monotony_index`): fraction of response latencies within ±0.15s
  of the median (consistency signal). Higher → more machine-like consistency.
- F-12 (`recovery_delay_cv`): CV of recovery delays after overlap events. Low CV
  indicates consistent, machine-like recovery; both features contributed to the
  model's top explanatory factors in training.

## Measured results (shipped model)
All numbers are copied from the canonical sources in this repo.

- Training / validation report (`product/ml/train/REPORT.md`): shipped
  LightGBM `model_lgbm.txt` (git_sha `5d539a8f32752f2d6892b01742a89e4f108f54b8`, seed `20260912`), scored on `val` (n=71):
  - Balanced accuracy (val, shipped threshold): 0.8466
  - ROC-AUC (val): 0.9436
  - Brier (after calibration): 0.0936
  - EER (val): 0.1129
  - Confusion matrix (val): TP=30 FN=4 / FP=7 TN=30
  - Error rate by duration bands: [60–120)s: 0.4444 (n=9), [120–180)s: 0.1176 (n=51), [180–280)s: 0.0909 (n=11)

- Deployment / real-checks (`product/deploy/DEPLOY_LOG.md`, T025 & T024):
  - Early public run (pre-fix): balanced_accuracy 0.500 (calls:71, errors:0)
  - Final public deploy (concorde-b-2, post export fix):
    - balanced_accuracy: 0.864
    - auc: 0.944
    - tpr_synthetic: 0.971
    - tnr_human: 0.757
    - accuracy: 0.859
    - brier: 0.103
    - mean latency (client): ~0.475 s; max latency: 0.589 s

## Check runs & verification artifacts
- VAD agreement (FR-004): `product/ml/validation/vad_agreement/REPORT.md`
  - Calls evaluated: 353
  - Per-channel median F1: caller 0.873, agent 0.861 — GATE: PASS
- Golden-vector parity (FR-005): `product/ml/validation/parity/REPORT.md` — PASS
  - Max abs error per feature all ≪ 1e-6 (machine-epsilon noise only)
- Latency percentiles (real deployment): `docs/latency-report.md`
  - server-side p50=475 ms, p95=568 ms, p99=585 ms
- Load / soak: `docs/load-report.md` — NOT YET RUN (T040 blocked on real model; TBD)

## Payload size & limits
- Default server limit: `CONCORDE_MAX_BODY_BYTES = 16_777_216` (16 MB) (`product/api/README.md`).
  Rationale: judge posts whole WAV as base64 inside JSON; measured medians ~6.2 MB and max ~11.7 MB — 16 MB gives margin.

## How to reproduce numbers
1. Set up dataset: ensure `CONCORDE_DATASET_DIR` points to the practice dataset (manifest + audio + turns). Manifest sha256 recorded: `4fa5ac3f25f2bc1fbff9a06a89f621fcca30db5f1443bbd164368193f1d7c544`.
2. Offline: `cd product/ml && python -m train.build_dataset && python -m train.train && python -m train.calibrate && python -m export.export_onnx`
   - The shipped training run recorded: git_sha `5d539a8f32752f2d6892b01742a89e4f108f54b8`, seed `20260912`.
3. Deploy: follow `product/deploy/DEPLOY_LOG.md` and `product/deploy/README.md` to place `artifacts/model.onnx` on the server and start the service.
4. Verify via Altur's client:
   - `python $CONCORDE_DATASET_DIR/scripts/check_endpoint.py --url https://getconcorde.tech/detect --split val --n 0 --out val_check_public.json`
5. To reproduce every number: use the same git SHA, seed, and manifest SHA256 above (NFR-009).

## Dataset & privacy compliance
- No dataset files or WAVs are committed to this repo (NFR-011). `audio/`, `turns/`, and any `ml/data/` outputs are gitignored.
- No speaker identification functionality is implemented or stored. We do not attempt to infer or store speaker identity.

## Deviation from ADR-012 (speaker-grouped CV)
- The dataset's `manifest.csv` contains no speaker identifier column and the dataset terms forbid attempting to identify speakers. Therefore we did **not** implement speaker-grouped CV inside `train`. Instead:
  - Training used stratified K-fold by `anon_id` inside `train` (documented in `product/ml/README.md`).
  - `val` remains the only speaker-disjoint measurement and was used only for final metrics and calibration.

## Improvement docs (shipped)
Only improvement tasks marked `status: done` in `orchestration/tasks.yaml` are included here.
- ASR server benchmark (T057): `docs/asr-server-benchmark.md` — server-side whisper.cpp measurements and decision rule (model choice, vCPU).
- TigerData schema & SQL (T047): `docs/tigerdata.md` — schema, hypertable, continuous-aggregate, and how to load events.
- Escalation protocol (T052): `docs/escalation-protocol.md` — operational states and handoff rules.

Not shipped (improvement work pending)
- `docs/semantic-layer.md` (local semantic fusion / F-23) — pending: T042/T043/T044/T045
- `docs/robustness.md` (red-team / ElevenLabs evaluation) — pending
- Load/soak results in `docs/load-report.md` — pending

## Known limitations
- See `docs/pre-judging-checklist.md` for the judge-focused runbook and limitations recorded during the final checklist run (T055).

## Verification & sign-off (pre-freeze)
- Verification checklist:
  - Every number in this README should match its source file. (Planned helper: `product/scripts/check_readme_numbers.py`.)
  - All links must resolve from a freshly cloned worktree.
  - Three-person sign-off required before the freeze deadline.

Signatures:
- Paul (owner): __________________  — time: __________________
- Diego (model lead): ______________  — time: __________________
- Néstor (console/ops): ____________  — time: __________________

---
This README is authoritative for judges. For developer-level details see the per-component READMEs under `product/`.

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
| **Probe-turn audio detector (F-23) degrades to null on ~43% of calls** | Measured 2026-09-12 (T043, full 353-call dataset): the audio-only detector (log-mel + subsequence-DTW template bank, `product/artifacts/probe_templates.json`) confidently classifies present/absent for only ~57% of calls; the rest (`ambiguous_rate = 0.4336`) correctly degrade F-23 to unavailable (AGENTS.md rule 4/ADR-008 — no unmarked degradation) rather than guess. `confident_false_positives = 0` over 76 probe-absent calls is the real safety gate and holds without exception; a naive single-threshold cut was deliberately rejected because it would have made this same bank's false-positive rate ~96%. **Consequence for the pitch**: F-23 (Gemini/whisper semantic layer, SHOULD-priority) contributes real signal on fewer than half of all calls; the behavioral core (fc-1, MUST-priority) is what the verdict depends on for the rest. See `product/ml/README.md`'s probe-detector section for the full validation table and `product/ml/semantic/probe_detector.py` for the degrade-to-null logic. |

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

---
⚠️ **KNOWN ISSUE — PENDING AUDIT**: This file currently contains two
concatenated README drafts with contradictory numbers (accuracy 0.8466
vs 0.864; latency mean/max vs p95/p99) and possibly stale claims about
TigerData/semantic layer status. Needs reconciliation against source
reports before judging. — flagged 2026-09-13
