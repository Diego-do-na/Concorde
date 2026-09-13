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
- `CONCORDE_SEMANTIC_ENABLED` — enable semantic layer (default `false`).
- `CONCORDE_SEMANTIC_TIMEOUT_MS` — default `1500` ms (ASR timeout budget for `/detect`).
- `CONCORDE_ANALYZE_SEMANTIC_TIMEOUT_MS` — default `8000` ms (ASR timeout budget for `/analyze`).
- `CONCORDE_WHISPER_MODEL_PATH` — path to ggml whisper model (e.g. `/opt/concorde/artifacts/ggml-tiny.bin`).
- `CONCORDE_WHISPER_THREADS` — default `4` (threads for whisper.cpp execution).
- `CONCORDE_ASR_MAX_CONCURRENT` — default `2` (max concurrent ASR calls via Tokio Semaphore; skips in <1ms if busy).
- `CONCORDE_SEMANTIC_FUSION_PATH` — path to fusion JSON artifact (default `artifacts/semantic_fusion.json`).
- `CONCORDE_PROBE_TEMPLATES_PATH` — path to probe template bank (default `artifacts/probe_templates.json`).
- `TIGERDATA_URL` — other optional knobs.

Semantic Layer & Local Fusion (T045, ADR-008, NFR-001, NFR-010)
- **Architecture**: Inline DTW probe detector (`probe.rs`) -> ASR on the caller's answer turn (`asr.rs` via `whisper-rs`) -> rule-based `answer_type` & `invention_score` (`rules.rs`) -> logit-space fusion (`fusion.rs`).
- **Fusion Rule**:
  `logit(p_final) = logit(p_behavioral) + w * (invention_score - 0.5)`
  Clamped so `|p_final - p_behavioral| <= max_delta_p` (shipped parameters: `w = -0.8`, `max_delta_p = 0.15`).
- **Explicit Degradation & Fallback**: When semantic is unavailable for any reason (disabled, no probe turn, no answer turn, semaphore busy, or ASR timeout), `p_final = p_behavioral` exactly (deterministic behavioral-only verdict, NFR-010) with `semantic_available = false` and `reason` in the structured log line.
- **Privacy Contract**: 100% local execution using embedded `whisper.cpp` (`whisper-rs`). Zero audio or transcript text ever leaves the process or server, and transcript text is never logged or stored.
- **Build Requirements**:
  - `cmake` and `libclang` installed on the build host (for `whisper-rs-sys`).
  - Model setup script: `./product/deploy/get_whisper_model.sh [target_dir]` downloads `ggml-tiny.bin` into `/opt/concorde/artifacts/` (T057 approved, sha256 pinned: `be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21`).
- **Parity Verification Command**:
  - `cargo test --test semantic_parity` (from `product/api`) verifies 100% parity between Rust `rules.rs` and Python `rules.py` rules/scores, probe detector DTW, and `semantic-dump` CLI binary output.
#
The `/detect` pipeline (T022)
- `pipeline::analyze_bytes(state, bytes) -> Result<Analysis>` is the one place decode, VAD, features, and inference are wired together; `routes::detect` calls it and reads only `Analysis::verdict`, still inside `http::failsafe::run_failsafe` so any `Err` here (undecodable WAV, no model loaded, an ONNX runtime error) becomes the ADR-006 fallback verdict at HTTP 200, never this function's own concern.

  ```text
  request bytes
      │
      ▼
  http::parse::extract_audio        (FR-003: 5 tolerated shapes -> WAV bytes)
      │
      ▼
  pipeline::analyze_bytes
      ├─ decode   -- hound WAV header peek (sample_rate, duration_s)
      ├─ vad      -- audio::vad::detect_turns (own VAD, ADR-003/FR-004; ch0=caller, ch1=agent)
      ├─ features -- features::extract (fc-1, F-01..F-22, frozen order)
      └─ inference-- inference::model::Model::score (ONNX + Platt calibration, §8.4)
      │
      ▼
  Analysis { turns, events, features, verdict, timings_ms, model_version }
      │
      ▼
  routes::detect -- DetectResponse::new(verdict.is_synthetic, verdict.confidence)
  ```

  Degenerate calls (no caller turns, a single active channel, near-silent audio) are **not** routed to the fallback: the fc-1 extractor defines a value for every feature on any input (see `features::mod`'s `fully_empty_call_is_all_zero` test), so they still get scored by the real model. Only bytes that don't parse as WAV at all, or a missing/broken model, are pipeline failures.

  Per-stage timing budget (NFR-001, internal — not enforced by the judge, which allows 30s/call including network):

  | stage      | budget   |
  |------------|----------|
  | decode     | 800 ms   |
  | vad        | 400 ms   |
  | features   | ~free    |
  | inference  | 50 ms    |
  | **total**  | **1.3 s**|

  Exactly one structured `event = "detect"` log line is emitted per successful request (`request_id`, `call_id`, `bytes`, `duration_s`, turn counts, the timings above, `p_synthetic`, the verdict, `model_version`) — never audio or transcripts (NFR-008). A failed request instead gets `run_failsafe`'s own `event = "incident"` line.

  Canonical smoke test — Altur's own scorer, run against a live server (`cargo run --bin concorde`, with `CONCORDE_MODEL_PATH` pointed at a real exported model):

  ```bash
  python $CONCORDE_DATASET_DIR/scripts/check_endpoint.py \
    --url http://127.0.0.1:8080/detect --split val --n 0 \
    --out product/ml/data/val_check_local.json
  ```

  Expect `answered: 71`, `errors: 0`, `balanced_accuracy` clearly above 0.5 and within ±0.01 of `product/ml/train/REPORT.md`, `auc`/`brier` populated, and `max_latency_s` well under 30. `cargo test` (including `tests/detect_e2e.rs`, which exercises the same router against the committed dummy fc-1 fixture model) is the fast, dataset-free proxy for this — every later task's own verification is gated on this one passing.

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

`POST /analyze` — internal superset payload (§8.2, FR-009, ADR-013, T028)
- Not the scored contract — `/detect` stays exactly `{"is_synthetic": <bool>, "confidence": <float>}`, nothing more, always HTTP 200. `/analyze` exists only for `console/`'s dashboard: it shares the same request parser and the same `pipeline::analyze_bytes` call as `/detect`, then builds the rich payload below in `analysis::build` (`src/analysis/mod.rs`). A failure here (bad input, no model, a timeline re-score error, a panic, a timeout) returns HTTP 200 with `{"error": "<reason>"}` instead of `/detect`'s fallback verdict — this route isn't graded, so there's no fallback-verdict contract to preserve, only "never 5xx, never hang".
- Request shapes: identical to `/detect` (`http::parse::extract_audio`, FR-003 — canonical `audio_base64` JSON, the other JSON key aliases, raw binary WAV, raw base64, multipart).
- Response body (`src/analysis/analyze.schema.json` is the JSON Schema `tests/analyze_e2e.rs` validates against):

  ```bash
  curl -X POST http://127.0.0.1:8080/analyze --data-binary @call.wav
  ```

  ```json
  {
    "verdict":      {"is_synthetic": true, "confidence": 0.87, "threshold": 0.5},
    "signals":      {"behavioral": 0.91, "semantic": null, "acoustic": null},
    "degraded":     {"semantic_available": false, "acoustic_available": false},
    "timeline":     [{"t": 10.6, "confidence": 0.63}, {"t": 21.2, "confidence": 0.71}, "...14 more...", {"t": 180.0, "confidence": 0.87}],
    "turns":        {"caller": [[10.5, 20.16], "..."], "agent": [[0.3, 7.66], "..."]},
    "events":       [{"type": "overlap", "t": 118.34, "duration": 0.42}, "..."],
    "features":     {"resp_latency_cv": 0.11, "overlap_count": 3.0, "...": "...(23 total, fc-1 names)"},
    "top_factors":  [{"feature": "resp_latency_cv", "value": 0.11, "direction": "synthetic", "weight": 0.41}, "...(up to 5, weights sum to 1)"],
    "rationale":    "Response latency stayed within CV 0.11 across 12 agent turns (resp_latency_cv, synthetic signal, weight 41%). ...",
    "waveform":     {"caller": [0.127, 0.456, "...(~3600 for 180s)", 0.234], "agent": [0.0, 0.0, "...", 0.089], "bucket_ms": 50},
    "timings_ms":   {"decode": 61.0, "vad": 240.0, "features": 88.0, "semantic": null, "inference": 7.0, "total": 396.0},
    "meta":         {"model_version": "concorde-dummy-0", "git_sha": "unknown", "feature_contract": "fc-1", "duration_s": 180.0}
  }
  ```

  On failure:

  ```json
  { "error": "WAV decode failed: not a valid WAV file" }
  ```

- `signals.behavioral` is the model's raw `p_synthetic` (pre-threshold probability); `verdict.confidence` is the calibrated, thresholded value `/detect` also reports.

- Notes on optional signals:
- `- `signals.semantic` and its `degraded.semantic_available` flag remain gated behind the semantic pipeline tasks (T038/T042/T045) and are `null`/`false` until that work ships.
- `- The acoustic sub-signal (experimental) is intentionally conservative: when available it populates `signals.acoustic` in the `/analyze` response (rich payload) and sets `degraded.acoustic_available = true` for `/analyze` only. `/detect` continues to return behavioral-only verdicts unless a reviewed fusion policy is explicitly enabled in production — see `product/ml/acoustic/` and `product/ml/acoustic/REPORT.md`. 
- `timeline` always has exactly 17 points: 16 evenly spaced truncation times over `(0, duration_s)` (features re-extracted and the model re-scored on the turns as they'd have looked had the call ended at each `t` — cheap tabular inference, no re-decode/re-VAD) plus a 17th point at `t = duration_s` whose `confidence` is `verdict.confidence` itself, not a redundant re-score.
- `top_factors` ranks all 23 fc-1 features by `|feature_importance_i × z_i|` (`z_i` = the call's feature value normalized against the model sidecar's `train_feature_means`/`train_feature_stds`), keeps the top 5, and renormalizes `weight` so they always sum to 1 (including the all-zero-importance edge case, which splits weight evenly rather than dividing by zero). `direction` is `"synthetic"` when `direction_sign_i × z_i ≥ 0`, else `"human"`.
- `rationale` is a deterministic template sentence per top-3 factor (`src/analysis/factors.rs` has one phrasing per fc-1 feature name), always naming its own raw feature identifier — e.g. `(resp_latency_cv, synthetic signal, weight 41%)` — so it's always traceable back to `top_factors`.
- `waveform` is a console-only enrichment (ADR-013) for dual-channel visualization. It contains the peak absolute amplitude in each 50 ms time bucket for both channels (`caller` and `agent`). Each value is in [0, 1] (normalized sample amplitude) rounded to 3 decimals. For a 180 s call, this is ~3600 floats per channel (~7200 total). A 2 s call produces 40 buckets; silence produces all zeros; a −6 dBFS tone has peaks ≈0.5.

Additional HTTP routes
- `GET /metrics` — returns a JSON object with process-wide counters and per-route latency percentiles (p50/p95/p99). If the request `Accept` header contains `text/plain` the route returns a tiny Prometheus-like exposition instead. Example JSON:

```json
{
  "requests": 123,
  "fallbacks": 2,
  "routes": {
    "detect": { "count": 120, "p50_ms": 10.5, "p95_ms": 40.2, "p99_ms": 90.1 }
  }
}
```

- `GET /version` — returns build information:

```json
{
  "git_sha": "abcdef",
  "build_time": "2026-09-12T00:00:00Z",
  "rustc": "1.70.0",
  "model_version": "v0.1.0",
  "feature_contract": "fc-1"
}
```

Live feed (T027)
- `GET /feed/recent?limit=N` — returns the most-recent compact feed events as a JSON array, newest-first. Each event has the schema:

```json
{
  "id": "string",
  "ts": 1690000000.123,           // epoch seconds (float)
  "duration_s": 12.34,
  "is_synthetic": false,
  "confidence": 0.75,
  "latency_ms": 123.4,
  "signals": { "behavioral": true, "semantic": null, "acoustic": null },
  "model_version": "v0.1.0"
}
```

- `GET /feed/analysis/:id` — returns the full `/analyze` payload previously retained for that id, or 404 if not retained (only the last 50 analyses are kept).
- `GET /ws` — websocket endpoint that streams new feed events as JSON. Basic rate limiting limits concurrent WS connections.

Tiger Data event writer (`storage/`, T048)
- **Storage is observability, not a dependency (§7.2).** `/detect` never `.await`s Tiger Data and never fails, slows down, or changes its verdict because storage is unreachable, slow, or unconfigured — the pipeline (`pipeline::analyze_bytes`) hands each finished `Analysis` to `storage::record` as the very last step, which is a synchronous, non-blocking call.
- Disabled cleanly when `TIGERDATA_URL` is unset: `storage::record` is then a no-op (it never even builds a channel), and `/health`'s `deps.tigerdata` reports `"disabled"`.
- When configured, `EventWriter::spawn` (booted lazily, once, on the first pipeline run that has a URL — mirrors the `metrics::GLOBAL_METRICS` singleton) creates a bounded `tokio::mpsc` channel (capacity 1024) and a background task that owns a lazily-connected `sqlx::PgPool` (connect timeout 3 s, `sslmode=require` unless the URL already specifies one). `EventWriter::record` is a plain `try_send`: on backpressure (channel full, e.g. Tiger Data is slow) it drops the event and increments an internal counter instead of blocking.
- The background task batches incoming events and inserts them into `detection_events` every 500 ms or every 50 rows, whichever comes first. `detection_events` is created with `CREATE TABLE IF NOT EXISTS` on first successful connect — no separate migration step needed to exercise this task.
- Any database error (initial connect or a later insert) marks `deps["tigerdata"] = degraded`, logs an `incident` event at most once per minute (never spammed), drops the pool so the next flush reconnects, and keeps running — it is never a reason to stop serving `/detect`.
- No audio, transcript text, or speaker-identifying data is ever written (NFR-011, §13.4) — a row is just `{request_id?, call_id?, is_synthetic, confidence, p_synthetic, duration_s, model_version, created_at}`. `request_id`/`call_id` are `Option` because `pipeline::analyze_bytes` (storage's only caller) doesn't itself see either id — both are assigned one layer up in `routes::detect`/`routes::analyze` — left as a hook for a future task to thread through rather than a schema change.
- Tests: `cargo test --lib storage::` covers the unreachable-URL (never blocks, ends up `degraded`) and full-channel (drops + counts, never awaits) cases without a real database. `cargo test --lib storage::tests::ten_events_land_in_the_table -- --ignored` exercises the real insert path against a local/CI Postgres — set `TIGERDATA_TEST_URL` first.

