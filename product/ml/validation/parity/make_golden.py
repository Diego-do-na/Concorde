#!/usr/bin/env python3
"""Generate golden fc-1 feature vectors for Python<->Rust parity (FR-005).

Writes one JSON fixture per call to `golden/<anon_id>.json`:

    {"caller": [[s, e], ...], "agent": [[s, e], ...], "duration_s": D,
     "fc1": [23 floats], "contract": "fc-1"}

`caller`/`agent` are the exact turn intervals fed to both extractors —
`api/tests/parity.rs` (T015) loads every file here, re-runs the Rust
extractor on `caller`/`agent`/`duration_s`, and asserts its output matches
`fc1` (computed by the Python reference extractor, `features/extract.py`)
element-wise within 1e-6.

Two fixture sources:

1. 10 fixed anon_ids (5 human, 5 synthetic) from `train`, listed below by
   name so this script is deterministic and idempotent — no random
   sampling. Only `turns/<id>.json` intervals and `manifest.csv`'s
   duration_s are read; no audio, no label, no split leaves this script
   (none of that is audio-bearing data, AGENTS.md NFR-011).
2. 3 hand-built degenerate fixtures (no dataset access) exercising corners
   fc-1 must never turn into NaN/inf on: no caller turns at all, one turn
   per channel, and turns that fully overlap.

Re-running this script must be a no-op against a clean checkout: it always
recomputes and rewrites every golden file, and the Python extractor is
pure, so a re-run against an unchanged dataset produces byte-identical
output (verified by (b) in T015's Definition of Done).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

_ML_ROOT = Path(__file__).resolve().parents[2]  # product/ml
sys.path.insert(0, str(_ML_ROOT))

from common import dataset  # noqa: E402
from features.extract import FEATURE_NAMES, extract  # noqa: E402

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

# Fixed anon_ids from `train`, picked once by lexicographic order of the
# manifest so the choice is reproducible without embedding randomness —
# never re-sample these lists; add new fixtures instead if more are needed.
HUMAN_ANON_IDS: Tuple[str, ...] = (
    "call_04d682ac0cef",
    "call_0615c3630150",
    "call_092aef8d1243",
    "call_0a9c546208d1",
    "call_0ab7d2a0c0f5",
)
SYNTHETIC_ANON_IDS: Tuple[str, ...] = (
    "call_0181ce113ebe",
    "call_018c9d3823ac",
    "call_01bf49059daf",
    "call_01c3806808d6",
    "call_02c249d2f89d",
)

Interval = Tuple[float, float]


def _f32_round_trip(x: float) -> float:
    """Round `x` to f32 precision, matching the Rust extractor's input.

    `features::extract` in `api/` takes `f32` turn boundaries (its own §9
    signature, mirroring real VAD output) and promotes to f64 internally.
    The Python reference extractor takes f64 throughout. Feeding both
    extractors the *same* f32-precision numbers (rather than the dataset's
    full f64 precision on the Python side only) is what makes the 1e-6
    parity tolerance meaningful — otherwise every fixture with many turns
    accumulates pure f32-promotion rounding well past 1e-6 with no actual
    behavioral disagreement between the two extractors.
    """
    return float(np.float32(x))


def _split_channels(turns: List[Tuple[int, float, float]]) -> Tuple[List[Interval], List[Interval]]:
    caller = [(_f32_round_trip(s), _f32_round_trip(e)) for ch, s, e in turns if ch == 0]
    agent = [(_f32_round_trip(s), _f32_round_trip(e)) for ch, s, e in turns if ch == 1]
    return caller, agent


def _duration_for(anon_id: str) -> float:
    manifest = dataset.load_manifest()
    rows = manifest[manifest["anon_id"] == anon_id]
    if rows.empty:
        raise KeyError(f"anon_id {anon_id!r} not found in manifest.csv")
    return float(rows.iloc[0]["duration_s"])


def _write_fixture(name: str, caller: List[Interval], agent: List[Interval], duration_s: float) -> None:
    fc1 = extract(caller, agent, duration_s)
    assert len(fc1) == len(FEATURE_NAMES) == 23
    payload = {
        "caller": [[float(s), float(e)] for s, e in caller],
        "agent": [[float(s), float(e)] for s, e in agent],
        "duration_s": float(duration_s),
        "fc1": [float(x) for x in fc1],
        "contract": "fc-1",
    }
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    out_path = GOLDEN_DIR / f"{name}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")


def _dataset_fixtures() -> None:
    for anon_id in HUMAN_ANON_IDS + SYNTHETIC_ANON_IDS:
        turns = dataset.load_turns(anon_id)
        caller, agent = _split_channels(turns)
        duration_s = _f32_round_trip(_duration_for(anon_id))
        _write_fixture(anon_id, caller, agent, duration_s)


def _degenerate_fixtures() -> None:
    # No caller turns at all — the agent speaks into total silence from
    # channel 0's point of view.
    _write_fixture(
        "degenerate_no_caller_turns",
        caller=[],
        agent=[(0.0, 5.0), (6.0, 9.0)],
        duration_s=10.0,
    )
    # Exactly one turn per channel, no overlap.
    _write_fixture(
        "degenerate_one_turn_each",
        caller=[(3.0, 5.0)],
        agent=[(0.0, 2.0)],
        duration_s=6.0,
    )
    # Caller and agent turns fully overlap (both channels talk the entire
    # call, one contained in the other).
    _write_fixture(
        "degenerate_fully_overlapping",
        caller=[(0.0, 10.0)],
        agent=[(0.0, 10.0)],
        duration_s=10.0,
    )


def main() -> None:
    _dataset_fixtures()
    _degenerate_fixtures()
    n = len(HUMAN_ANON_IDS) + len(SYNTHETIC_ANON_IDS) + 3
    print(f"wrote {n} golden fixtures to {GOLDEN_DIR}")


if __name__ == "__main__":
    main()
