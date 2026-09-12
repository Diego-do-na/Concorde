"""Python reference behavioral feature extractor — contract fc-1 (§9).

This module is the single source of truth for the fc-1 feature vector
(F-01…F-22, 23 floats — F-21 is two values). The Rust production extractor
(`api/src/features/`, T014) mirrors this file line by line; any behavioral
difference between the two is a bug in whichever one deviates from §9, not
an acceptable "reference vs. production" drift.

Notation (§9): C = caller turns (channel 0), A = agent turns (channel 1),
each a half-open interval [start, end) in seconds. D = call duration.
CV = coefficient of variation (population std / mean).

`extract()` takes already-channel-split turn lists so it works identically
on `turns/<id>.json` (practice dataset, ADR-003) and on this system's own
VAD output at serving time — neither caller sees a channel column.

Degenerate-input contract (never NaN/inf, see also product/ml/README.md):
  - empty latency/duration/delay sets  -> mean/median/std/min = 0.0, cv = 0.0
  - ratios and counts with empty/zero denominators -> 0.0
  - cv is std/mean, defined as 0.0 whenever mean == 0.0 (never 0/0 -> NaN)
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

import numpy as np

Interval = Tuple[float, float]

# §9 thresholds, shared verbatim with the Rust extractor (T014).
_MONOTONY_WINDOW_S = 0.15
_ABORT_WINDOW_S = 0.4
_SHORT_TURN_S = 1.0
_FRAGMENTATION_GAP_S = 0.5
_SILENCE_GAP_S = 2.0

# fc-1, in the exact order frozen by product/artifacts/feature_contract_fc-1.json.
FEATURE_NAMES: Tuple[str, ...] = (
    "resp_latency_mean",
    "resp_latency_median",
    "resp_latency_std",
    "resp_latency_cv",
    "latency_monotony_index",
    "resp_latency_min",
    "overlap_count",
    "overlap_rate_per_min",
    "overlap_total_dur",
    "caller_bargein_count",
    "recovery_delay_mean",
    "recovery_delay_cv",
    "recovery_abort_ratio",
    "caller_turn_dur_mean",
    "caller_turn_dur_std",
    "caller_turn_dur_cv",
    "short_turn_ratio",
    "fragmentation_rate",
    "caller_speech_ratio",
    "speech_balance",
    "silence_break_delay_mean",
    "silence_break_delay_cv",
    "turn_count_caller",
)


def _stats(values: Sequence[float]) -> Tuple[float, float, float, float]:
    """(mean, median, population-std, min) of `values`; all 0.0 if empty."""
    if not values:
        return 0.0, 0.0, 0.0, 0.0
    arr = np.asarray(values, dtype=np.float64)
    return (
        float(arr.mean()),
        float(np.median(arr)),
        float(arr.std(ddof=0)),
        float(arr.min()),
    )


def _cv(std: float, mean: float) -> float:
    """std / mean, defined as 0.0 when mean is 0.0 (never NaN from 0/0)."""
    return float(std / mean) if mean != 0.0 else 0.0


def _first_at_or_after(sorted_turns: Sequence[Interval], threshold: float) -> Optional[Interval]:
    """First turn in `sorted_turns` (sorted by start) with start >= threshold."""
    for turn in sorted_turns:
        if turn[0] >= threshold:
            return turn
    return None


def _merge_busy_intervals(intervals: Sequence[Interval]) -> List[Interval]:
    """Merge overlapping/touching intervals (both channels) into busy spans."""
    if not intervals:
        return []
    ordered = sorted(intervals)
    merged: List[List[float]] = [list(ordered[0])]
    for start, end in ordered[1:]:
        if start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    return [(s, e) for s, e in merged]


def extract(caller: Sequence[Interval], agent: Sequence[Interval], duration_s: float) -> np.ndarray:
    """The fc-1 feature vector for one call, per §9, as 23 float64s.

    `caller` / `agent` are lists of (start, end) half-open intervals in
    seconds for channel 0 / channel 1 respectively; unsorted input is
    accepted. `duration_s` is the call duration D.
    """
    caller_sorted = sorted((float(s), float(e)) for s, e in caller)
    agent_sorted = sorted((float(s), float(e)) for s, e in agent)
    duration_s = float(duration_s)

    # F-01…F-06 — response latency: for every agent turn a, the first
    # caller turn c with c.start >= a.end.
    latencies = []
    for a_start, a_end in agent_sorted:
        c = _first_at_or_after(caller_sorted, a_end)
        if c is not None:
            latencies.append(c[0] - a_end)
    lat_mean, lat_median, lat_std, lat_min = _stats(latencies)
    f01 = lat_mean
    f02 = lat_median
    f03 = lat_std
    f04 = _cv(f03, f01)
    if latencies:
        f05 = sum(
            1 for x in latencies if abs(x - lat_median) <= _MONOTONY_WINDOW_S
        ) / len(latencies)
    else:
        f05 = 0.0
    f06 = lat_min

    # F-07…F-10 — overlap and interruption. An (a, c) pair intersects iff
    # a.start < c.end and c.start < a.end (half-open interval overlap).
    overlap_events = []  # (a, c, overlap_start, overlap_end)
    for a_start, a_end in agent_sorted:
        for c_start, c_end in caller_sorted:
            if a_start < c_end and c_start < a_end:
                ov_start = max(a_start, c_start)
                ov_end = min(a_end, c_end)
                overlap_events.append((a_start, a_end, c_start, c_end, ov_start, ov_end))

    f07 = float(len(overlap_events))
    f08 = f07 / (duration_s / 60.0) if duration_s > 0.0 else 0.0
    f09 = sum(ov_end - ov_start for *_r, ov_start, ov_end in overlap_events)

    f10 = 0.0
    for c_start, _c_end in caller_sorted:
        if any(a_start < c_start < a_end for a_start, a_end in agent_sorted):
            f10 += 1.0

    # F-11…F-13 — recovery after an overlap event.
    recovery_delays = []
    abort_count = 0
    for _a_start, _a_end, c_start, c_end, ov_start, ov_end in overlap_events:
        nxt = _first_at_or_after(caller_sorted, ov_end)
        if nxt is not None:
            recovery_delays.append(nxt[0] - ov_end)
        if (c_end - ov_start) <= _ABORT_WINDOW_S:
            abort_count += 1
    rec_mean, _rec_median, rec_std, _rec_min = _stats(recovery_delays)
    f11 = rec_mean
    f12 = _cv(rec_std, f11)
    f13 = (abort_count / f07) if f07 > 0.0 else 0.0

    # F-14…F-18 — caller turn morphology.
    caller_durs = [e - s for s, e in caller_sorted]
    dur_mean, _dur_median, dur_std, _dur_min = _stats(caller_durs)
    f14 = dur_mean
    f15 = dur_std
    f16 = _cv(f15, f14)
    f17 = (
        sum(1 for d in caller_durs if d < _SHORT_TURN_S) / len(caller_durs)
        if caller_durs
        else 0.0
    )

    fragmentation_count = 0
    for (_s0, e0), (s1, _e1) in zip(caller_sorted, caller_sorted[1:]):
        if (s1 - e0) < _FRAGMENTATION_GAP_S:
            fragmentation_count += 1
    f18 = fragmentation_count / (duration_s / 60.0) if duration_s > 0.0 else 0.0

    # F-19…F-20 — channel share.
    caller_total = sum(caller_durs)
    agent_total = sum(e - s for s, e in agent_sorted)
    f19 = caller_total / duration_s if duration_s > 0.0 else 0.0
    f20 = caller_total / agent_total if agent_total > 0.0 else 0.0

    # F-21 — silence-break delay: for every mutual-silence gap > 2.0 s
    # (including before the first turn and after the last, up to D), the
    # time from the start of the gap until the caller's next turn begins.
    busy = _merge_busy_intervals(list(caller_sorted) + list(agent_sorted))
    silence_gaps: List[float] = []
    cursor = 0.0
    for start, end in busy:
        if start - cursor > _SILENCE_GAP_S:
            silence_gaps.append(cursor)
        cursor = max(cursor, end)
    if duration_s - cursor > _SILENCE_GAP_S:
        silence_gaps.append(cursor)

    silence_delays = []
    for gap_start in silence_gaps:
        nxt = _first_at_or_after(caller_sorted, gap_start)
        if nxt is not None:
            silence_delays.append(nxt[0] - gap_start)
    sil_mean, _sil_median, sil_std, _sil_min = _stats(silence_delays)
    f21a = sil_mean
    f21b = _cv(sil_std, f21a)

    # F-22 — normalising turn count.
    f22 = float(len(caller_sorted))

    return np.array(
        [
            f01, f02, f03, f04, f05, f06,
            f07, f08, f09, f10,
            f11, f12, f13,
            f14, f15, f16, f17, f18,
            f19, f20,
            f21a, f21b,
            f22,
        ],
        dtype=np.float64,
    )
