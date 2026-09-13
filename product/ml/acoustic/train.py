#!/usr/bin/env python3
"""Train a tiny LightGBM on the acoustic features (same protocol as T017).

This is intentionally small and follows the same CV / reproducibility
conventions as `product/ml/train/train.py`. It expects a parquet table
`ml/data/acoustic_features.parquet` with columns:
  anon_id,label,split, duration_s, <8 acoustic feature names in order>

It writes:
  ml/data/acoustic_model_lgbm.txt
  product/artifacts/acoustic.onnx.meta.json   (sidecar with basic provenance)

Note: converting a LightGBM booster to an actual .onnx graph can be
performed by extending `product/ml/export/export_onnx.py` — this script
emits the textual booster + a meta sidecar to record the run.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

PRODUCT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"

FEATURES_PATH = DATA_DIR / "acoustic_features.parquet"
MODEL_PATH = DATA_DIR / "acoustic_model_lgbm.txt"
META_OUT = PRODUCT_DIR / "artifacts" / "acoustic.onnx.meta.json"

SEED = 20260912
N_FOLDS = 5
CV_SEEDS: Tuple[int, ...] = (SEED, SEED + 1, SEED + 2)
NUM_BOOST_ROUND_MAX = 500
EARLY_STOPPING_ROUNDS = 50

POSITIVE_LABEL = "synthetic"


def load_features(path: Path = FEATURES_PATH, split: str = "train") -> pd.DataFrame:
    if split != "train":
        raise RuntimeError("train only")
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — build acoustic_features.parquet first")
    df = pd.read_parquet(path)
    return df[df["split"] == split].reset_index(drop=True)


def label_to_target(labels: Sequence[str]) -> np.ndarray:
    return np.array([1 if l == POSITIVE_LABEL else 0 for l in labels], dtype=np.int64)


def compute_scale_pos_weight(y: np.ndarray) -> float:
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if pos == 0:
        return 1.0
    return neg / pos


def base_lgbm_params(scale_pos_weight: float, seed: int) -> dict:
    return {
        "objective": "binary",
        "metric": "auc",
        "max_depth": 3,
        "num_leaves": 15,
        "min_data_in_leaf": 10,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "learning_rate": 0.05,
        "scale_pos_weight": scale_pos_weight,
        "verbosity": -1,
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 1,
        "seed": seed,
    }


def run_cv(X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, n_folds: int = N_FOLDS, seeds: Sequence[int] = CV_SEEDS) -> dict:
    fold_aucs: List[float] = []
    fold_best_iterations: List[int] = []
    for seed in seeds:
        splitter = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=seed)
        for train_idx, valid_idx in splitter.split(X, y):
            params = base_lgbm_params(scale_pos_weight, seed)
            train_set = lgb.Dataset(X.iloc[train_idx], label=y[train_idx], feature_name=list(X.columns))
            valid_set = lgb.Dataset(X.iloc[valid_idx], label=y[valid_idx], feature_name=list(X.columns), reference=train_set)
            booster = lgb.train(
                params,
                train_set,
                num_boost_round=NUM_BOOST_ROUND_MAX,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            best_iteration = booster.best_iteration if booster.best_iteration else booster.current_iteration()
            preds = booster.predict(X.iloc[valid_idx], num_iteration=best_iteration)
            y_valid = y[valid_idx]
            if len(set(y_valid.tolist())) < 2:
                auc = 0.5
            else:
                auc = float(roc_auc_score(y_valid, preds))
            fold_aucs.append(auc)
            fold_best_iterations.append(max(1, int(best_iteration)))
    auc_arr = np.asarray(fold_aucs, dtype=np.float64)
    best_num_boost_round = max(1, int(round(float(np.mean(fold_best_iterations)))))
    return {
        "fold_aucs": fold_aucs,
        "fold_best_iterations": fold_best_iterations,
        "auc_mean": float(auc_arr.mean()),
        "auc_std": float(auc_arr.std(ddof=0)),
        "best_num_boost_round": best_num_boost_round,
    }


def train_final_model(X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, num_boost_round: int, seed: int = SEED) -> lgb.Booster:
    params = base_lgbm_params(scale_pos_weight, seed)
    train_set = lgb.Dataset(X, label=y, feature_name=list(X.columns))
    return lgb.train(params, train_set, num_boost_round=num_boost_round)


def get_git_sha() -> Optional[str]:
    try:
        result = subprocess.run(["git", "rev-parse", "HEAD"], cwd=PRODUCT_DIR, capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def compute_manifest_sha256() -> Optional[str]:
    try:
        # reuse common.dataset if available
        from product.ml.common.dataset import manifest_path  # type: ignore

        path = manifest_path()
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return None


def save_artifacts(booster: lgb.Booster, meta: dict, model_path: Path, meta_out: Path) -> None:
    model_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_path))
    meta_out.parent.mkdir(parents=True, exist_ok=True)
    with meta_out.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--features-path", type=Path, default=FEATURES_PATH)
    p.add_argument("--model-path", type=Path, default=MODEL_PATH)
    p.add_argument("--meta-out", type=Path, default=META_OUT)
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    df = load_features(args.features_path, split="train")
    feature_cols = [c for c in df.columns if c not in ("anon_id", "label", "split", "duration_s")]
    X = df[feature_cols]
    y = label_to_target(df["label"])
    scale_pos_weight = compute_scale_pos_weight(y)

    cv_result = run_cv(X, y, scale_pos_weight)
    print(f"CV AUC: mean={cv_result['auc_mean']:.4f} std={cv_result['auc_std']:.4f} over {len(cv_result['fold_aucs'])} folds")

    booster = train_final_model(X, y, scale_pos_weight, cv_result["best_num_boost_round"], seed=SEED)
    feature_importance = booster.feature_importance(importance_type="gain")

    meta = {
        "model_version": "acoustic-1",
        "feature_contract": "acoustic-1",
        "git_sha": get_git_sha(),
        "seed": SEED,
        "manifest_sha256": compute_manifest_sha256(),
        "feature_names": feature_cols,
        "train_feature_means": [float(x) for x in X.mean(axis=0).tolist()],
        "train_feature_stds": [float(x) for x in X.std(ddof=0, axis=0).tolist()],
        "feature_importance": [float(x) for x in feature_importance.tolist()],
    }

    save_artifacts(booster, meta, args.model_path, args.meta_out)
    print(f"wrote {args.model_path} and {args.meta_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

