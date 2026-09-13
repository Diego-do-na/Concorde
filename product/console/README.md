# CONCORDE Console (product/console)

React dashboard for the CONCORDE trust layer. Four views: live monitor, call
detail, executive summary, demo zone (§11, FR-012, FR-013).

## Running it

The console has **no mock mode and no fixtures in the dev path**. It renders
whatever `concorde-api` returns, and if no API is reachable it says so — the
header status dot goes DEGRADED and the monitor's empty state explains that
the feed is offline. That is deliberate: a stub answering `/health`,
`/metrics` and `/feed/recent` with plausible numbers is indistinguishable on
screen from a working service, and a console that can look healthy with
nothing behind it is worse than one that plainly cannot start.

So you need the API running first:

```bash
# 1. build and start concorde-api (from repo root)
cd product/api && cargo build --release --bin concorde
CONCORDE_BIND=127.0.0.1:8080 \
CONCORDE_MODEL_PATH="$PWD/../artifacts/model.onnx" \
CONCORDE_FEATURE_CONTRACT=fc-1 \
CONCORDE_SEMANTIC_ENABLED=false \
RUST_LOG=info,concorde_api=info \
  ../target/release/concorde

# 2. start the console (separate shell)
cd product/console
npm install
npm run dev              # proxies API routes to 127.0.0.1:8080
```

Point it somewhere else with `CONCORDE_API_TARGET`:

```bash
CONCORDE_API_TARGET=https://getconcorde.tech npm run dev
```

### Why a proxy at all

In production Caddy serves the console and the API from one origin
(`product/deploy/Caddyfile`), so every request in `src/lib/api.ts` is a
relative path. In dev there is no Caddy, so those paths would hit the Vite
server and 404. `vite.config.mts` forwards `/health`, `/version`, `/metrics`,
`/detect`, `/analyze`, `/feed/*`, `/history` and the `/ws` websocket to
`CONCORDE_API_TARGET`.

## Build and test

```bash
npm run build      # runs typecheck first, then vite build
npm run typecheck  # tsc --noEmit
npm run test       # vitest (jsdom)
```

Tests use msw and the fixture generator in `src/mocks/`. Those are test-only
— nothing in the dev or production path loads them.

## Populating the feed

The monitor reads `GET /feed/recent` and the `/ws` stream, which the service
fills as calls are scored. An idle API means an empty monitor. To put real
calls through it, POST WAVs at `/detect` (see
`product/deploy/replay_val.sh`), or use the Demo tab to upload one by hand.

The demo tab's pre-wired clips live in `public/demo/` and are gitignored
(§13.4 — no audio in the repo). Without them the clip buttons report that the
file is missing; drag-and-drop still works.

## What comes from where

Every figure on screen is sourced, because §11 requires it:

| View | Reads |
| --- | --- |
| Header | `/health` (model, uptime, deps), `/metrics` (p95, processed count), `/version` (feature contract) |
| Monitor | `/feed/recent` + `/ws` for rows; `/metrics` for the processed counter and p95 |
| Detail | `/feed/analysis/:id` — the *retained* `pipeline::Analysis` |
| Exec | `src/views/exec/metrics.json`, copied from `product/ml/data/metrics.json` at build time |
| Demo | `POST /detect` and `POST /analyze` on the uploaded clip |

### Two absences the UI states rather than hides

- **The detail view has no confidence trace, no top factors and no rationale
  for a call opened from the feed.** Those three are computed only by `POST
  /analyze`; the feed retains `pipeline::Analysis`, which has none of them.
  The view says so in place of each. The full detail view is reachable via
  the Demo tab, which calls `/analyze` directly.
- **Every feed row shows semantic and acoustic as degraded.**
  `feed::Feed::push` sets both to `None`, so the monitor's degradation rate
  reads 100%. That is the shipped behaviour, not a display bug.

## Exec view figures

The exec view is built from `metrics.json` and a set of business assumptions.
Every row carries a provenance tag — STATED, ESTIMATE, DERIVED or MEASURED —
and the `_source` note from `metrics.json` is printed at the bottom of the
page so the MEASURED tags are checkable rather than decorative.

Assumptions and their sources:

- Monthly call volume: 150 000 000 — STATED (Altur brief)
- Share screened: 100% — ESTIMATE
- Voice-fraud attempt rate: 0.040% (industry range 0.02–0.08%) — ESTIMATE
- Loss per incident: $1,850 — ESTIMATE
- Detection recall at threshold — MEASURED, from `metrics.json`
- Inference CPU-seconds per call — ESTIMATE, see `docs/latency-report.md`

`metrics.json` is copied at build time by `scripts/copy-metrics.js` from
`product/ml/data/metrics.json`.
