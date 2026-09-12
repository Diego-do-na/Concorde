---
name: frontend
description: "Use when building or editing the concorde-console React dashboard (console/) — design tokens, the three views (live/detail/exec), or the demo upload flow."
---

# Frontend Skill — Concorde Console (React)

Full detail: `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf` §11 (frontend
spec), §8.2 (`/analyze` payload the console consumes). This file is the fast
reference.

## Governing requirement (NFR-012)

The dashboard must read as a production fintech operations console handling
150M+ calls/month — not a hackathon demo. Dark-mode-first, no default
framework theming, legible on a 13" laptop at 1x zoom.

## Design tokens (binding)

- **Mode**: dark by default. Bluish-charcoal base, never pure black.
  Elevated surfaces via luminance, not shadow.
- **Semantic accent**: exactly three states — verified (cool green), review
  (amber), synthetic (red). Color is never the only carrier of meaning:
  always paired with a text label and icon.
- **Typography**: one interface sans family, high x-height. Tabular figures
  mandatory in every table/metric so numeric columns align. Monospace only
  for identifiers and payloads.
- **Scale**: at most 4 text sizes. Hierarchy via weight/color before size.
- **Density**: high — compact rows, no inflated cards or decorative
  spacing. This is an ops console; it shows many calls at once.
- **Motion**: functional only (live status updates, confidence trace).
  Nothing decorative.
- **States**: every component defines loading / empty / error / degraded
  states. The screen must never go blank — on backend unreachability it
  degrades to the last known state, never an error screen.

## The three views (FR-012)

1. **Live monitor**: dense table of processed calls (id, duration, verdict,
   confidence, latency, timestamp) + a service-health header (model
   version, current p95, dependency degradation). WebSocket live updates
   with automatic silent fallback to polling.
2. **Call detail** (the screen that wins judging): both channels' waveforms
   stacked and time-synced, with markers at every overlap/interruption/long
   silence event; a confidence-over-time trace aligned to the waveform;
   per-signal breakdown (behavioral/semantic/acoustic) showing explicit
   availability — a degraded signal shows as degraded, never as zero; top
   contributing factors (value, direction, weight); one-to-two sentence
   natural-language rationale. All driven by `POST /analyze`, never
   `/detect`.
3. **Executive**: aggregate operational metrics + a business case scaled to
   Altur's real volume (150M+ calls/month) with assumptions **visible on
   screen and labelled as estimates**.

## Demo zone (FR-013)

Arbitrary WAV upload → live verdict. Keep two clips ready: one human
recording, one ElevenLabs-generated synthetic clip the model never saw —
this is what makes Robustness demonstrable rather than claimed.

## Testing expectations

- All three views reachable within 20 seconds from landing (walkthrough
  rehearsal, §14).
- Component-level loading/empty/error/degraded states verified, not just
  the happy path.
- Live demo rehearsed against both the human and ElevenLabs clips before
  judging.
