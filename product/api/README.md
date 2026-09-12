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

