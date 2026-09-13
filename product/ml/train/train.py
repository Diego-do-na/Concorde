#!/usr/bin/env python3
"""Trains the fc-1 LightGBM binary classifier on `train` only (T017).

ADR-012 deviation: `manifest.csv` carries no speaker identifier and the
dataset terms forbid trying to identify anyone, so the speaker-grouped CV
ADR-012 calls for is not possible here and must not be approximated by
clustering voices. This script instead runs stratified K-fold (by label,
grouped by `anon_id` only in the trivial sense that each call is one row)
*inside* `train`; `val` is the only speaker-disjoint measurement anywhere
in this pipeline, and it is never opened by this file.

Reads:
    ml/data/features_vad.parquet   built by train/build_dataset.py (T016);
                                    training happens on the VAD-derived
                                    table, never features_ref (FR-004/ADR-003)
    ml/validation/vad_agreement/REPORT.md   T012's gate; must read PASS or
                                             this script refuses to train

Writes:
    ml/data/model_lgbm.txt      LightGBM text model (train split only)
    ml/data/train_meta.json     reproducibility record (see build_meta())

`ml/data/` is gitignored (AGENTS.md, NFR-011) — never committed.

Usage:
    cd product/ml
    python -m train.train
    python -m train.train --acknowledge-audit   # required if CV AUC > 0.97, §10.2
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
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.dataset import manifest_path  # noqa: E402
from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES  # noqa: E402

# product/ — two levels up from product/ml/train/train.py.
PRODUCT_DIR = Path(__file__).resolve().parents[2]
ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"

VAD_PARQUET = DATA_DIR / "features_vad.parquet"
MODEL_PATH = DATA_DIR / "model_lgbm.txt"
META_PATH = DATA_DIR / "train_meta.json"
VAD_AGREEMENT_REPORT = ML_DIR / "validation" / "vad_agreement" / "REPORT.md"

SEED = 20260912
N_FOLDS = 5
CV_SEEDS: Tuple[int, ...] = (SEED, SEED + 1, SEED + 2)
NUM_BOOST_ROUND_MAX = 500
EARLY_STOPPING_ROUNDS = 50

# Anti-self-deception rule, §10.2: a CV AUC this high on ~300 calls almost
# always means leakage or a shortcut feature, not a genuinely solved problem.
CV_AUC_AUDIT_THRESHOLD = 0.97

POSITIVE_LABEL = "synthetic"  # matches /detect's is_synthetic (ADR-006)


def assert_vad_gate_passed(report_path: Path = VAD_AGREEMENT_REPORT) -> None:
    """Raises if T012's VAD agreement gate did not read PASS.

    Training on VAD-derived features (features_vad.parquet) without this
    gate risks silently training on a VAD that doesn't track the ground
    truth turns closely enough (R-01, the #1 silent risk in this project).
    """
    if not report_path.is_file():
        raise RuntimeError(
            f"VAD agreement report not found at {report_path} — run T012's "
            "sweep/run_agreement scripts before training."
        )
    text = report_path.read_text(encoding="utf-8")
    if "FR-004 VAD agreement gate: PASS" not in text:
        raise RuntimeError(
            f"VAD agreement gate did not read PASS in {report_path} — "
            "refusing to train on features_vad until R-01 is resolved."
        )


def load_features(path: Path = VAD_PARQUET, split: str = "train") -> pd.DataFrame:
    """Loads `path` (a features_vad.parquet-shaped table) filtered to one split.

    ADR-012: `val` is intocable during training — this function only ever
    accepts split="train"; any other value raises rather than silently
    returning val rows. Callers in this module must never pass anything
    other than the literal "train".
    """
    if split != "train":
        raise RuntimeError(
            "ADR-012: val is intocable during training — train.py must only "
            f"ever load split='train', got split={split!r}"
        )
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found — run `python -m train.build_dataset` first (T016)."
        )
    df = pd.read_parquet(path)
    return df[df["split"] == split].reset_index(drop=True)


def label_to_target(labels: Sequence[str]) -> np.ndarray:
    """1 for "synthetic", 0 for "human" — matches /detect's is_synthetic."""
    return np.array([1 if label == POSITIVE_LABEL else 0 for label in labels], dtype=np.int64)


def compute_scale_pos_weight(y: np.ndarray) -> float:
    """neg/pos count ratio — the 40/60 (human/synthetic) train imbalance."""
    pos = int((y == 1).sum())
    neg = int((y == 0).sum())
    if pos == 0:
        return 1.0
    return neg / pos


def base_lgbm_params(scale_pos_weight: float, seed: int) -> dict:
    return {
        "objective": "binary",
        "metric": "auc",
        "max_depth": 4,
        "num_leaves": 15,
        "min_data_in_leaf": 15,
        "feature_fraction": 0.8,
        "bagging_fraction": 0.8,
        "bagging_freq": 1,
        "learning_rate": 0.05,
        "scale_pos_weight": scale_pos_weight,
        "verbosity": -1,
        # Determinism: single-threaded, forced row-wise histogram building,
        # and every internal RNG pinned to `seed` (LightGBM docs: deterministic=True
        # plus force_row_wise=True is required for bit-identical reruns).
        "deterministic": True,
        "force_row_wise": True,
        "num_threads": 1,
        "seed": seed,
        "bagging_seed": seed,
        "feature_fraction_seed": seed,
        "data_random_seed": seed,
    }


def run_cv(
    X: pd.DataFrame,
    y: np.ndarray,
    scale_pos_weight: float,
    n_folds: int = N_FOLDS,
    seeds: Sequence[int] = CV_SEEDS,
) -> dict:
    """5-fold StratifiedKFold repeated over `seeds`, early stopping per fold.

    Returns fold_aucs (len == n_folds * len(seeds)), fold_best_iterations,
    auc_mean, auc_std, and best_num_boost_round (mean best iteration,
    rounded, floor 1) used to train the final full-train model.
    """
    fold_aucs: List[float] = []
    fold_best_iterations: List[int] = []

    for seed in seeds:
        splitter = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=seed)
        for train_idx, valid_idx in splitter.split(X, y):
            params = base_lgbm_params(scale_pos_weight, seed)
            train_set = lgb.Dataset(
                X.iloc[train_idx], label=y[train_idx], feature_name=list(FEATURE_NAMES)
            )
            valid_set = lgb.Dataset(
                X.iloc[valid_idx], label=y[valid_idx], feature_name=list(FEATURE_NAMES), reference=train_set
            )
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


def train_final_model(
    X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, num_boost_round: int, seed: int = SEED
) -> lgb.Booster:
    """Final model trained on every train-split row, no held-out validation
    set (the CV above already spent its budget deciding num_boost_round)."""
    params = base_lgbm_params(scale_pos_weight, seed)
    train_set = lgb.Dataset(X, label=y, feature_name=list(FEATURE_NAMES))
    return lgb.train(params, train_set, num_boost_round=num_boost_round)


def compute_direction_signs(X: pd.DataFrame, y: np.ndarray) -> List[int]:
    """Sign of the Spearman correlation between each fc-1 feature and the
    train label (1=synthetic), used downstream for dashboard explainability
    (a feature that pushes the verdict toward synthetic gets +1)."""
    signs: List[int] = []
    for name in FEATURE_NAMES:
        col = X[name].to_numpy(dtype=np.float64)
        if np.std(col) == 0.0 or len(col) < 2:
            signs.append(1)
            continue
        rho, _p = spearmanr(col, y)
        signs.append(1 if (rho == rho and rho >= 0) else -1)  # rho==rho: not NaN
    return signs


def compute_feature_stats(X: pd.DataFrame) -> Tuple[List[float], List[float]]:
    """(means, population stds) per fc-1 feature on the train split."""
    means = [float(X[name].mean()) for name in FEATURE_NAMES]
    stds = [float(X[name].std(ddof=0)) for name in FEATURE_NAMES]
    return means, stds


def get_git_sha() -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=PRODUCT_DIR, capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def compute_manifest_sha256() -> Optional[str]:
    """sha256 of manifest.csv, matching eda/run_eda.py's gate_manifest_and_wav.

    Best-effort: the raw dataset (manifest.csv) is gitignored and may be
    absent on a machine that only has the already-built features_vad.parquet;
    that's fine here since train.py never reads manifest.csv itself.
    """
    try:
        path = manifest_path()
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def print_audit_top5(feature_names: Sequence[str], importances: Sequence[float]) -> None:
    order = np.argsort(importances)[::-1][:5]
    print("=" * 72)
    print("ANTI-SELF-DECEPTION AUDIT (§10.2): CV AUC exceeds the "
          f"{CV_AUC_AUDIT_THRESHOLD} suspicion threshold.")
    print("Top-5 features by gain importance:")
    for rank, idx in enumerate(order, start=1):
        print(f"  {rank}. {feature_names[idx]:<28} gain={importances[idx]:.4f}")
    print(
        "A CV AUC this high on ~300 calls usually means leakage or a shortcut "
        "feature, not a genuinely solved problem. A human must review the list "
        "above and re-run with --acknowledge-audit to accept the model."
    )
    print("=" * 72)


def build_meta(
    *,
    cv_result: dict,
    params: dict,
    feature_importance: Sequence[float],
    train_means: Sequence[float],
    train_stds: Sequence[float],
    direction_sign: Sequence[int],
    seed: int,
) -> dict:
    return {
        "feature_contract": contract.CONTRACT_ID,
        "git_sha": get_git_sha(),
        "seed": seed,
        "manifest_sha256": compute_manifest_sha256(),
        "cv_auc_mean": cv_result["auc_mean"],
        "cv_auc_std": cv_result["auc_std"],
        "cv_protocol": {
            "n_folds": N_FOLDS,
            "seeds": list(CV_SEEDS),
            "note": (
                "ADR-012 deviation: stratified K-fold by anon_id inside train "
                "(manifest.csv has no speaker identifier and speaker "
                "identification is forbidden by the dataset terms — clustering "
                "voices to approximate speaker grouping is not an acceptable "
                "substitute); val is the only speaker-disjoint measurement."
            ),
        },
        "params": params,
        "feature_names": list(FEATURE_NAMES),
        "feature_importance": [float(x) for x in feature_importance],
        "train_feature_means": [float(x) for x in train_means],
        "train_feature_stds": [float(x) for x in train_stds],
        "direction_sign": [int(x) for x in direction_sign],
    }


def save_artifacts(booster: lgb.Booster, meta: dict, model_path: Path, meta_path: Path) -> None:
    model_path.parent.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(model_path))
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2, sort_keys=True)
        f.write("\n")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-path", type=Path, default=VAD_PARQUET)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--meta-path", type=Path, default=META_PATH)
    parser.add_argument("--vad-report-path", type=Path, default=VAD_AGREEMENT_REPORT)
    parser.add_argument(
        "--acknowledge-audit",
        action="store_true",
        help="Required to proceed when CV AUC exceeds the §10.2 suspicion threshold.",
    )
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    contract.assert_contract_matches_extractor()
    assert_vad_gate_passed(args.vad_report_path)

    df = load_features(args.features_path, split="train")
    X = df[list(FEATURE_NAMES)]
    y = label_to_target(df["label"])
    scale_pos_weight = compute_scale_pos_weight(y)

    cv_result = run_cv(X, y, scale_pos_weight)
    print(
        f"CV AUC: mean={cv_result['auc_mean']:.4f} std={cv_result['auc_std']:.4f} "
        f"over {len(cv_result['fold_aucs'])} folds "
        f"({N_FOLDS} folds x {len(CV_SEEDS)} seeds); "
        f"best_num_boost_round={cv_result['best_num_boost_round']}"
    )

    booster = train_final_model(
        X, y, scale_pos_weight, cv_result["best_num_boost_round"], seed=SEED
    )
    feature_importance = booster.feature_importance(importance_type="gain")

    if cv_result["auc_mean"] > CV_AUC_AUDIT_THRESHOLD:
        print_audit_top5(FEATURE_NAMES, feature_importance)
        if not args.acknowledge_audit:
            print("Refusing to save artifacts without --acknowledge-audit.")
            return 1

    direction_sign = compute_direction_signs(X, y)
    train_means, train_stds = compute_feature_stats(X)
    params = base_lgbm_params(scale_pos_weight, SEED)

    meta = build_meta(
        cv_result=cv_result,
        params=params,
        feature_importance=feature_importance,
        train_means=train_means,
        train_stds=train_stds,
        direction_sign=direction_sign,
        seed=SEED,
    )

    save_artifacts(booster, meta, args.model_path, args.meta_path)
    print(f"wrote {args.model_path}")
    print(f"wrote {args.meta_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
