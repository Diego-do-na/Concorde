# CONCORDE load report — T040 (NFR-004, NFR-006)

**Status: NOT YET RUN.** This is the skeleton and methodology; the actual
concurrency and soak runs are blocked on T059 (deploy the real trained
model — see below) so that this report measures real inference latency and
memory, not the all-fallback shortcut path. Fill in every `TBD` once T059
lands and the runs below are executed.

## Context (§3, ADR-013)

Altur's client sends **one call at a time** from a single grading station —
concurrency is not a judging condition. The numbers here are engineering
evidence for the Feasibility criterion (NFR-004/006), not a replay of how
the judge actually calls `/detect`.

## Prerequisite

- **T059** (`[Diego] Run the real train→calibrate→export→deploy chain
  end-to-end`) must be done first. As of this writing `GET /health` reports
  `model_version: none` and every `/detect` call returns only the fallback
  verdict `{"is_synthetic": false, "confidence": 0.50}` — a load test against
  that deployment would measure request-parsing + fallback-path latency
  only, not real ONNX inference, and `model_version` in the report below
  would be misleading. Re-run once T059 is merged and deployed.
- If a stakeholder wants pre-T059 numbers anyway (pure infra ceiling), they
  can be produced with the same script and clearly labeled
  "fallback-path only, not model_version-bearing" — but that is not this
  report's default path.

## Method

Tooling: `product/api/tests/load/run_load.py` (asyncio + httpx), driving
canonical Altur JSON bodies (`{"call_id", "audio_base64", "sample_rate",
"channels"}`, ADR-013) at `POST /detect` against the live deployment.

### (1) Concurrency

8 concurrent clients × 10 requests each (80 total), single ~180s val clip
(never committed to the repo — NFR-011). Reports:
- client-side p50/p95/p99 (measured by the script itself)
- server-side p50/p95/p99 for the `detect` route, from `GET /metrics`
  (JSON via `Accept: application/json`), sampled before and after the run

```bash
python3 product/api/tests/load/run_load.py concurrency \
  --base-url https://getconcorde.tech \
  --clip /path/to/val_180s.wav \
  --clients 8 --requests-per-client 10 \
  --out docs/load-report-concurrency.json
```

### (2) Soak

4 req/s sustained for 30 minutes, run in the background so it never blocks
the session; samples `ps -o rss` of `concorde-api` over Tailscale every 30s.

```bash
nohup python3 product/api/tests/load/run_load.py soak \
  --base-url https://getconcorde.tech \
  --clip /path/to/val_180s.wav \
  --rate 4 --duration-s 1800 \
  --ssh-host root@100.93.147.55 --ssh-port 2222 \
  --out docs/load-soak.jsonl --rss-out docs/load-soak-rss.jsonl \
  >> docs/load-soak-runner.log 2>&1 &
```

## Thresholds

| Threshold | Requirement | Result | Pass/Fail |
|---|---|---|---|
| p99 latency (8-way concurrency) | <= 5000 ms | TBD | TBD |
| Peak RSS during 30-min soak | <= 1.5 GB | TBD | TBD |
| Non-200 responses (either run) | 0 | TBD | TBD |

## Run metadata

- Commit SHA tested: TBD
- `model_version` (from `/health`): TBD
- Date/time of run: TBD
- Local ASR (T045) enabled at test time: TBD — if yes, report count of
  `semantic_available=false` responses attributable to the
  `CONCORDE_ASR_MAX_CONCURRENT` semaphore (T045/env.server.template).

## Concurrency results

TBD — paste `docs/load-report-concurrency.json` summary here once run.

## Soak results

TBD — paste `docs/load-soak.jsonl` / `docs/load-soak-rss.jsonl` summary here
once run.

## Follow-ups

- If any threshold fails: file via
  `python orchestration/scripts/add_task.py` and link it here.
- T058 (`Mutex<Model>` vs a Session pool) explicitly depends on these
  numbers — do not close T040 without also updating T058's owner that data
  is ready to analyze.

## T058 — Mutex\<Model\> vs Session pool: status and qualitative analysis

**Blocked on real numbers.** As of this writing T059 (real train→calibrate→
export→deploy chain) has not landed — `/health` still reports
`model_version: none` and every `/detect` call takes the fallback path, so
the concurrency/soak runs above are still `TBD`. A load test run today would
measure request-parsing + fallback-path latency only, not real ONNX
`session.run` latency, which is exactly the number this question turns on.
This section is prep work only — the qualitative reasoning and the pool
design to fall back on if numbers show it's needed — not the measured
finding T058's DoD asks for. **Do not mark T058 done from this section
alone; the verdict row below stays TBD until the real 8-way run exists.**

### What the lock actually serializes

Confirmed by reading `product/api/src/state.rs` and
`product/api/src/inference/model.rs`:

- `AppState.model` is `Arc<Mutex<Model>>` (`state.rs:14`) purely because
  `ort`'s `Session::run` takes `&mut self` — `Arc<Model>` alone doesn't
  compile against concurrent handlers at all, so this was never a capacity
  choice.
- The `Session` is built with `.with_intra_threads(1)` (`model.rs:79-82`):
  each `session.run` call is already single-threaded internally, so the
  Mutex isn't fighting the session for CPU cores — it's just enforcing that
  only one `run` call is in flight at a time.
- `Model::score` (`model.rs:93-118`) does exactly three things under the
  lock once acquired: build a `(1, 23)` f32 array from the feature vector,
  call `session.run`, and apply the Platt sigmoid + threshold to the
  scalar output. No I/O, no `.await` inside the critical section — the lock
  is held for the duration of one small matmul only, consistent with the
  comment at `state.rs:9-13`.
- Everything upstream of scoring (WAV decode, VAD, feature extraction) is
  per-request state with no shared lock, so it already parallelizes freely
  across concurrent `/detect` handlers; only the final `score()` call
  queues.

### What would make a pool necessary vs not

- **Mutex sufficient** if per-call `session.run` time stays close to the
  ~50 ms NFR-001 budget assumed at T022 time. At 8-way concurrency the
  worst-case queuing for the last request in line is ~8×(actual run time),
  so as long as real run time is on that order, queuing stays well inside
  the 5000 ms p99 threshold and the 1.3 s internal budget.
- **Pool needed** only if the real exported model's `session.run` turns out
  meaningfully slower than assumed (bigger tree ensemble, cold cache
  effects, etc.) such that 8×run-time approaches or exceeds the 5000 ms
  threshold, or if `/metrics`' server-side p95/p99 for the `detect` route
  shows queuing time (not decode/VAD/feature time) as the dominant
  contributor once T040 actually runs.
- If the numbers do call for a pool: a fixed-size `Vec<Mutex<Model>>` (or
  `Vec<Arc<Mutex<Model>>>`) of N `Session`s built from the same ONNX file
  at boot, checked out round-robin or via a semaphore — same NFR-007
  ready≤5s startup cost as today since all N load at boot, none of the
  per-request reload or per-thread unbounded pitfalls the task description
  rules out. `Model::load` already takes a `&Path` and returns an owned
  `Model`, so building N of them at startup is a small change localized to
  wherever `AppState` is constructed — no change to `Model::score`'s
  signature or the feature contract.

### Verdict

| Question | Answer |
|---|---|
| Mutex sufficient or pool needed? | **TBD — depends on T059 + a real T040 run** |
| Measured 8-way p99 | TBD |
| Queuing time vs total latency (from `/metrics`) | TBD |

Next step once T059 lands and T040's concurrency run is re-executed against
the real model: fill in the verdict row above from the actual
`docs/load-report-concurrency.json` output. If a pool turns out to be
needed, file it as its own follow-up via `add_task.py` (scope:
`product/api/src/inference/model.rs`, `product/api/src/state.rs`) rather
than expanding this task's scope further.
