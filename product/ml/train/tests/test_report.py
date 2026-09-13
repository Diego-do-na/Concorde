"""Tests for train/report.py (T019).

Same tier as train/tests/test_calibrate.py: synthetic in-memory feature
tables under tmp_path, no CONCORDE_DATASET_DIR, no vad-dump binary — a
real (small) model is trained and calibrated on the fixture via
train.train.main() / train.calibrate.main() so report.py is exercised
end-to-end against real artifacts of the shape T017/T018 produce.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd
import pytest

from features.extract import FEATURE_NAMES
from train import calibrate as calibrate_module
from train import report
from train import train as train_module

PASSING_VAD_REPORT = """# VAD agreement report (FR-004)

Gate
----
FR-004 VAD agreement gate: PASS
"""

EDA_REPORT_WITH_DURATION_GATE = """## Gate 3 -- duration vs label

Mean duration -- human: 149.9 s, synthetic: 146.5 s.
Point-biserial correlation (duration, is_synthetic): -0.056.
AUC of duration alone: 0.475 (discriminative power 0.525).
Duration-as-shortcut gate (discriminative power >= 0.6): **not triggered**.
"""

VAD_AGREEMENT_REPORT_WITH_PER_LABEL = (
    PASSING_VAD_REPORT
    + """
Per-label breakdown
-------------------
- human: caller_f1=0.883, agent_f1=0.872, ratio=1.01
- synthetic: caller_f1=0.861, agent_f1=0.845, ratio=1.03
"""
)


def _make_features_df(
    n_human: int,
    n_synthetic: int,
    split: str,
    rng: np.random.RandomState,
    separable: bool = False,
    durations: list | None = None,
) -> pd.DataFrame:
    """A features_vad.parquet-shaped table for one split.

    When `separable`, resp_latency_mean tracks the label almost exactly so
    AUC lands near 1.0 on both CV and val — used to exercise the §10.2
    anti-self-deception gate. `durations`, if given, overrides the default
    constant 90s (one value per row, len must equal n_human + n_synthetic)
    so error_rate_by_duration has more than one band to bucket into.
    """
    labels = ["human"] * n_human + ["synthetic"] * n_synthetic
    rows = []
    for i, label in enumerate(labels):
        target = 1.0 if label == "synthetic" else 0.0
        feats = {name: float(rng.normal(0.0, 1.0)) for name in FEATURE_NAMES}
        if separable:
            feats["resp_latency_mean"] = target * 10.0 + float(rng.normal(0.0, 1e-3))
        else:
            feats["resp_latency_mean"] = target * 1.5 + float(rng.normal(0.0, 1.0))
        duration = durations[i] if durations is not None else 90.0
        rows.append(
            {
                "anon_id": f"{split}-{i}",
                "label": label,
                "split": split,
                "duration_s": duration,
                **feats,
                "source": "vad",
            }
        )
    return pd.DataFrame.from_records(
        rows, columns=["anon_id", "label", "split", "duration_s", *FEATURE_NAMES, "source"]
    )


@pytest.fixture()
def features_path(tmp_path):
    rng = np.random.RandomState(11)
    train_durations = [70.0] * 20 + [150.0] * 30 + [200.0] * 10
    val_durations = [70.0, 90.0, 200.0, 250.0, 150.0] * 5
    train_df = _make_features_df(30, 30, "train", rng, durations=train_durations)
    val_df = _make_features_df(12, 13, "val", rng, durations=val_durations)
    df = pd.concat([train_df, val_df], ignore_index=True)
    path = tmp_path / "features_vad.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


@pytest.fixture()
def separable_features_path(tmp_path):
    """AUC near 1.0 on both CV and val, to exercise the audit-triggered path."""
    rng = np.random.RandomState(3)
    train_df = _make_features_df(30, 30, "train", rng, separable=True)
    val_df = _make_features_df(15, 15, "val", rng, separable=True)
    df = pd.concat([train_df, val_df], ignore_index=True)
    path = tmp_path / "features_vad_separable.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    return path


@pytest.fixture()
def vad_report_path(tmp_path):
    path = tmp_path / "vad_agreement_REPORT.md"
    path.write_text(PASSING_VAD_REPORT, encoding="utf-8")
    return path


@pytest.fixture()
def vad_report_path_with_per_label(tmp_path):
    path = tmp_path / "vad_agreement_REPORT_full.md"
    path.write_text(VAD_AGREEMENT_REPORT_WITH_PER_LABEL, encoding="utf-8")
    return path


@pytest.fixture()
def eda_report_path(tmp_path):
    path = tmp_path / "eda_REPORT.md"
    path.write_text(EDA_REPORT_WITH_DURATION_GATE, encoding="utf-8")
    return path


def _train_and_calibrate(features_path, vad_report_path, tmp_path, suffix=""):
    model_path = tmp_path / f"model_lgbm{suffix}.txt"
    meta_path = tmp_path / f"train_meta{suffix}.json"
    rc = train_module.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--vad-report-path", str(vad_report_path),
            "--acknowledge-audit",
        ]
    )
    assert rc == 0

    calibration_path = tmp_path / f"calibration{suffix}.json"
    reliability_path = tmp_path / f"reliability{suffix}.png"
    rc = calibrate_module.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--calibration-path", str(calibration_path),
            "--reliability-path", str(reliability_path),
        ]
    )
    assert rc == 0
    return model_path, meta_path, calibration_path


@pytest.fixture()
def trained_artifacts(features_path, vad_report_path, tmp_path):
    return _train_and_calibrate(features_path, vad_report_path, tmp_path)


@pytest.fixture()
def report_paths(tmp_path):
    return tmp_path / "REPORT.md", tmp_path / "metrics.json"


@pytest.fixture()
def run_report(features_path, trained_artifacts, report_paths, tmp_path):
    model_path, meta_path, calibration_path = trained_artifacts
    report_path, metrics_path = report_paths
    rc = report.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--calibration-path", str(calibration_path),
            "--val-check-path", str(tmp_path / "no_val_check.json"),
            "--eda-report-path", str(tmp_path / "no_eda_report.md"),
            "--vad-report-path", str(tmp_path / "no_vad_report.md"),
            "--report-path", str(report_path),
            "--metrics-path", str(metrics_path),
        ]
    )
    assert rc == 0
    return report_path.read_text(encoding="utf-8"), json.loads(metrics_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Data loading.
# ---------------------------------------------------------------------------


def test_load_val_split_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        report.load_val_split(tmp_path / "nope.parquet")


def test_load_val_split_requires_val_split(tmp_path):
    rng = np.random.RandomState(0)
    df = _make_features_df(10, 10, "train", rng)
    path = tmp_path / "train_only.parquet"
    df.to_parquet(path, engine="pyarrow", index=False)
    with pytest.raises(RuntimeError, match="no 'val' split"):
        report.load_val_split(path)


def test_load_val_split_returns_only_val_rows(features_path):
    val_df = report.load_val_split(features_path)
    assert set(val_df["split"].unique()) == {"val"}
    assert len(val_df) == 25


# ---------------------------------------------------------------------------
# Metrics primitives.
# ---------------------------------------------------------------------------


def test_confusion_matrix_matches_counts():
    y = np.array([1, 1, 0, 0, 1])
    pred = np.array([1, 0, 0, 1, 1])
    cm = report.confusion_matrix(y, pred)
    assert cm == {"tp": 2, "fn": 1, "fp": 1, "tn": 1}


def test_tpr_tnr_known_values():
    y = np.array([1, 1, 0, 0])
    pred = np.array([1, 0, 0, 1])
    tpr, tnr = report.tpr_tnr(y, pred)
    assert tpr == pytest.approx(0.5)
    assert tnr == pytest.approx(0.5)


def test_compute_eer_perfect_separation_is_zero():
    y = np.array([0, 0, 0, 1, 1, 1])
    probs = np.array([0.1, 0.2, 0.3, 0.7, 0.8, 0.9])
    assert report.compute_eer(y, probs) == pytest.approx(0.0)


def test_compute_eer_single_class_returns_half():
    y = np.array([1, 1, 1])
    probs = np.array([0.2, 0.5, 0.9])
    assert report.compute_eer(y, probs) == pytest.approx(0.5)


def test_reliability_table_bins_cover_all_rows():
    probs = np.array([0.05, 0.15, 0.95, 0.99])
    y = np.array([0, 0, 1, 1])
    table = report.reliability_table(probs, y, n_bins=10)
    assert len(table) == 10
    assert sum(row["count"] for row in table) == len(y)


def test_error_rate_by_duration_bands_known_values():
    y = np.array([1, 1, 0, 0])
    pred = np.array([1, 0, 0, 0])  # index1 (dur 100) misclassified: FN
    duration = np.array([70.0, 100.0, 150.0, 190.0])
    rows = report.error_rate_by_duration(y, pred, duration)
    by_band = {(r["band_lo"], r["band_hi"]): r for r in rows}
    assert by_band[(60.0, 120.0)]["n"] == 2
    assert by_band[(60.0, 120.0)]["errors"] == 1
    assert by_band[(60.0, 120.0)]["error_rate"] == pytest.approx(0.5)
    assert by_band[(120.0, 180.0)]["n"] == 1
    assert by_band[(120.0, 180.0)]["errors"] == 0
    assert by_band[(180.0, 280.0)]["n"] == 1


def test_top_feature_importances_orders_desc_and_limits_to_k():
    meta = {
        "feature_names": list(FEATURE_NAMES),
        "feature_importance": list(range(len(FEATURE_NAMES))),  # last name has highest importance
        "direction_sign": [1] * len(FEATURE_NAMES),
    }
    top = report.top_feature_importances(meta, k=3)
    assert len(top) == 3
    assert [row["importance"] for row in top] == sorted(
        (row["importance"] for row in top), reverse=True
    )
    assert top[0]["name"] == FEATURE_NAMES[-1]


def test_top_feature_importances_direction_sign_mapping():
    meta = {
        "feature_names": ["a", "b"],
        "feature_importance": [5.0, 1.0],
        "direction_sign": [1, -1],
    }
    top = report.top_feature_importances(meta, k=2)
    assert top[0] == {"name": "a", "importance": 5.0, "direction": "synthetic"}
    assert top[1] == {"name": "b", "importance": 1.0, "direction": "human"}


# ---------------------------------------------------------------------------
# Anti-self-deception audit.
# ---------------------------------------------------------------------------


def test_audit_not_triggered_below_threshold():
    meta = {"feature_names": ["a"], "feature_importance": [1.0], "direction_sign": [1]}
    triggered, evidence = report.anti_self_deception_audit(0.90, meta)
    assert triggered is False
    assert evidence is None


def test_audit_triggered_gathers_evidence(eda_report_path, vad_report_path_with_per_label):
    meta = {
        "feature_names": list(FEATURE_NAMES),
        "feature_importance": list(range(len(FEATURE_NAMES))),
        "direction_sign": [1] * len(FEATURE_NAMES),
    }
    triggered, evidence = report.anti_self_deception_audit(
        0.99, meta, eda_report_path=eda_report_path, vad_report_path=vad_report_path_with_per_label
    )
    assert triggered is True
    assert "AUC of duration alone: 0.475" in evidence["duration_correlation"]
    assert "human: caller_f1=0.883" in evidence["vad_label_agreement"]
    assert len(evidence["top_importances"]) == 5


def test_audit_triggered_missing_report_files_degrades_gracefully(tmp_path):
    meta = {"feature_names": ["a"], "feature_importance": [1.0], "direction_sign": [1]}
    triggered, evidence = report.anti_self_deception_audit(
        0.99, meta, eda_report_path=tmp_path / "missing.md", vad_report_path=tmp_path / "missing2.md"
    )
    assert triggered is True
    assert evidence["duration_correlation"] is None
    assert evidence["vad_label_agreement"] is None


# ---------------------------------------------------------------------------
# Cross-check against Altur's own scorer (T022).
# ---------------------------------------------------------------------------


def test_load_cross_check_missing_file(tmp_path):
    result = report.load_cross_check(tmp_path / "nope.json", 0.85)
    assert result["available"] is False


def test_load_cross_check_within_tolerance(tmp_path):
    path = tmp_path / "val_check.json"
    path.write_text(json.dumps({"balanced_accuracy": 0.851}), encoding="utf-8")
    result = report.load_cross_check(path, 0.845)
    assert result["available"] is True
    assert result["within_tolerance"] is True


def test_load_cross_check_outside_tolerance(tmp_path):
    path = tmp_path / "val_check.json"
    path.write_text(json.dumps({"balanced_accuracy": 0.70}), encoding="utf-8")
    result = report.load_cross_check(path, 0.845)
    assert result["available"] is True
    assert result["within_tolerance"] is False


# ---------------------------------------------------------------------------
# End-to-end: REPORT.md + metrics.json.
# ---------------------------------------------------------------------------

REQUIRED_SECTION_HEADERS = (
    "## Balanced accuracy (val, shipped threshold)",
    "## ROC-AUC and Brier (val)",
    "## EER (val)",
    "## Accuracy (val)",
    "## ECE and reliability (val)",
    "## Confusion matrix (val)",
    "## Error rate by duration band (val)",
    "## Top-10 feature importances",
    "## CV-vs-val gap",
    "## Anti-self-deception audit (§10.2)",
)


def test_report_contains_every_required_section_header(run_report):
    report_text, _ = run_report
    for header in REQUIRED_SECTION_HEADERS:
        assert header in report_text, f"missing section header: {header!r}"


def test_metrics_json_has_all_required_keys(run_report):
    _, metrics = run_report
    for key in report.REQUIRED_METRICS_KEYS:
        assert key in metrics, f"metrics.json missing key: {key!r}"
    assert "balanced_accuracy" in metrics


def test_report_numbers_match_metrics_json(run_report):
    report_text, metrics = run_report

    ba_match = re.search(r"\| Balanced accuracy \| ([\d.]+) \|", report_text)
    assert ba_match is not None
    assert float(ba_match.group(1)) == pytest.approx(metrics["balanced_accuracy"], abs=1e-3)

    auc_match = re.search(r"\| ROC-AUC \| ([\d.]+) \|", report_text)
    assert auc_match is not None
    assert float(auc_match.group(1)) == pytest.approx(metrics["roc_auc"], abs=1e-3)

    brier_match = re.search(r"\| Brier \(after calibration\) \| ([\d.]+) \|", report_text)
    assert brier_match is not None
    assert float(brier_match.group(1)) == pytest.approx(metrics["brier"], abs=1e-3)

    eer_match = re.search(r"Equal error rate.*\*\*([\d.]+)\*\*", report_text)
    assert eer_match is not None
    assert float(eer_match.group(1)) == pytest.approx(metrics["eer"], abs=1e-3)

    acc_match = re.search(r"Unweighted accuracy.*\*\*([\d.]+)\*\*", report_text)
    assert acc_match is not None
    assert float(acc_match.group(1)) == pytest.approx(metrics["accuracy"], abs=1e-3)

    ece_match = re.search(r"Expected calibration error.*\*\*([\d.]+)\*\*", report_text)
    assert ece_match is not None
    assert float(ece_match.group(1)) == pytest.approx(metrics["ece"], abs=1e-3)


def test_confusion_matrix_in_report_matches_metrics(run_report):
    report_text, metrics = run_report
    cm = metrics["confusion_matrix"]
    assert f"TP={cm['tp']}" in report_text
    assert f"FN={cm['fn']}" in report_text
    assert f"FP={cm['fp']}" in report_text
    assert f"TN={cm['tn']}" in report_text


def test_report_regenerates_identically(features_path, trained_artifacts, tmp_path):
    model_path, meta_path, calibration_path = trained_artifacts
    outputs = []
    for i in range(2):
        report_path = tmp_path / f"REPORT_{i}.md"
        metrics_path = tmp_path / f"metrics_{i}.json"
        rc = report.main(
            [
                "--features-path", str(features_path),
                "--model-path", str(model_path),
                "--meta-path", str(meta_path),
                "--calibration-path", str(calibration_path),
                "--val-check-path", str(tmp_path / "no_val_check.json"),
                "--eda-report-path", str(tmp_path / "no_eda_report.md"),
                "--vad-report-path", str(tmp_path / "no_vad_report.md"),
                "--report-path", str(report_path),
                "--metrics-path", str(metrics_path),
            ]
        )
        assert rc == 0
        outputs.append((report_path.read_text(encoding="utf-8"), json.loads(metrics_path.read_text(encoding="utf-8"))))

    assert outputs[0][0] == outputs[1][0]
    assert outputs[0][1] == outputs[1][1]


def test_report_with_val_check_present_and_within_tolerance(features_path, trained_artifacts, tmp_path):
    model_path, meta_path, calibration_path = trained_artifacts

    # First pass, no val_check, to learn this fixture's actual balanced accuracy.
    report_path = tmp_path / "REPORT_probe.md"
    metrics_path = tmp_path / "metrics_probe.json"
    rc = report.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--calibration-path", str(calibration_path),
            "--val-check-path", str(tmp_path / "no_val_check.json"),
            "--eda-report-path", str(tmp_path / "no_eda_report.md"),
            "--vad-report-path", str(tmp_path / "no_vad_report.md"),
            "--report-path", str(report_path),
            "--metrics-path", str(metrics_path),
        ]
    )
    assert rc == 0
    ba = json.loads(metrics_path.read_text(encoding="utf-8"))["balanced_accuracy"]

    val_check_path = tmp_path / "val_check.json"
    val_check_path.write_text(json.dumps({"balanced_accuracy": ba}), encoding="utf-8")

    report_path2 = tmp_path / "REPORT_with_check.md"
    metrics_path2 = tmp_path / "metrics_with_check.json"
    rc = report.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--calibration-path", str(calibration_path),
            "--val-check-path", str(val_check_path),
            "--eda-report-path", str(tmp_path / "no_eda_report.md"),
            "--vad-report-path", str(tmp_path / "no_vad_report.md"),
            "--report-path", str(report_path2),
            "--metrics-path", str(metrics_path2),
        ]
    )
    assert rc == 0
    report_text2 = report_path2.read_text(encoding="utf-8")
    metrics2 = json.loads(metrics_path2.read_text(encoding="utf-8"))
    assert metrics2["cross_check"]["available"] is True
    assert metrics2["cross_check"]["within_tolerance"] is True
    assert "PASS" in report_text2.split("## Cross-check against Altur's scorer (T022)")[1]


def test_audit_triggered_end_to_end_with_separable_fixture(separable_features_path, vad_report_path, tmp_path):
    model_path, meta_path, calibration_path = _train_and_calibrate(
        separable_features_path, vad_report_path, tmp_path, suffix="_sep"
    )
    report_path = tmp_path / "REPORT_sep.md"
    metrics_path = tmp_path / "metrics_sep.json"
    rc = report.main(
        [
            "--features-path", str(separable_features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--calibration-path", str(calibration_path),
            "--val-check-path", str(tmp_path / "no_val_check.json"),
            "--eda-report-path", str(tmp_path / "no_eda_report.md"),
            "--vad-report-path", str(tmp_path / "no_vad_report.md"),
            "--report-path", str(report_path),
            "--metrics-path", str(metrics_path),
        ]
    )
    assert rc == 0
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    if metrics["roc_auc"] >= report.SELF_DECEPTION_AUC_THRESHOLD:
        assert metrics["anti_self_deception_triggered"] is True
        assert metrics["anti_self_deception_evidence"] is not None
        report_text = report_path.read_text(encoding="utf-8")
        assert "suspicion threshold" in report_text
