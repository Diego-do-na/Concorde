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
