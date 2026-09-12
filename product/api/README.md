# CONCORDE API — product/api

Module map:
- `http/` — HTTP parsing and request helpers (routes, failsafe). (T00X)
- `audio/` — decoding and VAD. (T00X)
- `audio/vad/` — VAD implementations (energy, smoothing, params). (T00X)
- `features/` — behavioral feature extractor (F-01 .. F-22). (T00X)
- `inference/` — ONNX/ORT model wrapper and runtime. (T00X)
- `analysis/` — analysis and explainability for POST /analyze. (T00X)
- `semantic/` — semantic model glue and fusion. (T00X)
- `storage/` — TigerData/local storage backends. (T00X)
- `routes/` — HTTP route handlers (`/health`, `/detect`, `/analyze`, ...).
- `metrics.rs`, `feed.rs`, `pipeline.rs` — instrumentation and orchestration helpers.

Building / running / testing
- From repo root:
  - cd product
  - cargo build
  - cargo run --bin concorde
  - cargo test

`POST /detect` request shapes (FR-003)
- **Canonical** — this is Altur's judge client (`hackmty26/scripts/check_endpoint.py`) and the only shape it actually sends. Match it exactly and everything else below is just defensive robustness:

  ```bash
  curl -X POST http://127.0.0.1:8080/detect \
    -H "Content-Type: application/json" \
    -d '{"call_id": "...", "audio_base64": "<base64 of the complete WAV file bytes>", "sample_rate": 8000, "channels": 2}'
  ```

- **Tolerated fallbacks** (accepted with no configuration; `parse.rs` tries them in this order after the canonical shape, and always ahead of giving up):
  - JSON with the base64 audio under a different key — any of `audio`, `wav`, `data`, `file`, `clip`, `content`:
    ```bash
    curl -X POST http://127.0.0.1:8080/detect \
      -H "Content-Type: application/json" \
      -d '{"audio": "<base64 WAV>"}'
    ```
  - Raw binary WAV body (`RIFF....WAVE` magic), no encoding:
    ```bash
    curl -X POST http://127.0.0.1:8080/detect --data-binary @call.wav
    ```
  - Raw base64 body, no JSON envelope:
    ```bash
    curl -X POST http://127.0.0.1:8080/detect --data-binary "$(base64 -w0 call.wav)"
    ```
  - `multipart/form-data` with a single file part:
    ```bash
    curl -X POST http://127.0.0.1:8080/detect -F "file=@call.wav"
    ```

  Base64 in any of the above (canonical or fallback) tolerates a leading `data:audio/wav;base64,` URI prefix, embedded whitespace, padded or unpadded input, and the URL-safe alphabet.

Fault semantics (ADR-006, NFR-005)
- `POST /detect` **always answers HTTP 200** with the two-key body `{"is_synthetic": <bool>, "confidence": <float in [0,1]>}` — this holds for a valid call, a malformed/empty/oversized body, a panic anywhere in the handler, and a handler that overruns its time budget. It never emits a 5xx in normal operation.
- Every request runs through `http::failsafe::run_failsafe`, which wraps the handler future in a panic catch (`catch_unwind` + `AssertUnwindSafe`, since the future itself need not be `UnwindSafe`) and a `CONCORDE_HANDLER_TIMEOUT_MS` timeout (default `20000` ms). The judge's own per-call timeout is 30 s including network, so 20 s leaves margin without ever leaving the judge's client hanging.
- On a panic, a timeout, or a reported failure (e.g. every `ParseError` variant from FR-003 — malformed JSON, invalid base64, an oversized body, etc.), the response is the **fallback verdict** `{"is_synthetic": false, "confidence": 0.50}` at HTTP 200. The incident is always logged internally as a structured `incident` event (`reason`, `request_id`, `call_id` when it was parsed, and the request's byte size) — a degradation is never silently unmarked (§7.2).
- `CONCORDE_STRICT=1` (dev only — never set in production) turns that same failure into HTTP 500 with the failure detail in the body instead of masking it behind the fallback verdict, so a developer sees it immediately instead of a plausible-looking 0.50.
- The router disables axum's own default 2 MB request-body limit (`DefaultBodyLimit::disable()` in `routes/mod.rs`): legitimate judge payloads run 6-12 MB (see `CONCORDE_MAX_BODY_BYTES` below), and even a genuinely oversized body must come back as the fallback verdict rather than a bare transport-level 413 — sizing is enforced by `http::parse` itself, after buffering, exactly as documented there.
- The response body's confidence is always clamped to `[0, 1]` and rounded to 4 decimals (`DetectResponse::new`), so this holds for the placeholder verdict today and for the real model's output once T014/T019/T020 land.

Environment variables (defaults and rationale)
- `CONCORDE_BIND` — bind address (default `127.0.0.1:8080`).
- `CONCORDE_MODEL_PATH` — path to ONNX model.
- `CONCORDE_FEATURE_CONTRACT` — feature contract id (default `fc-1`).
- `CONCORDE_THRESHOLD` — optional float override for decision threshold.
- `CONCORDE_MAX_BODY_BYTES` — default `16777216` (16 MB). Rationale: judge posts whole WAV as base64 inside JSON; measured medians ~6.2MB and max ~11.7MB; 16 MB provides margin.
- `CONCORDE_HANDLER_TIMEOUT_MS` — default `20000` (20s). Rationale: judge per-call timeout is 30s including network; handler budget should be smaller.
- `CONCORDE_STRICT` — if set, surface parsing errors in dev.
- `CONCORDE_SEMANTIC_ENABLED` — enable semantic layer.
- `CONCORDE_SEMANTIC_TIMEOUT_MS` — default `1500` ms.
- `CONCORDE_ANALYZE_SEMANTIC_TIMEOUT_MS` — default `8000` ms.
- `CONCORDE_WHISPER_MODEL_PATH`, `CONCORDE_WHISPER_THREADS`, `CONCORDE_ASR_MAX_CONCURRENT`, `CONCORDE_SEMANTIC_FUSION_PATH`, `TIGERDATA_URL` — other optional knobs.

