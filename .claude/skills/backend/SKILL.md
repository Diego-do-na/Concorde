---
name: backend
description: "Use when building or editing the concorde-api Rust service (api/) — request parsing, WAV decode, VAD, feature extraction, ONNX inference, or the HTTP routes."
---

# Backend Skill — concorde-api (Rust)

Full detail: `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf` §7 (architecture),
§8 (API contract), §9 (feature catalog), §13.2 (task scopes). This file is
the fast-reference summary.

## Stack

Rust + Axum + Tokio. `hound` for WAV decode, `ort` for ONNX Runtime inference,
`serde` for JSON, `tracing` for structured logs. No Python at runtime —
Python only exists in the offline `ml/` pipeline (ADR-004).

## Non-negotiable contract rules

- `/detect` returns **exactly** `{"is_synthetic": bool, "confidence": float}`,
  nothing more, and **always HTTP 200** (ADR-006, ADR-013, NFR-005). Any
  panic must be caught at the handler boundary and converted to the
  fallback verdict `{"is_synthetic": false, "confidence": 0.50}` — a panic
  must never take down the worker.
- Accept all four request shapes from FR-003 (raw base64 body; JSON with
  any of `audio`/`audio_base64`/`wav`/`data`/`file`/`clip`/`content`;
  multipart; raw binary WAV), tolerating data-URI prefixes and unpadded
  base64.
- `/analyze` is the only place explanatory/rich data goes (ADR-013). Never
  add fields to `/detect`'s response, even temporarily for debugging.
- Feature extraction must produce the exact `fc-1` vector, F-01…F-22 in
  order (§9), matching the Python reference extractor byte-for-byte within
  1e-6 (golden-vector parity test, FR-005). Never reorder or add features
  without bumping the contract version and re-exporting the ONNX artifact.
- The VAD (`api/src/audio/vad.rs`) must run in the inference path — there is
  no `turns.json` at inference time — and be validated against the
  provided practice `turns/*.json` before the model trains on its output
  (FR-004, ADR-003).
- Model artifact is a single versioned ONNX file loaded once at process
  start (`CONCORDE_MODEL_PATH`); a feature-contract version mismatch aborts
  startup (FR-006).
- The semantic layer (Gemini probe) runs under a **hard timeout**
  (`CONCORDE_SEMANTIC_TIMEOUT_MS`, default 1500ms) and is cancellable —
  never on the critical path (ADR-008). Same for Tiger Data writes:
  storage is observability, not a dependency — `/detect` must answer
  normally even if it's down.

## Latency budget (NFR-001)

p95 ≤ 3.0s / p99 ≤ 5.0s end-to-end, excluding network transfer. Internal
per-stage budget: decode + VAD ≤ 800ms, feature extraction ≤ 400ms,
inference ≤ 50ms, semantic layer hard-capped at 1.5s and skipped on expiry.

## Logging

Structured JSON per request (NFR-008): request id, byte size, detected
duration, turn count, per-stage timings, sub-scores, final verdict, model
version. **Never log audio content or transcript text.**

## Testing expectations

- Golden-vector parity test (Rust extractor vs Python reference) on fixed
  calls, tolerance 1e-6.
- Fault-injection suite: truncated WAV, zero bytes, mono file, 44.1kHz
  file, non-audio blob, oversized blob — all must return 200 with the
  fallback verdict, never 5xx.
- Soak/concurrency test: ≥8 concurrent requests without exceeding p99
  budget; resident memory ≤1.5GB under sustained load.
- `CONCORDE_STRICT=1` in dev/test to propagate errors instead of masking
  them behind the fallback verdict.
