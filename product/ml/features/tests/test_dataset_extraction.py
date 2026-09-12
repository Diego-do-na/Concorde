"""Runs the extractor over the whole practice dataset (FR: <10s, no NaN).

Skips entirely when CONCORDE_DATASET_DIR isn't set to a real dataset dir —
the dataset is never committed (AGENTS.md, NFR-011) — matching
eda/test_gates.py's convention.
"""

from __future__ import annotations

import time

import numpy as np
import pytest

from common.dataset import dataset_dir, iter_split, load_turns
from features.extract import extract

pytestmark = pytest.mark.skipif(
    not dataset_dir().is_dir(),
    reason="CONCORDE_DATASET_DIR not set to an existing directory",
)


def test_extract_whole_dataset_is_fast_and_finite():
    start = time.perf_counter()
    n = 0
    for row in iter_split(None):
        turns = load_turns(row["anon_id"])
        caller = [(s, e) for ch, s, e in turns if ch == 0]
        agent = [(s, e) for ch, s, e in turns if ch == 1]
        vec = extract(caller, agent, float(row["duration_s"]))
        assert vec.shape == (23,)
        assert np.isfinite(vec).all(), f"non-finite feature for {row['anon_id']}"
        n += 1
    elapsed = time.perf_counter() - start

    assert n > 0
    assert elapsed < 10.0, f"extract() over {n} calls took {elapsed:.2f}s (budget: 10s)"
