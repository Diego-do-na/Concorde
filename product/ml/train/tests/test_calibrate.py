"""Tests for train/calibrate.py (T018).

Same tier as train/tests/test_train.py: synthetic in-memory feature tables
under tmp_path, no CONCORDE_DATASET_DIR, no real LightGBM model required —
a real (small) model is trained on the fixture via train.train.main() so the
calibration pipeline is exercised end-to-end.
"""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import pandas as pd
import pytest

from features.extract import FEATURE_NAMES
from train import calibrate
from train import train as train_module

PASSING_REPORT = """# VAD agreement report (FR-004)

Gate
----
FR-004 VAD agreement gate: PASS
"""


def _make_features_df(n_human: int, n_synthetic: int, split: str, rng: np.random.RandomState) -> pd.DataFrame:
    """A features_vad.parquet-shaped table with a moderate (not perfect,
    not absent) signal on resp_latency_mean — separable enough for CV AUC
    to sit well above chance without being degenerate, so Platt scaling has
    a non-trivial sigmoid to fit and thresholds aren't trivially 0/1."""
    labels = ["human"] * n_human + ["synthetic"] * n_synthetic
    rows = []
    for i, label in enumerate(labels):
        target = 1.0 if label == "synthetic" else 0.0
        feats = {name: float(rng.normal(0.0, 1.0)) for name in FEATURE_NAMES}
        feats["resp_latency_mean"] = target * 1.5 + float(rng.normal(0.0, 1.0))
        rows.append(
            {
                "anon_id": f"{split}-{i}",
                "label": label,
                "split": split,
                "duration_s": 60.0,
                **feats,
                "source": "vad",
            }
        )
    return pd.DataFrame.from_records(
        rows, columns=["anon_id", "label", "split", "duration_s", *FEATURE_NAMES, "source"]
    )


@pytest.fixture()
def features_path(tmp_path):
    rng = np.random.RandomState(7)
    train_df = _make_features_df(60, 90, "train", rng)
    val_df = _make_features_df(25, 25, "val", rng)
    df = pd.concat([train_df, val_df], ignore_index=True)
    path = tmp_path / "features_vad.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


@pytest.fixture()
def passing_report_path(tmp_path):
    path = tmp_path / "REPORT.md"
    path.write_text(PASSING_REPORT, encoding="utf-8")
    return path


@pytest.fixture()
def model_path(features_path, passing_report_path, tmp_path):
    out_model = tmp_path / "model_lgbm.txt"
    out_meta = tmp_path / "train_meta.json"
    rc = train_module.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(out_model),
            "--meta-path", str(out_meta),
            "--vad-report-path", str(passing_report_path),
        ]
    )
    assert rc == 0
    return out_model


@pytest.fixture()
def calibration_paths(tmp_path):
    return tmp_path / "calibration.json", tmp_path / "reliability_curve.png"


@pytest.fixture()
def run_calibrate(features_path, model_path, calibration_paths):
    calibration_path, reliability_path = calibration_paths
    rc = calibrate.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--calibration-path", str(calibration_path),
            "--reliability-path", str(reliability_path),
        ]
    )
    assert rc == 0
    return json.loads(calibration_path.read_text()), reliability_path


# ---------------------------------------------------------------------------
# Data loading — val is legitimate here (ADR-012's designated use).
# ---------------------------------------------------------------------------


def test_load_all_features_requires_both_splits(tmp_path):
    rng = np.random.RandomState(0)
    df = _make_features_df(10, 10, "train", rng)
    path = tmp_path / "train_only.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    with pytest.raises(RuntimeError, match="missing split"):
        calibrate.load_all_features(path)


def test_load_all_features_returns_both_splits(features_path):
    df = calibrate.load_all_features(features_path)
    assert set(df["split"].unique()) == {"train", "val"}


# ---------------------------------------------------------------------------
# Platt scaling: monotonicity and Brier improvement.
# ---------------------------------------------------------------------------


def test_apply_platt_is_monotonic_in_raw_score():
    rng = np.random.RandomState(1)
    scores = np.sort(rng.uniform(0.0, 1.0, 200))
    y = (scores + rng.normal(0.0, 0.05, 200) > 0.5).astype(int)
    a, b = calibrate.fit_platt(scores, y)
    calibrated = calibrate.apply_platt(scores, a, b)
    assert np.all(np.diff(calibrated) >= -1e-9)  # scores already ascending


def test_platt_calibration_end_to_end_is_monotonic(features_path, model_path):
    df = calibrate.load_all_features(features_path)
    val_df = df[df["split"] == "val"].reset_index(drop=True)
    X_val = val_df[list(FEATURE_NAMES)]
    y_val = calibrate.label_to_target(val_df["label"])

    booster = lgb.Booster(model_file=str(model_path))
    raw = booster.predict(X_val)
    a, b = calibrate.fit_platt(raw, y_val)

    order = np.argsort(raw)
    calibrated_sorted = calibrate.apply_platt(raw[order], a, b)
    assert np.all(np.diff(calibrated_sorted) >= -1e-9)


def test_brier_after_calibration_le_before(run_calibrate):
    calibration, _ = run_calibrate
    assert calibration["brier_val_after"] <= calibration["brier_val_before"] + 1e-9


# ---------------------------------------------------------------------------
# Threshold selection.
# ---------------------------------------------------------------------------


def test_select_threshold_ties_broken_by_asymmetric_cost():
    # index: 0=human p=.1, 1=synthetic p=.3, 2=human p=.5, 3=synthetic p=.9
    # thr in (.1,.3]: FP=1 (idx2), FN=0           -> BA=.75, cost=3*1+0=3
    # thr in (.5,.9]: FP=0,        FN=1 (idx1)    -> BA=.75, cost=0+1*1=1
    # both are the max-BA plateau; the cheaper (fewer FP) one must win.
    probs = np.array([0.1, 0.3, 0.5, 0.9])
    y = np.array([0, 1, 0, 1])
    threshold, info = calibrate.select_threshold(probs, y)
    assert threshold == pytest.approx(0.9)
    assert info["ba_train_oof"] == pytest.approx(0.75)
    assert info["n_tied_ba"] == 2
    assert info["n_tied_cost"] == 1


def test_select_threshold_is_strictly_inside_unit_interval():
    probs = np.array([0.0, 0.5, 1.0])
    y = np.array([0, 1, 1])
    threshold, _ = calibrate.select_threshold(probs, y)
    assert 0.0 < threshold < 1.0


def test_balanced_accuracy_perfect_and_worst_case():
    y = np.array([0, 0, 1, 1])
    assert calibrate.balanced_accuracy(y, np.array([0, 0, 1, 1])) == pytest.approx(1.0)
    assert calibrate.balanced_accuracy(y, np.array([1, 1, 0, 0])) == pytest.approx(0.0)


def test_asymmetric_cost_weights_false_positives_3x():
    y = np.array([0, 1])
    fp_only = calibrate.asymmetric_cost(y, np.array([1, 1]))  # idx0 misclassified: FP=1
    fn_only = calibrate.asymmetric_cost(y, np.array([0, 0]))  # idx1 misclassified: FN=1
    assert fp_only == pytest.approx(3.0)
    assert fn_only == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Metrics.
# ---------------------------------------------------------------------------


def test_brier_score_known_values():
    y = np.array([0, 1])
    assert calibrate.brier_score(np.array([0.0, 1.0]), y) == pytest.approx(0.0)
    assert calibrate.brier_score(np.array([1.0, 0.0]), y) == pytest.approx(1.0)
    assert calibrate.brier_score(np.array([0.5, 0.5]), y) == pytest.approx(0.25)


def test_expected_calibration_error_perfect_bins_is_zero():
    # Every point's probability equals its bin's empirical accuracy exactly
    # (bin_conf == bin_acc in both bins), so ECE must be exactly 0.
    probs = np.array([0.0, 0.0, 1.0, 1.0])
    y = np.array([0, 0, 1, 1])
    assert calibrate.expected_calibration_error(probs, y, n_bins=10) == pytest.approx(0.0)


def test_expected_calibration_error_known_gap():
    # One bin: predicted confidence 0.9, empirical accuracy 0.5 -> ECE = 0.4.
    probs = np.array([0.9, 0.9])
    y = np.array([1, 0])
    assert calibrate.expected_calibration_error(probs, y, n_bins=10) == pytest.approx(0.4)


# ---------------------------------------------------------------------------
# calibration.json: schema, threshold recomputation, artifacts on disk.
# ---------------------------------------------------------------------------


def test_calibration_json_matches_schema(run_calibrate):
    calibration, _ = run_calibrate
    calibrate.validate_calibration_schema(calibration)  # must not raise
    assert calibration["type"] == "platt"
    assert calibration["fitted_on"] == "val"
    assert calibration["confidence_rule"] == "asserted_class"
    assert 0.0 < calibration["threshold"] < 1.0


@pytest.mark.parametrize(
    "mutation",
    [
        lambda c: c.pop("threshold"),
        lambda c: c.__setitem__("threshold", 0.0),
        lambda c: c.__setitem__("threshold", 1.0),
        lambda c: c.__setitem__("type", "isotonic"),
        lambda c: c.__setitem__("ba_val", 1.5),
        lambda c: c.__setitem__("a", "not-a-number"),
    ],
)
def test_calibration_json_schema_rejects_violations(run_calibrate, mutation):
    calibration, _ = run_calibrate
    broken = dict(calibration)
    mutation(broken)
    with pytest.raises(ValueError):
        calibrate.validate_calibration_schema(broken)


def test_threshold_matches_argmax_ba_on_train_oof_recomputed(features_path, run_calibrate):
    calibration, _ = run_calibrate

    df = calibrate.load_all_features(features_path)
    train_df = df[df["split"] == "train"].reset_index(drop=True)
    X_train = train_df[list(FEATURE_NAMES)]
    y_train = calibrate.label_to_target(train_df["label"])
    scale_pos_weight = calibrate.compute_scale_pos_weight(y_train)

    train_oof_raw = calibrate.compute_train_oof_scores(X_train, y_train, scale_pos_weight)
    train_oof_platt = calibrate.apply_platt(train_oof_raw, calibration["a"], calibration["b"])
    recomputed_threshold, info = calibrate.select_threshold(train_oof_platt, y_train)

    assert recomputed_threshold == pytest.approx(calibration["threshold"])
    assert info["ba_train_oof"] == pytest.approx(calibration["ba_train_oof"])


def test_reliability_curve_png_written(run_calibrate):
    _, reliability_path = run_calibrate
    assert reliability_path.is_file()
    assert reliability_path.stat().st_size > 0


def test_calibrate_is_deterministic(features_path, model_path, tmp_path):
    results = []
    for i in range(2):
        calibration_path = tmp_path / f"calibration_{i}.json"
        reliability_path = tmp_path / f"reliability_{i}.png"
        rc = calibrate.main(
            [
                "--features-path", str(features_path),
                "--model-path", str(model_path),
                "--calibration-path", str(calibration_path),
                "--reliability-path", str(reliability_path),
            ]
        )
        assert rc == 0
        results.append(json.loads(calibration_path.read_text()))

    assert results[0] == results[1]
