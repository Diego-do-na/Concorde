"""Tests for train/train.py (T017).

All tests run against small synthetic in-memory feature tables written to
tmp_path — no CONCORDE_DATASET_DIR, no vad-dump binary, no LightGBM training
on the real ~300-call dataset required. This mirrors train/tests/test_build_dataset.py's
unit-test tier.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from features.extract import FEATURE_NAMES
from train import train

PASSING_REPORT = """# VAD agreement report (FR-004)

Gate
----
FR-004 VAD agreement gate: PASS
"""

FAILING_REPORT = """# VAD agreement report (FR-004)

Gate
----
FR-004 VAD agreement gate: FAIL
"""


def _make_features_df(
    n_human: int, n_synthetic: int, split: str, rng: np.random.RandomState, separable: bool = False
) -> pd.DataFrame:
    """A features_vad.parquet-shaped table for one split.

    When `separable`, `resp_latency_mean` (F-01) is set to (almost) exactly
    the label so CV AUC lands near 1.0 — used to exercise the §10.2
    anti-self-deception gate.
    """
    labels = ["human"] * n_human + ["synthetic"] * n_synthetic
    n = len(labels)
    rows = []
    for i, label in enumerate(labels):
        target = 1.0 if label == "synthetic" else 0.0
        feats = {name: float(rng.normal(0.0, 1.0)) for name in FEATURE_NAMES}
        if separable:
            feats["resp_latency_mean"] = target * 10.0 + float(rng.normal(0.0, 1e-3))
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


def _write_features_parquet(path, rng: np.random.RandomState, separable: bool = False) -> None:
    train_df = _make_features_df(40, 60, "train", rng, separable=separable)
    val_df = _make_features_df(15, 15, "val", rng, separable=separable)
    df = pd.concat([train_df, val_df], ignore_index=True)
    df.to_parquet(path, engine="pyarrow", index=False)


@pytest.fixture()
def features_path(tmp_path):
    rng = np.random.RandomState(0)
    path = tmp_path / "features_vad.parquet"
    _write_features_parquet(path, rng)
    return path


@pytest.fixture()
def separable_features_path(tmp_path):
    rng = np.random.RandomState(0)
    path = tmp_path / "features_vad_separable.parquet"
    _write_features_parquet(path, rng, separable=True)
    return path


@pytest.fixture()
def passing_report_path(tmp_path):
    path = tmp_path / "REPORT.md"
    path.write_text(PASSING_REPORT, encoding="utf-8")
    return path


@pytest.fixture()
def failing_report_path(tmp_path):
    path = tmp_path / "REPORT.md"
    path.write_text(FAILING_REPORT, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Gate check (T012 dependency)
# ---------------------------------------------------------------------------


def test_assert_vad_gate_passed_ok(passing_report_path):
    train.assert_vad_gate_passed(passing_report_path)  # must not raise


def test_assert_vad_gate_passed_missing_file(tmp_path):
    with pytest.raises(RuntimeError, match="not found"):
        train.assert_vad_gate_passed(tmp_path / "missing.md")


def test_assert_vad_gate_passed_fail_text(failing_report_path):
    with pytest.raises(RuntimeError, match="did not read PASS"):
        train.assert_vad_gate_passed(failing_report_path)


# ---------------------------------------------------------------------------
# val is intocable (ADR-012)
# ---------------------------------------------------------------------------


def test_load_features_rejects_val_split(features_path):
    with pytest.raises(RuntimeError, match="ADR-012"):
        train.load_features(features_path, split="val")


def test_load_features_rejects_arbitrary_split(features_path):
    with pytest.raises(RuntimeError, match="ADR-012"):
        train.load_features(features_path, split="bogus")


def test_load_features_returns_train_rows_only(features_path):
    df = train.load_features(features_path, split="train")
    assert (df["split"] == "train").all()
    assert len(df) == 100  # 40 human + 60 synthetic, matching build fixture


def test_main_never_requests_val_split(monkeypatch, features_path, passing_report_path, tmp_path):
    """Monkeypatches the feature loader to fail loudly if ever asked for
    split='val'; main() must complete successfully, proving it only ever
    asks for split='train'."""
    real_load_features = train.load_features
    calls = []

    def spy(path, split="train"):
        calls.append(split)
        if split == "val":
            raise AssertionError("train.main() must never load split='val' (ADR-012)")
        return real_load_features(path, split=split)

    monkeypatch.setattr(train, "load_features", spy)

    rc = train.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(tmp_path / "model.txt"),
            "--meta-path", str(tmp_path / "meta.json"),
            "--vad-report-path", str(passing_report_path),
        ]
    )
    assert rc == 0
    assert calls == ["train"]


# ---------------------------------------------------------------------------
# Label mapping / class weight
# ---------------------------------------------------------------------------


def test_label_to_target():
    y = train.label_to_target(["human", "synthetic", "synthetic"])
    assert list(y) == [0, 1, 1]


def test_compute_scale_pos_weight_matches_train_split_balance():
    # 113 human / 169 synthetic — the actual train-split balance (EDA gate 1).
    y = np.array([0] * 113 + [1] * 169)
    weight = train.compute_scale_pos_weight(y)
    assert weight == pytest.approx(113 / 169)


def test_compute_scale_pos_weight_no_positives_is_neutral():
    y = np.array([0, 0, 0])
    assert train.compute_scale_pos_weight(y) == 1.0


# ---------------------------------------------------------------------------
# Full pipeline: determinism, CV AUC reporting, meta schema.
# ---------------------------------------------------------------------------


def test_training_is_deterministic(features_path, passing_report_path, tmp_path):
    model_a = tmp_path / "a" / "model.txt"
    meta_a = tmp_path / "a" / "meta.json"
    model_b = tmp_path / "b" / "model.txt"
    meta_b = tmp_path / "b" / "meta.json"

    for model_path, meta_path in ((model_a, meta_a), (model_b, meta_b)):
        rc = train.main(
            [
                "--features-path", str(features_path),
                "--model-path", str(model_path),
                "--meta-path", str(meta_path),
                "--vad-report-path", str(passing_report_path),
            ]
        )
        assert rc == 0

    assert model_a.read_text() == model_b.read_text()

    meta_a_json = json.loads(meta_a.read_text())
    meta_b_json = json.loads(meta_b.read_text())
    assert meta_a_json == meta_b_json


def test_meta_reports_cv_auc_and_full_schema(features_path, passing_report_path, tmp_path):
    model_path = tmp_path / "model.txt"
    meta_path = tmp_path / "meta.json"
    rc = train.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--vad-report-path", str(passing_report_path),
        ]
    )
    assert rc == 0
    assert model_path.is_file()

    meta = json.loads(meta_path.read_text())
    assert meta["feature_contract"] == "fc-1"
    assert isinstance(meta["cv_auc_mean"], float)
    assert 0.0 <= meta["cv_auc_mean"] <= 1.0
    assert isinstance(meta["cv_auc_std"], float)
    assert meta["seed"] == train.SEED
    assert meta["feature_names"] == list(FEATURE_NAMES)
    for key in ("feature_importance", "train_feature_means", "train_feature_stds", "direction_sign"):
        assert len(meta[key]) == len(FEATURE_NAMES), key
    assert all(sign in (1, -1) for sign in meta["direction_sign"])
    assert meta["params"]["max_depth"] <= 4
    assert meta["params"]["num_leaves"] <= 15
    assert meta["params"]["min_data_in_leaf"] >= 15
    assert meta["params"]["feature_fraction"] == 0.8
    assert meta["params"]["learning_rate"] == 0.05
    assert "ADR-012 deviation" in meta["cv_protocol"]["note"]


def test_run_cv_uses_five_folds_times_three_seeds(features_path):
    df = train.load_features(features_path, split="train")
    X = df[list(FEATURE_NAMES)]
    y = train.label_to_target(df["label"])
    result = train.run_cv(X, y, train.compute_scale_pos_weight(y))
    assert len(result["fold_aucs"]) == train.N_FOLDS * len(train.CV_SEEDS)
    assert result["best_num_boost_round"] >= 1


# ---------------------------------------------------------------------------
# Anti-self-deception gate, §10.2.
# ---------------------------------------------------------------------------


def test_audit_gate_blocks_high_auc_without_acknowledgement(
    separable_features_path, passing_report_path, tmp_path
):
    model_path = tmp_path / "model.txt"
    meta_path = tmp_path / "meta.json"
    rc = train.main(
        [
            "--features-path", str(separable_features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--vad-report-path", str(passing_report_path),
        ]
    )
    assert rc == 1
    assert not model_path.exists()
    assert not meta_path.exists()


def test_audit_gate_allows_high_auc_with_acknowledgement(
    separable_features_path, passing_report_path, tmp_path
):
    model_path = tmp_path / "model.txt"
    meta_path = tmp_path / "meta.json"
    rc = train.main(
        [
            "--features-path", str(separable_features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--vad-report-path", str(passing_report_path),
            "--acknowledge-audit",
        ]
    )
    assert rc == 0
    assert model_path.is_file()
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text())
    assert meta["cv_auc_mean"] > train.CV_AUC_AUDIT_THRESHOLD
