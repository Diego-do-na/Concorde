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
    (offline evaluation of the booster recorded in REPORT.md; the live figure
    below is 0.864 — the difference is consistent with the T059 re-export
    chain, `concorde-b-1` → `concorde-b-2`, whose exact contribution was not
    investigated further before the freeze)
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
- Load / soak: `docs/load-report.md` — 8-way concurrency run against the final deployment (see the report for p95/p99 under load)

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
- Local semantic layer, F-23 (T042–T045): `docs/semantic-layer.md` — probe-turn audio detector + whisper.cpp + answer-type rules, fully on-server; A/B and fusion parameters in `product/ml/semantic/AB_REPORT.md`. Disabled in the judged deployment (see Known limitations).
- Red-team / robustness (T050, T051): `docs/robustness.md` and `product/eval/redteam/REPORT.md` — ElevenLabs synthetic callers (unseen engine) and in-house human recordings.
- TigerData schema, event writer and `/history` (T047–T049): `docs/tigerdata.md` — hypertable, continuous aggregate, non-blocking writer; active in the judged deployment (`/health` reports `tigerdata: ok` once traffic flows).
- Acoustic sub-signal, experimental (T053): `product/ml/acoustic/` — display-only, never part of the `/detect` verdict.
- Escalation protocol (T052): `docs/escalation-protocol.md` — operational states and handoff rules.

## Known limitations
- See `docs/pre-judging-checklist.md` for the judge-focused runbook and limitations recorded during the final checklist run (T055).
- The semantic layer is **disabled** in the judged deployment (`CONCORDE_SEMANTIC_ENABLED=false`): the fitted fusion weight (`w = -0.8`, base model) never received the bootstrap stability check that was set as a condition, so the served verdict is behavioral-only and deterministic (NFR-010). `/analyze` reports `semantic: null`, `semantic_available: false` — a degraded signal, never a fabricated zero.
- The whisper.cpp source commit used for the offline transcripts is not recorded; the ggml model files are pinned by sha256 and the serving side is pinned through `whisper-rs` in `Cargo.lock`. A rebuild of the offline CLI on another machine could produce slightly different ground truth (NFR-009 risk, offline only).
- Red-team clips were assembled from a 10-turn script without the agent's deliberate silences, so the two dominant features (`silence_break_delay_*`) are degenerate on them and the synthetic clips land close to the threshold; see `docs/robustness.md`.

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
