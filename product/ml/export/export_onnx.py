#!/usr/bin/env python3
"""Exports the shipped LightGBM model to ONNX with a versioned meta sidecar
(T021, ADR-004, NFR-009).

This is the last step of the offline pipeline: T016 built the feature
table, T017 trained `model_lgbm.txt` on `train`, T018 fit Platt calibration
and picked a threshold on `val`/train-OOF. This script converts the
booster to ONNX (the only model format the Rust service loads, ADR-002)
and writes the `<model>.onnx.meta.json` sidecar the API's `Model::load`
validates against `features::FC1_NAMES` before it will serve a single
request (FR-006).

Reads:
    ml/data/model_lgbm.txt      T017's booster (train-only)
    ml/data/calibration.json    T018's Platt fit + threshold (fit on val)
    ml/data/train_meta.json     T017's reproducibility record (feature
                                 means/stds/importances/direction, git_sha,
                                 seed, manifest_sha256)

Writes:
    artifacts/model.onnx
    artifacts/model.onnx.meta.json
    artifacts/CHANGELOG.md      one appended line per exported version

`model_version` is "concorde-b-<n>", bumped from the highest <n> already
present in CHANGELOG.md (starts at 1 if the file is empty/missing).

Usage:
    cd product/ml
    python -m export.export_onnx
    python -m export.export_onnx --acknowledge-audit  # forwarded, unused here;
        kept for symmetry with train.py's flag is NOT accepted — see --help
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import lightgbm as lgb
import numpy as np
import onnx
from onnx import helper, numpy_helper, TensorProto
from onnxmltools import convert_lightgbm
from onnxmltools.convert.common.data_types import FloatTensorType

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES  # noqa: E402

ML_DIR = Path(__file__).resolve().parents[1]
PRODUCT_DIR = ML_DIR.parent
DATA_DIR = ML_DIR / "data"
ARTIFACTS_DIR = PRODUCT_DIR / "artifacts"

MODEL_TXT_PATH = DATA_DIR / "model_lgbm.txt"
CALIBRATION_JSON_PATH = DATA_DIR / "calibration.json"
TRAIN_META_JSON_PATH = DATA_DIR / "train_meta.json"

ONNX_PATH = ARTIFACTS_DIR / "model.onnx"
ONNX_META_PATH = ARTIFACTS_DIR / "model.onnx.meta.json"
CHANGELOG_PATH = ARTIFACTS_DIR / "CHANGELOG.md"

N_FEATURES = len(FEATURE_NAMES)

_VERSION_RE = re.compile(r"concorde-b-(\d+)\b")


def next_model_version(changelog_path: Path = CHANGELOG_PATH) -> str:
    """"concorde-b-<n>", one past the highest <n> already in CHANGELOG.md."""
    if not changelog_path.is_file():
        return "concorde-b-1"
    highest = 0
    for match in _VERSION_RE.finditer(changelog_path.read_text(encoding="utf-8")):
        highest = max(highest, int(match.group(1)))
    return f"concorde-b-{highest + 1}"


def load_json(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found — run the pipeline in order: "
            "train/build_dataset.py (T016) -> train/train.py (T017) -> "
            "train/calibrate.py (T018) before export.export_onnx (T021)."
        )
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def convert_to_onnx(booster: lgb.Booster, n_features: int = N_FEATURES) -> onnx.ModelProto:
    """LightGBM booster -> ONNX graph with a single output: P(class=1).

    onnxmltools' default LightGBM conversion exposes two outputs — an
    integer `label` and a `probabilities` tensor of shape (N, 2) (or a
    ZipMap dict, with zipmap left on). Neither is what the API needs: it
    calls `try_extract_array::<f32>()` then reshapes to 1-D, so the graph
    must expose exactly one float output of shape (N,), the probability of
    the synthetic class (label 1, ADR-006). `zipmap=False` gives a plain
    tensor; a Gather+Squeeze pair then slices out column 1 and drops the
    `label` output entirely so nothing downstream can accidentally read a
    hard class label where a probability is expected.
    """
    initial_types = [("input", FloatTensorType([None, n_features]))]
    onnx_model = convert_lightgbm(
        booster, initial_types=initial_types, zipmap=False, target_opset=15
    )
    graph = onnx_model.graph

    output_names = {o.name for o in graph.output}
    if "probabilities" not in output_names:
        raise RuntimeError(
            f"expected onnxmltools to emit a 'probabilities' output, got {sorted(output_names)} — "
            "the converted graph exposes labels, not probabilities; refusing to export."
        )
    probabilities_name = "probabilities"

    # Gather column 1 (P(class=1)) along axis 1, then squeeze it back to (N,).
    index_1 = numpy_helper.from_array(np.array([1], dtype=np.int64), name="class1_index")
    graph.initializer.append(index_1)

    gather_node = helper.make_node(
        "Gather",
        inputs=[probabilities_name, "class1_index"],
        outputs=["p_synthetic_col"],
        axis=1,
        name="gather_p_synthetic",
    )
    # The converted graph's opset (ai.onnx domain) predates opset-13's
    # Squeeze, where `axes` moved from an attribute to an optional input —
    # onnxmltools pins an older opset, so `axes` must stay an attribute here.
    squeeze_node = helper.make_node(
        "Squeeze",
        inputs=["p_synthetic_col"],
        outputs=["p_synthetic"],
        name="squeeze_p_synthetic",
        axes=[1],
    )
    graph.node.extend([gather_node, squeeze_node])

    # Replace the graph outputs with the single scalar-per-row probability;
    # drop `label` and the raw `probabilities` tensor so /detect's serving
    # path can never accidentally consume a hard label (ADR-013).
    new_output = helper.make_tensor_value_info("p_synthetic", TensorProto.FLOAT, [None])
    del graph.output[:]
    graph.output.append(new_output)

    onnx.checker.check_model(onnx_model)
    return onnx_model


def build_meta(model_version: str, calibration: Dict[str, Any], train_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Assembles the `<model>.onnx.meta.json` sidecar per product/api/README.md's
    "Model sidecar schema (T021)" section — the API's `Model::load` deserializes
    exactly this shape and aborts startup on any mismatch (FR-006)."""
    feature_names = train_meta["feature_names"]
    if list(feature_names) != list(FEATURE_NAMES):
        raise ValueError(
            "train_meta.json's feature_names do not match features.extract.FEATURE_NAMES — "
            "the model was trained against a different fc-1 ordering than the one this "
            "checkout's extractor produces; refusing to export (§9 freezing rule)."
        )
    return {
        "model_version": model_version,
        "feature_contract": contract.CONTRACT_ID,
        "calibration": {
            "type": calibration["type"],
            "a": float(calibration["a"]),
            "b": float(calibration["b"]),
        },
        "threshold": float(calibration["threshold"]),
        "git_sha": train_meta.get("git_sha"),
        "seed": train_meta.get("seed"),
        "manifest_sha256": train_meta.get("manifest_sha256"),
        "feature_names": list(feature_names),
        "train_feature_means": [float(x) for x in train_meta["train_feature_means"]],
        "train_feature_stds": [float(x) for x in train_meta["train_feature_stds"]],
        "feature_importance": [float(x) for x in train_meta["feature_importance"]],
        "direction_sign": [int(x) for x in train_meta["direction_sign"]],
    }


_META_SCHEMA_REQUIRED: Dict[str, type] = {
    "model_version": str,
    "feature_contract": str,
    "calibration": dict,
    "threshold": float,
    "feature_names": list,
    "train_feature_means": list,
    "train_feature_stds": list,
    "feature_importance": list,
    "direction_sign": list,
}


def validate_meta_schema(meta: Dict[str, Any], n_features: int = N_FEATURES) -> None:
    """Raises ValueError on the first violation of the sidecar schema."""
    for key, typ in _META_SCHEMA_REQUIRED.items():
        if key not in meta:
            raise ValueError(f"meta missing required key {key!r}")
        if typ is float:
            if not isinstance(meta[key], (int, float)):
                raise ValueError(f"meta[{key!r}] must be numeric, got {type(meta[key])}")
        elif not isinstance(meta[key], typ):
            raise ValueError(f"meta[{key!r}] must be {typ}, got {type(meta[key])}")
    for key in ("git_sha", "manifest_sha256"):
        if key in meta and meta[key] is not None and not isinstance(meta[key], str):
            raise ValueError(f"meta[{key!r}] must be str or null")
    if "seed" in meta and meta["seed"] is not None and not isinstance(meta["seed"], int):
        raise ValueError("meta['seed'] must be int or null")
    cal = meta["calibration"]
    if cal.get("type") != "platt" or not isinstance(cal.get("a"), (int, float)) or not isinstance(
        cal.get("b"), (int, float)
    ):
        raise ValueError(f"meta['calibration'] malformed: {cal!r}")
    if not (0.0 <= meta["threshold"] <= 1.0):
        raise ValueError(f"meta['threshold'] out of [0,1]: {meta['threshold']}")
    for key in ("feature_names", "train_feature_means", "train_feature_stds", "feature_importance", "direction_sign"):
        if len(meta[key]) != n_features:
            raise ValueError(f"meta[{key!r}] has {len(meta[key])} entries, expected {n_features}")
    if list(meta["feature_names"]) != list(FEATURE_NAMES):
        raise ValueError("meta['feature_names'] does not match FEATURE_NAMES / fc-1 order")


def append_changelog(model_version: str, meta: Dict[str, Any], changelog_path: Path = CHANGELOG_PATH) -> None:
    changelog_path.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"- {model_version}: feature_contract={meta['feature_contract']} "
        f"threshold={meta['threshold']:.6f} git_sha={meta.get('git_sha')} "
        f"seed={meta.get('seed')} manifest_sha256={meta.get('manifest_sha256')}\n"
    )
    is_new = not changelog_path.is_file()
    with changelog_path.open("a", encoding="utf-8") as f:
        if is_new:
            f.write("# ONNX export changelog (T021)\n\n")
            f.write(
                "One line per exported `artifacts/model.onnx` version. Reproduce any "
                "entry from `git_sha` + `seed` + `manifest_sha256` by re-running the "
                "pipeline (T016 -> T017 -> T018 -> T021) against the same dataset.\n\n"
            )
        f.write(line)


def export(
    *,
    model_txt_path: Path = MODEL_TXT_PATH,
    calibration_path: Path = CALIBRATION_JSON_PATH,
    train_meta_path: Path = TRAIN_META_JSON_PATH,
    onnx_path: Path = ONNX_PATH,
    onnx_meta_path: Path = ONNX_META_PATH,
    changelog_path: Path = CHANGELOG_PATH,
) -> Dict[str, Any]:
    contract.assert_contract_matches_extractor()

    if not model_txt_path.is_file():
        raise FileNotFoundError(
            f"{model_txt_path} not found — run `python -m train.train` (T017) first."
        )
    booster = lgb.Booster(model_file=str(model_txt_path))

    calibration = load_json(calibration_path)
    train_meta = load_json(train_meta_path)

    model_version = next_model_version(changelog_path)
    meta = build_meta(model_version, calibration, train_meta)
    validate_meta_schema(meta)

    onnx_model = convert_to_onnx(booster)

    onnx_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(onnx_model, str(onnx_path))
    with onnx_meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")

    append_changelog(model_version, meta, changelog_path)
    return meta


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-txt-path", type=Path, default=MODEL_TXT_PATH)
    parser.add_argument("--calibration-path", type=Path, default=CALIBRATION_JSON_PATH)
    parser.add_argument("--train-meta-path", type=Path, default=TRAIN_META_JSON_PATH)
    parser.add_argument("--onnx-path", type=Path, default=ONNX_PATH)
    parser.add_argument("--onnx-meta-path", type=Path, default=ONNX_META_PATH)
    parser.add_argument("--changelog-path", type=Path, default=CHANGELOG_PATH)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    meta = export(
        model_txt_path=args.model_txt_path,
        calibration_path=args.calibration_path,
        train_meta_path=args.train_meta_path,
        onnx_path=args.onnx_path,
        onnx_meta_path=args.onnx_meta_path,
        changelog_path=args.changelog_path,
    )
    print(f"Exported {meta['model_version']} -> {args.onnx_path}")
    print(f"  feature_contract={meta['feature_contract']} threshold={meta['threshold']:.6f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
