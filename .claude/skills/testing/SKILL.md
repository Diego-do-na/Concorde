---
name: testing
description: "Use when writing or updating tests for concorde-api, the ml/ pipeline, or the console — including the FR verification suites and NFR benchmarks."
---

# Testing Skill — CONCORDE

Full detail: `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf` §5 (FR
Verification column), §6 (NFRs), §14 (Definition of Done). Every FR/NFR in
the spec names its own proof — this file indexes what "done" means per
component; don't invent a different bar.

## What must pass before a task is marked done

A task is not done because the code runs once locally — it's done when the
verification method named in its FR/NFR (or ADR) passes, reproducibly, from
a clean checkout. Cite the FR/NFR id in the PR/commit, not just "tests
pass".

## Backend (Rust, `api/`)

- **Golden-vector parity** (FR-005): Rust extractor output == Python
  reference extractor output, tolerance 1e-6, on ≥10 fixed calls. This is
  the test that proves `fc-1` isn't silently broken — treat any failure as
  a blocking defect, not a rounding nuisance.
- **VAD agreement** (FR-004): per-frame F1 ≥ 0.85 and turn-count ratio
  within ±20% vs the provided `turns/*.json`, on ≥30 calls.
- **Schema assertion** (FR-002): `/detect` response is exactly the two keys
  `is_synthetic`/`confidence`, no more, over a sample of responses.
- **Parametrised parsing suite** (FR-003): all 4 request shapes × padded/
  unpadded base64 × with/without data-URI prefix.
- **Fault injection** (FR-008, NFR-005): truncated WAV, zero bytes, mono
  file, 44.1kHz file, non-audio blob, oversized (>32MB) blob — every case
  must return HTTP 200 with the fallback verdict, never 5xx.
- **Startup contract check** (FR-006): a deliberately mismatched feature-
  contract version must fail to boot.
- **Soak + concurrency** (NFR-004, NFR-006): ≥8 concurrent requests without
  exceeding p99; ≤1.5GB resident memory over a 30-minute soak at 4 req/s.
- **Latency** (NFR-001): p95 ≤ 3.0s / p99 ≤ 5.0s server-side on a 180s
  clip, measured on the actual deployment, not a dev machine.

## ML (Python, `ml/`)

- **Anti-self-deception rule** (§10.2): a val AUC near 0.99 with 22
  features and ~250 training examples is treated as an alarm, not a
  success. Audit feature importances for leakage (duration correlated with
  label, VAD behaving differently by voice type) before trusting the
  number.
- **Speaker-grouped CV only within `train`** (ADR-012): `val` is never used
  for model/hyperparameter selection, only final measurement and
  calibration. Any test that touches `val` for tuning is itself a bug.
- **Metrics to report**: ROC-AUC (target ≥0.90), EER, Brier score (target
  ≤0.12), reliability curve/ECE, confusion matrix, error breakdown by call
  duration.
- **Robustness probe**: evaluate against ElevenLabs-generated clips (an
  engine absent from the dataset) as the most honest available proxy for
  the hidden set.

## Frontend (`console/`)

- All three views (live/detail/exec) reachable in <20s from landing.
- Every component's loading/empty/error/degraded states exercised, not
  just the happy path — a blank screen on backend hiccup is a failing
  test, not a UI nitpick (NFR-012).

## Reproducibility (NFR-009)

Every reported metric must be reproducible by a third party from the repo
alone: git SHA, model version, training seed, dataset manifest hash, and
feature-contract version are all recorded with the artifact. A test result
that can't be traced back to these is not evidence.
