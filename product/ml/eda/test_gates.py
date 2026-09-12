"""Asserts the four §10.1 EDA gate numbers against the live dataset.

Skips entirely when CONCORDE_DATASET_DIR isn't set to a real dataset dir —
the dataset is never committed (AGENTS.md, NFR-011), so CI and machines
without it must not fail here.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from common.dataset import dataset_dir, load_manifest  # noqa: E402
from eda.run_eda import (  # noqa: E402
    gate_class_balance,
    gate_duration_vs_label,
    gate_manifest_and_wav,
    gate_overlap_density,
)

pytestmark = pytest.mark.skipif(
    not dataset_dir().is_dir(),
    reason="CONCORDE_DATASET_DIR not set to an existing directory",
)

EXPECTED_MANIFEST_SHA256 = (
    "4fa5ac3f25f2bc1fbff9a06a89f621fcca30db5f1443bbd164368193f1d7c544"
)


@pytest.fixture(scope="module")
def manifest():
    return load_manifest()


def test_gate1_class_balance(manifest):
    result = gate_class_balance(manifest)
    by_split = {r["split"]: r for r in result["rows"]}

    assert by_split["train"]["n"] == 282
    assert by_split["train"]["n_human"] == 113
    assert by_split["train"]["n_synthetic"] == 169

    assert by_split["val"]["n"] == 71
    assert by_split["val"]["n_human"] == 37
    assert by_split["val"]["n_synthetic"] == 34

    # train sits on the 60/40 boundary (59.9/40.1) -> class_weight is applied
    # even though the literal ">60%" gate does not strictly fire.
    assert result["imbalance_beyond_60_40"] is False
    assert result["apply_class_weight"] is True


def test_gate2_overlap_density(manifest):
    result = gate_overlap_density(manifest)

    assert result["overall_frac"] == pytest.approx(0.946, abs=0.005)
    assert result["human_frac"] == pytest.approx(0.98, abs=0.01)
    assert result["synthetic_frac"] == pytest.approx(0.92, abs=0.01)

    # R-04 gate: fewer than 40% of calls with overlap would trigger it.
    assert result["r04_triggered"] is False


def test_gate3_duration_vs_label(manifest):
    result = gate_duration_vs_label(manifest)

    assert result["mean_duration_human_s"] == pytest.approx(149.7, abs=1.0)
    assert result["mean_duration_synthetic_s"] == pytest.approx(146.2, abs=1.0)

    # Duration must not leak the label, or it would be an unusable
    # practice-set shortcut (§10.1 gate 3).
    assert result["discriminative_power"] < 0.6
    assert result["duration_banned"] is False


def test_gate4_manifest_hash_and_wav_census(manifest):
    result = gate_manifest_and_wav(manifest)

    assert result["manifest_sha256"] == EXPECTED_MANIFEST_SHA256
    assert result["n_wavs_checked"] == 353
    assert result["all_stereo_8k_16bit"] is True
    assert result["formats"] == {(2, 8000, 2): 353}

    # No speaker-id column -> ADR-012's literal speaker-grouped CV is not
    # implementable; T016/T017 must use the documented fallback instead.
    assert result["has_speaker_id_column"] is False
