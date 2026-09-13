"""Tests for export/export_onnx.py (T021).

Runs against a small synthetic LightGBM booster + calibration/train_meta
sidecars written to tmp_path — mirrors train/tests/test_train.py's and
train/tests/test_calibrate.py's tier: no real product/ml/data/* files
(gitignored, built from the real ~300-call dataset by T016-T018) required.

The parity check below uses 353 synthetic rows specifically because that
is the real dataset's row count (T021's own VERIFY step) — this test
proves the export path is loss-free at that scale; it is not a substitute
for re-running it against the real model_lgbm.txt once T016-T018 have
produced one locally.
"""

from __future__ import annotations

import json

import lightgbm as lgb
import numpy as np
import onnxruntime as ort
import pytest

from export import export_onnx
from features.extract import FEATURE_NAMES

N_FEATURES = len(FEATURE_NAMES)
N_ROWS = 353  # matches the real dataset's row count (§9 / T016)


def _train_synthetic_booster(seed: int = 20260912) -> lgb.Booster:
    rng = np.random.RandomState(seed)
    X = rng.normal(size=(200, N_FEATURES))
    y = (X[:, 0] + rng.normal(scale=0.1, size=200) > 0).astype(int)
    ds = lgb.Dataset(X, label=y, feature_name=list(FEATURE_NAMES))
    params = {"objective": "binary", "verbosity": -1, "seed": seed, "num_leaves": 7}
    return lgb.train(params, ds, num_boost_round=20)


@pytest.fixture
def pipeline_dir(tmp_path):
    """Writes model_lgbm.txt, calibration.json, train_meta.json to tmp_path,
    matching what T017/T018 would have left in ml/data/."""
    booster = _train_synthetic_booster()
    model_path = tmp_path / "model_lgbm.txt"
    booster.save_model(str(model_path))

    calibration = {
        "type": "platt",
        "a": 1.7,
        "b": -0.3,
        "threshold": 0.42,
        "threshold_rule": "max balanced accuracy on train OOF, ties -> 3FP+1FN",
        "fitted_on": "val",
        "ba_train_oof": 0.81,
        "ba_val": 0.79,
        "ece_val": 0.03,
        "brier_val_before": 0.19,
        "brier_val_after": 0.15,
        "confidence_rule": "asserted_class",
    }
    (tmp_path / "calibration.json").write_text(json.dumps(calibration), encoding="utf-8")

    rng = np.random.RandomState(1)
    train_meta = {
        "feature_contract": "fc-1",
        "git_sha": "deadbeef",
        "seed": 20260912,
        "manifest_sha256": "abc123",
        "feature_names": list(FEATURE_NAMES),
        "feature_importance": [float(v) for v in rng.uniform(size=N_FEATURES)],
        "train_feature_means": [float(v) for v in rng.normal(size=N_FEATURES)],
        "train_feature_stds": [float(v) for v in np.abs(rng.normal(size=N_FEATURES)) + 0.1],
        "direction_sign": [1 if v > 0 else -1 for v in rng.normal(size=N_FEATURES)],
    }
    (tmp_path / "train_meta.json").write_text(json.dumps(train_meta), encoding="utf-8")

    return tmp_path, booster, calibration, train_meta


def test_export_writes_onnx_and_meta(pipeline_dir):
    tmp_path, booster, calibration, train_meta = pipeline_dir
    changelog = tmp_path / "CHANGELOG.md"

    meta = export_onnx.export(
        model_txt_path=tmp_path / "model_lgbm.txt",
        calibration_path=tmp_path / "calibration.json",
        train_meta_path=tmp_path / "train_meta.json",
        onnx_path=tmp_path / "model.onnx",
        onnx_meta_path=tmp_path / "model.onnx.meta.json",
        changelog_path=changelog,
    )

    assert (tmp_path / "model.onnx").is_file()
    assert (tmp_path / "model.onnx.meta.json").is_file()
    assert meta["model_version"] == "concorde-b-1"
    assert changelog.is_file()
    assert "concorde-b-1" in changelog.read_text(encoding="utf-8")


def test_meta_matches_documented_schema(pipeline_dir):
    tmp_path, *_ = pipeline_dir
    meta = export_onnx.export(
        model_txt_path=tmp_path / "model_lgbm.txt",
        calibration_path=tmp_path / "calibration.json",
        train_meta_path=tmp_path / "train_meta.json",
        onnx_path=tmp_path / "model.onnx",
        onnx_meta_path=tmp_path / "model.onnx.meta.json",
        changelog_path=tmp_path / "CHANGELOG.md",
    )
    export_onnx.validate_meta_schema(meta)  # raises on violation
    with (tmp_path / "model.onnx.meta.json").open() as f:
        on_disk = json.load(f)
    assert on_disk == meta


def test_onnx_matches_lightgbm_within_tolerance(pipeline_dir):
    tmp_path, booster, _, _ = pipeline_dir
    export_onnx.export(
        model_txt_path=tmp_path / "model_lgbm.txt",
        calibration_path=tmp_path / "calibration.json",
        train_meta_path=tmp_path / "train_meta.json",
        onnx_path=tmp_path / "model.onnx",
        onnx_meta_path=tmp_path / "model.onnx.meta.json",
        changelog_path=tmp_path / "CHANGELOG.md",
    )

    rng = np.random.RandomState(7)
    X = rng.normal(size=(N_ROWS, N_FEATURES)).astype(np.float32)

    expected = booster.predict(X.astype(np.float64))  # LightGBM's own P(class=1)

    sess = ort.InferenceSession(str(tmp_path / "model.onnx"))
    (input_name,) = [i.name for i in sess.get_inputs()]
    (output_name,) = [o.name for o in sess.get_outputs()]
    actual = sess.run([output_name], {input_name: X})[0]

    assert actual.shape == (N_ROWS,)
    np.testing.assert_allclose(actual, expected, atol=1e-5, rtol=1e-5)


def test_onnx_graph_exposes_only_probability_output(pipeline_dir):
    tmp_path, *_ = pipeline_dir
    export_onnx.export(
        model_txt_path=tmp_path / "model_lgbm.txt",
        calibration_path=tmp_path / "calibration.json",
        train_meta_path=tmp_path / "train_meta.json",
        onnx_path=tmp_path / "model.onnx",
        onnx_meta_path=tmp_path / "model.onnx.meta.json",
        changelog_path=tmp_path / "CHANGELOG.md",
    )
    sess = ort.InferenceSession(str(tmp_path / "model.onnx"))
    outputs = sess.get_outputs()
    assert len(outputs) == 1, f"expected exactly one output, got {[o.name for o in outputs]}"
    assert outputs[0].name != "label"


def test_next_model_version_bumps_from_changelog(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    assert export_onnx.next_model_version(changelog) == "concorde-b-1"

    changelog.write_text("- concorde-b-1: ...\n- concorde-b-3: ...\n", encoding="utf-8")
    assert export_onnx.next_model_version(changelog) == "concorde-b-4"


def test_export_rejects_feature_name_mismatch(pipeline_dir):
    tmp_path, *_ = pipeline_dir
    train_meta = json.loads((tmp_path / "train_meta.json").read_text(encoding="utf-8"))
    train_meta["feature_names"] = list(reversed(train_meta["feature_names"]))
    (tmp_path / "train_meta.json").write_text(json.dumps(train_meta), encoding="utf-8")

    with pytest.raises(ValueError):
        export_onnx.export(
            model_txt_path=tmp_path / "model_lgbm.txt",
            calibration_path=tmp_path / "calibration.json",
            train_meta_path=tmp_path / "train_meta.json",
            onnx_path=tmp_path / "model.onnx",
            onnx_meta_path=tmp_path / "model.onnx.meta.json",
            changelog_path=tmp_path / "CHANGELOG.md",
        )


def test_export_missing_inputs_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        export_onnx.export(
            model_txt_path=tmp_path / "model_lgbm.txt",
            calibration_path=tmp_path / "calibration.json",
            train_meta_path=tmp_path / "train_meta.json",
            onnx_path=tmp_path / "model.onnx",
            onnx_meta_path=tmp_path / "model.onnx.meta.json",
            changelog_path=tmp_path / "CHANGELOG.md",
        )
