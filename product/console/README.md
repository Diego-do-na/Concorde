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

Token table (name -> value)

- fonts.body -> IBM Plex Sans, system-ui, sans-serif
- fonts.mono -> IBM Plex Mono, monospace
- surfaces.page -> oklch(0.185 0.014 252)
- surfaces.panel -> oklch(0.2 0.014 252)
- surfaces.panelAlt -> oklch(0.197 0.014 252)
- surfaces.header -> oklch(0.215 0.015 252)
- surfaces.plot -> oklch(0.205 0.014 252)
- surfaces.chipBg -> oklch(0.235 0.015 252)
- surfaces.hover -> oklch(0.245 0.016 252)
- surfaces.tooltip -> oklch(0.28 0.016 252)
- surfaces.tooltipBorder -> oklch(0.4 0.016 252)
- surfaces.tableAlt1 -> oklch(0.225 0.015 252)
- surfaces.tableAlt2 -> oklch(0.207 0.014 252)
- lines.line -> oklch(0.3 0.016 252)
- lines.innerA -> oklch(0.28 0.016 252)
- lines.innerB -> oklch(0.25 0.015 252)
- lines.rowDivider -> oklch(0.24 0.014 252)
- lines.chipBorder -> oklch(0.32 0.016 252)
- ink.INK -> oklch(0.94 0.006 252)
- ink.INK2 -> oklch(0.72 0.01 252)
- ink.MUTED -> oklch(0.6 0.012 252)
- ink.DIM_LABEL -> oklch(0.56 0.012 252)
- semantic.VERIFIED.color -> oklch(0.74 0.12 168)
- semantic.VERIFIED.dim -> oklch(0.42 0.07 168)
- semantic.VERIFIED.wash -> oklch(0.27 0.035 168)
- semantic.REVIEW.color -> oklch(0.79 0.13 78)
- semantic.REVIEW.dim -> oklch(0.44 0.08 78)
- semantic.REVIEW.wash -> oklch(0.28 0.04 78)
- semantic.SYNTHETIC.color -> oklch(0.66 0.17 24)
- semantic.SYNTHETIC.dim -> oklch(0.40 0.09 24)
- semantic.SYNTHETIC.wash -> oklch(0.27 0.045 24)

Rules:

- Exactly three semantic states: `VERIFIED`, `REVIEW`, `SYNTHETIC`. Never rely on colour alone — always include a label and an icon/dot.

# Concorde Console (product/console) — T005 shell

Run locally:

- Install: `npm install`
- Dev server: `npm run dev` (opens Vite dev server on 5173)
- Build: `npm run build`
- Tests: `npm run test`

Token table (name -> value) — copied from `src/theme/tokens.ts`:

- FONTS.BODY: "IBM Plex Sans", system-ui, sans-serif
- FONTS.MONO: "IBM Plex Mono", monospace
- SURFACES.PAGE_BG: oklch(0.185 0.014 252)
- SURFACES.PANEL: oklch(0.2 0.014 252)
- SURFACES.PANEL_ALT: oklch(0.197 0.014 252)
- SURFACES.HEADER: oklch(0.215 0.015 252)
- SURFACES.PLOT: oklch(0.205 0.014 252)
- SURFACES.CHIP_BG: oklch(0.235 0.015 252)
- SURFACES.HOVER: oklch(0.245 0.016 252)
- SURFACES.TOOLTIP_BG: oklch(0.28 0.016 252)
- SURFACES.TOOLTIP_BORDER: oklch(0.4 0.016 252)
- SURFACES.TABLE_ROW_ALT: oklch(0.225 0.015 252)
- SURFACES.TABLE_ROW_ALT_2: oklch(0.207 0.014 252)
- LINES.LINE: oklch(0.3 0.016 252)
- LINES.INNER_A: oklch(0.28 0.016 252)
- LINES.INNER_B: oklch(0.25 0.015 252)
- LINES.ROW_DIV: oklch(0.24 0.014 252)
- LINES.CHIP_BORDER: oklch(0.32 0.016 252)
- INK.INK: oklch(0.94 0.006 252)
- INK.INK2: oklch(0.72 0.01 252)
- INK.MUTED: oklch(0.6 0.012 252)
- INK.DIM_LABEL: oklch(0.56 0.012 252)
- INK.MONO1: oklch(0.86 0.008 252)
- INK.MONO2: oklch(0.88 0.008 252)
- INK.MONO3: oklch(0.9 0.008 252)
- INK.PLAYHEAD: oklch(0.95 0.006 252)
- INK.LINK: oklch(0.78 0.05 252)
- INK.LINK_HOVER: oklch(0.9 0.05 252)
- INK.FOCUS: oklch(0.8 0.05 252)
- INK.SELECTION: oklch(0.4 0.05 252)
- SEMANTIC.VERIFIED.COLOR: oklch(0.74 0.12 168)
- SEMANTIC.VERIFIED.DIM: oklch(0.42 0.07 168)
- SEMANTIC.VERIFIED.WASH: oklch(0.27 0.035 168)
- SEMANTIC.REVIEW.COLOR: oklch(0.79 0.13 78)
- SEMANTIC.REVIEW.DIM: oklch(0.44 0.08 78)
- SEMANTIC.REVIEW.WASH: oklch(0.28 0.04 78)
- SEMANTIC.SYNTHETIC.COLOR: oklch(0.66 0.17 24)
- SEMANTIC.SYNTHETIC.DIM: oklch(0.40 0.09 24)
- SEMANTIC.SYNTHETIC.WASH: oklch(0.27 0.045 24)
- DEGRADED.HATCH: repeating-linear-gradient(135deg, oklch(0.46 0.012 252) 0 3px, oklch(0.3 0.014 252) 3px 6px)
- DEGRADED.LABEL: DEGRADED

Rule: exactly three semantic states (VERIFIED, REVIEW, SYNTHETIC); never rely on colour alone — always include label text + dot/icon.

## Routing

- `/` → Monitor
- `/calls/:id` → Detail
- `/exec` → Exec
- `/demo` → Demo

## Degrade rule

- The header keeps the last-known health values when `GET /health` fails. The feed chip switches to `OFFLINE` and dependency chips become amber to indicate degraded state. This ensures the header never blanks (NFR-012).

## Mocks and API client

- Enable dev mocks with `VITE_USE_MOCKS=1` (default off). When enabled the dev server serves deterministic fixtures for `/analyze`, `/feed/*`, `/health`, and `/metrics` using `product/console/src/mocks`.
- API base URL can be overridden with `VITE_API_BASE` (default same-origin).

Type table: the console mirrors spec §8.2 for the analysis payload (verdict, signals, degraded, timeline, turns, events, features, top_factors, rationale, timings_ms, meta). The console adds a "console-only enrichment" field `waveform`:

- `waveform { caller:number[], agent:number[], bucket_ms:50 }` — peak-envelope buckets for rendering only; this field is never sent by `/detect` and is flagged as an `/analyze` enrichment (T029).

