# CONCORDE API — product/api

Module map:
- `http/` — HTTP parsing and request helpers (routes, failsafe). (T00X)
- `audio/` — decoding and VAD. (T00X)
  - `audio/decode.rs` — WAV decoder: accepts stereo, 8000 Hz, 16-bit PCM. Exposes `decode_wav(bytes) -> Result<Stereo8k, AudioError)` where `Stereo8k` contains `ch0`, `ch1` as `Vec<f32>` in [-1,1] and `duration_s`. `AudioError` variants: `NotWav`, `Mono`, `WrongRate(u32)`, `WrongBits`, `Truncated`, `Other`. Decoder tolerates truncated data only when at least 1 second of samples remain; it guards against absurd headers and never allocates more than the declared sample count.
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
#
Running the VAD CLI (`vad-dump`)
- Build the binary: `cargo build --bin vad-dump` (from `product/api`).
- Usage: `cargo run --bin vad-dump -- <in.wav> [--params k=v ...]`
- The tool prints a single JSON object to stdout with the exact dataset shape:
  `{"turns":[{"channel":0,"start":0.00,"end":1.23}, ...]}`. Times are seconds rounded to 2 decimals.

VadParams fields (T011)
- `frame_ms` (u32): analysis frame length in milliseconds (default 20).
- `hop_ms` (u32): hop/stride between successive frame starts in milliseconds (default 10).
- `noise_floor_percentile` (f32): percentile of per-frame energy used as noise floor (default 0.10).
- `threshold_db_above_floor` (f32): dB offset above the estimated floor to set the activity threshold (default 6.0).
- `on_frames` (u32): consecutive above-threshold frames required to declare speech onset (default 3).
- `off_frames` (u32): consecutive below-threshold frames required to declare speech offset (default 15).
- `min_speech_ms` (u32): minimum duration in ms for a segment to be kept as speech (default 200).
- `min_gap_ms` (u32): gaps shorter than this (ms) between segments are merged (default 250).

Note: T012 (the agreement sweep) may update the Stage-B defaults; `--params` allows experimenting with alternate values.

Behavioral feature extraction (`features/`, T014)
- Contract: `product/artifacts/feature_contract_fc-1.json` is the single source of truth for the fc-1 feature vector's name/order (§9); `features::FC1_NAMES` and `features::FC1_VERSION` mirror it, and a unit test asserts they match the file byte-for-byte.
- `features::extract(caller, agent, duration_s) -> [f64; 23]` mirrors `product/ml/features/extract.py::extract` line by line — same half-open `[start, end)` interval convention, the same `±0.15s / 0.4s / 1.0s / 0.5s / 2.0s` thresholds, and the same population-std (`ddof=0`) / `cv = std/mean, 0.0 when mean==0.0` degenerate-input rules. `caller`/`agent` are channel-split turn lists — this system's own VAD output at serving time (ADR-003), or `turns/<id>.json` in the offline pipeline — so neither this function nor its Python counterpart ever sees a channel column. `extract_named()` pairs the same 23 values with `FC1_NAMES` for `/analyze` (ADR-013 — `/detect` never carries this).
- Split across `latency.rs` (F-01…F-06), `overlap.rs` (F-07…F-13), `morphology.rs` (F-14…F-18), and `balance.rs` (F-19…F-22, where F-21 is the `silence_break_delay_mean`/`_cv` pair) — same grouping as the section comments in `extract.py`.
- **Any change to this contract creates `fc-2`, requires re-running T015's golden-vector parity test (Rust vs. Python, tolerance 1e-6), and requires a new ONNX export.** Never reorder or add to `FC1_NAMES` casually — a silently reordered vector produces plausible-looking but wrong verdicts with no error raised.
#
Model sidecar schema (T021)
- Models are shipped alongside a JSON "meta" file named `<model>.onnx.meta.json`. The service expects the following fields (exporter T021 must conform):
  - `model_version` (string)
  - `feature_contract` (string)
  - `calibration` (object) with `{ "type": "platt", "a": <float>, "b": <float> }`
  - `threshold` (float)
  - `git_sha` (string|null)
  - `seed` (int|null)
  - `manifest_sha256` (string|null)
  - `feature_names` (array of 23 strings)
  - `train_feature_means` (array of 23 floats)
  - `train_feature_stds` (array of 23 floats)
  - `feature_importance` (array of 23 floats)
  - `direction_sign` (array of 23 ints)

FR-006 (startup abort)
- On startup the API loads the configured ONNX model and its `<model>.meta.json` sidecar. If `meta.feature_contract != CONCORDE_FEATURE_CONTRACT` or `meta.feature_names != features::FC1_NAMES` the process aborts with a non-zero exit and a clear error log. This prevents silent, dangerous mismatches between the extractor's vector order and the exported model's expectation.

Golden-vector parity (FR-005, T015)
- `cargo test --test parity` (from `product/api`) runs `tests/parity.rs`, which loads every fixture in `product/ml/validation/parity/golden/*.json` (10 real calls + 3 hand-built degenerate cases), re-runs `features::extract` on each fixture's `caller`/`agent`/`duration_s`, and asserts the result matches the fixture's `fc1` (computed by the Python reference extractor) element-wise within `1e-6`, printing the first mismatching feature name on failure.
- Fixtures are regenerated on the Python side by `python product/ml/validation/parity/make_golden.py` (see `product/ml/README.md`'s parity section) — this Rust test only consumes them, it never regenerates them.
- Any mismatch is fixed by aligning the Rust extractor to Python, unless Python itself is found to violate §9 — in that case stop and get Diego's sign-off (AGENTS.md) rather than "fixing" the Python side unilaterally.

