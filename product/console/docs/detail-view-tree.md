# Detail view (View 2) — component tree

- `src/views/detail/DetailView.tsx` — assembly: recent-calls sidebar, header metrics, plot container, two-column grid, event log.
  - Sidebar: recent rows (3px tone bar, mono id, "verdict · conf")
  - Header: `CALL (mono 19px)` · verdict badge · CONFIDENCE · THRESHOLD · DURATION · PROCESS LATENCY
  - Plot container (bordered, `surfaces.plot` bg):
    - `DualChannelWaveform` (`src/views/detail/waveform/DualChannelWaveform.tsx`) — caller/agent lanes, emits `onScrub`.
    - `ConfidenceTrace` (`src/views/detail/trace/ConfidenceTrace.tsx`) — P(SYN) trace, follower of scrub.
    - `MarkerOverlay` (`src/views/detail/markers/MarkerOverlay.tsx`) — vertical event markers (uses `TimeScale`).
  - Left column: `Factors` (`src/views/detail/factors/Factors.tsx`)
  - Right column: `SignalBreakdown` (`src/views/detail/signals/SignalBreakdown.tsx`) + timings/meta
  - `EventLog` (`src/views/detail/markers/EventLog.tsx`) — full-width below grid

Notes:
- Shared `TimeScale` coordinate space for waveform/markers/trace.
- Sidebar driven by `Feed`; selecting a call triggers `GET /feed/analysis/:id` — UI falls back to a retained-only state if the server didn't keep full analysis.

