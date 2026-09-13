# CONCORDE Console (product/console)

Run the dev server:

```bash
npm install
npm run dev
```

Build:

```bash
npm run build
```

Tests:

```bash
npm run test
```

... (token table and docs omitted for brevity — original README preserved in repository) ...

View: Exec — Executive view overview (new)
---------------------------------------

This view lives at `/exec`. It presents an executive summary: top-line stats strip, verdict distributions, latency bars, and a business-case table with clear assumption tags.

Assumptions and sources (complete list of every assumption and its source):

- Monthly call volume: 150 000 000 — STATED (Altur brief)
- Share screened: 100% — ESTIMATE
- Voice-fraud attempt rate: 0.040% (industry range 0.02–0.08%) — ESTIMATE
- Loss per incident: $1,850 — ESTIMATE
- Detection recall at threshold: MEASURED — pulled from `product/console/src/views/exec/metrics.json` at build time (copy of `product/ml/data/metrics.json`)
- Inference CPU-seconds per call: ESTIMATE — see `docs/latency-report.md`

Notes:
- `metrics.json` is copied at build time by `scripts/copy-metrics.js`. If you need to update the measured values, update `product/ml/data/metrics.json` (the build script will copy it when you run `npm run build`) or directly edit `product/console/src/views/exec/metrics.json` for local debugging.
- The banner "EVERY FIGURE BELOW IS A LABELLED ESTIMATE" is intentionally prominent on the Exec screen; MEASURED rows are tagged accordingly.

