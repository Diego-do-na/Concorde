"""Tests for train/build_dataset.py (T016).

Two tiers:
  - Unit tests against tiny synthetic manifests/turns — no dataset, no
    Rust toolchain/vad-dump binary required, always run.
  - One integration test against the live practice dataset AND a built
    `vad-dump` binary — skips when either is unavailable, matching
    eda/test_gates.py's / features/tests/test_dataset_extraction.py's
    convention (AGENTS.md, NFR-011: the dataset is never committed).
"""

from __future__ import annotations

import shutil
import time

import numpy as np
import pandas as pd
import pytest

from common.dataset import dataset_dir
from features.extract import FEATURE_NAMES
from train import build_dataset

# ---------------------------------------------------------------------------
# Unit tests: synthetic fixtures, no dataset / no cargo required.
# ---------------------------------------------------------------------------


def _tiny_manifest() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "anon_id": ["c1", "c2", "c3"],
            "label": ["human", "synthetic", "human"],
            "split": ["train", "train", "val"],
            "duration_s": [10.0, 20.0, 5.0],
        }
    )


_FAKE_REF_TURNS = {
    "c1": [(0, 1.0, 2.0), (1, 2.5, 4.0), (0, 4.2, 5.0)],
    "c2": [(1, 0.0, 1.0), (0, 1.2, 1.5)],
    "c3": [],  # no turns at all -- must stay finite, never NaN
}


def _fake_ref_turns_for(anon_id: str):
    return _FAKE_REF_TURNS[anon_id]


def test_build_table_schema_and_values():
    manifest = _tiny_manifest()
    df = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")

    assert list(df.columns) == [
        "anon_id",
        "label",
        "split",
        "duration_s",
        *FEATURE_NAMES,
        "source",
    ]
    assert len(df) == 3
    assert list(df["anon_id"]) == ["c1", "c2", "c3"]
    assert (df["source"] == "ref").all()
    feature_values = df[list(FEATURE_NAMES)].to_numpy(dtype=np.float64)
    assert not np.isnan(feature_values).any()
    assert np.isfinite(feature_values).all()


def test_build_table_empty_turns_is_finite_zero():
    manifest = _tiny_manifest()
    df = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")
    row = df[df["anon_id"] == "c3"].iloc[0]
    for name in FEATURE_NAMES:
        assert row[name] == 0.0


def test_build_table_is_idempotent():
    manifest = _tiny_manifest()
    df1 = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")
    df2 = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")
    pd.testing.assert_frame_equal(df1, df2)


def test_split_channels():
    caller, agent = build_dataset.split_channels(
        [(0, 1.0, 2.0), (1, 2.0, 3.0), (0, 4.0, 5.0)]
    )
    assert caller == [(1.0, 2.0), (4.0, 5.0)]
    assert agent == [(2.0, 3.0)]


def test_compute_skew_report_lists_all_features_and_flags_low_correlation():
    manifest = _tiny_manifest()
    df_ref = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")

    # "vad" turns equal to "ref" for c1/c2 (perfect agreement) but wildly
    # different for c3, so every feature has some spread to correlate over
    # and at least the ones sensitive to c3's turns get flagged.
    fake_vad_turns = dict(_FAKE_REF_TURNS)
    fake_vad_turns["c3"] = [(0, 0.5, 3.0), (1, 3.5, 4.5)]
    df_vad = build_dataset.build_table(
        manifest, lambda a: fake_vad_turns[a], source="vad"
    )

    report = build_dataset.compute_skew_report(df_ref, df_vad)

    assert "Calls compared: 3" in report
    for name in FEATURE_NAMES:
        assert name in report
    assert "VAD-sensitive" in report


def test_compute_skew_report_identical_sources_yields_zero_diff():
    manifest = _tiny_manifest()
    df_ref = build_dataset.build_table(manifest, _fake_ref_turns_for, source="ref")
    df_vad = build_dataset.build_table(manifest, _fake_ref_turns_for, source="vad")

    report = build_dataset.compute_skew_report(df_ref, df_vad)
    # Every feature's mean-abs-diff column must read 0.0000 when both
    # sources are byte-for-byte the same turns.
    for name in FEATURE_NAMES:
        assert f"| {name} | 0.0000 |" in report


# ---------------------------------------------------------------------------
# Integration test: the real practice dataset + a built vad-dump binary.
# ---------------------------------------------------------------------------


def _integration_available() -> bool:
    if not dataset_dir().is_dir():
        return False
    if build_dataset.vad_dump_binary_path().is_file():
        return True
    return shutil.which("cargo") is not None


@pytest.mark.skipif(
    not _integration_available(),
    reason="CONCORDE_DATASET_DIR not set, or no vad-dump binary and no cargo to build one",
)
def test_full_pipeline_against_live_dataset(tmp_path, monkeypatch):
    monkeypatch.setattr(build_dataset, "DATA_DIR", tmp_path)
    monkeypatch.setattr(build_dataset, "REF_PARQUET", tmp_path / "features_ref.parquet")
    monkeypatch.setattr(build_dataset, "VAD_PARQUET", tmp_path / "features_vad.parquet")
    monkeypatch.setattr(build_dataset, "SKEW_REPORT", tmp_path / "skew_report.md")

    build_dataset.ensure_vad_dump_built()  # warm/build once, outside the timed budget

    start = time.perf_counter()
    build_dataset.main()
    elapsed = time.perf_counter() - start
    assert elapsed < 180.0, f"build_dataset.main() took {elapsed:.1f}s (budget: 3 min)"

    df_ref = pd.read_parquet(build_dataset.REF_PARQUET)
    df_vad = pd.read_parquet(build_dataset.VAD_PARQUET)

    assert len(df_ref) == 353
    assert len(df_vad) == 353
    assert set(df_ref["anon_id"]) == set(df_vad["anon_id"])
    assert not df_ref[list(FEATURE_NAMES)].isna().any().any()
    assert not df_vad[list(FEATURE_NAMES)].isna().any().any()

    assert build_dataset.SKEW_REPORT.is_file()
    report = build_dataset.SKEW_REPORT.read_text()
    for name in FEATURE_NAMES:
        assert name in report

    # Idempotency: rerunning must reproduce identical tables.
    build_dataset.main()
    df_ref_2 = pd.read_parquet(build_dataset.REF_PARQUET)
    df_vad_2 = pd.read_parquet(build_dataset.VAD_PARQUET)
    pd.testing.assert_frame_equal(df_ref, df_ref_2)
    pd.testing.assert_frame_equal(df_vad, df_vad_2)
