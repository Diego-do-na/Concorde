#!/usr/bin/env python3
"""Calibrates model_lgbm.txt's scores into probabilities, on val (T018).

ADR-012 explicitly designates `val` for exactly two things: final measurement
and calibration. This script is that calibration step — the only place in
the whole pipeline (besides T019/T021 final reporting) allowed to open `val`.
`train/train.py` never does (see its own ADR-012 guard) and this file must
not weaken that: it reads `val` only to fit the sigmoid and to report the
resulting balanced accuracy, never to pick features or hyperparameters.

What "raw score" means here: `model_lgbm.txt` is a LightGBM `objective=binary`
booster, so `Booster.predict()` already applies the model's own internal
sigmoid — that in-[0,1] number is the "raw" score in this file's vocabulary
(uncalibrated, i.e. not yet trustworthy as a probability of the true label).
"Calibrated" means after Platt scaling has been applied on top of it.

Two different populations of raw scores are needed:

- **val raw scores**: `model_lgbm.txt` (the model actually shipped, trained
  on *all* of train) predicting on val. Used to fit Platt's A,B and to
  report val-side metrics (ADR-012's designated use).
- **train raw scores, out-of-fold**: `model_lgbm.txt` was fit on every train
  row, so scoring train rows with it would be in-sample and not usable for
  threshold selection. Instead this file reruns train/train.py's exact CV
  protocol (same N_FOLDS, CV_SEEDS, params) and keeps each fold's
  held-out predictions — deterministic and leakage-free, matching what
  train.py used to pick `best_num_boost_round`.

The threshold is chosen on the train OOF calibrated probabilities (never on
val) to maximise balanced accuracy — Altur's primary metric — with ties
(a flat BA plateau) broken by the asymmetric cost 3*FP + 1*FN from §8.4:
a false positive blocks a legitimate customer, three times as costly as
letting a synthetic caller through undetected.

Reads:
    ml/data/features_vad.parquet   built by train/build_dataset.py (T016)
    ml/data/model_lgbm.txt          the shipped booster (T017)

Writes:
    ml/data/calibration.json        {type, a, b, threshold, ...} — see
                                     CALIBRATION_JSON_SCHEMA below
    ml/data/reliability_curve.png   before/after reliability diagram on val

`ml/data/` is gitignored (AGENTS.md, NFR-011) — never committed.

Usage:
    cd product/ml
    python -m train.calibrate
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import lightgbm as lgb
import matplotlib

matplotlib.use("Agg")  # headless — this script never opens a display
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy.optimize import minimize  # noqa: E402
from scipy.special import expit  # noqa: E402
from sklearn.isotonic import IsotonicRegression  # noqa: E402
from sklearn.model_selection import StratifiedKFold  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES  # noqa: E402
from train import train as train_module  # noqa: E402
from train.train import compute_scale_pos_weight, label_to_target  # noqa: E402

ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"

VAD_PARQUET = DATA_DIR / "features_vad.parquet"
MODEL_PATH = DATA_DIR / "model_lgbm.txt"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
RELIABILITY_PNG = DATA_DIR / "reliability_curve.png"

# Altur's asymmetric cost (§8.4): a false positive blocks a real customer.
COST_FP = 3.0
COST_FN = 1.0

ECE_N_BINS = 10
SENSITIVITY_DELTA = 0.05

# Threshold is reported strictly inside (0, 1) — see VERIFY in T018's
# description — so it is never mistaken for "always synthetic"/"never
# synthetic" edge behavior baked into the served default.
_THRESHOLD_EPS = 1e-9

CONFIDENCE_RULE = "asserted_class"


# ---------------------------------------------------------------------------
# Data loading — this is the one file allowed to open `val` (ADR-012).
# ---------------------------------------------------------------------------


def load_all_features(path: Path = VAD_PARQUET) -> pd.DataFrame:
    """The full features_vad.parquet table, both `train` and `val` rows.

    Unlike train.train.load_features (which refuses anything but split=
    "train"), this module's entire purpose is calibrating on val — its
    documented, designated use (ADR-012). Both splits are legitimate here.
    """
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} not found — run `python -m train.build_dataset` first (T016)."
        )
    df = pd.read_parquet(path)
    missing = {"train", "val"} - set(df["split"].unique())
    if missing:
        raise RuntimeError(
            f"features table at {path} is missing split(s) {sorted(missing)} — "
            "calibration needs both train and val."
        )
    return df


# ---------------------------------------------------------------------------
# Train out-of-fold raw scores — reruns train.py's CV protocol to get
# leakage-free predictions for every train row (model_lgbm.txt itself was
# fit on all of train, so scoring train with it would be in-sample).
# ---------------------------------------------------------------------------


def compute_train_oof_scores(
    X: pd.DataFrame,
    y: np.ndarray,
    scale_pos_weight: float,
    n_folds: int = train_module.N_FOLDS,
    seeds: Sequence[int] = train_module.CV_SEEDS,
) -> np.ndarray:
    """Mean-over-seeds out-of-fold raw score for every row in X.

    Mirrors train.run_cv's fold loop exactly (same params/early stopping)
    but keeps each fold's held-out predictions instead of only the AUC —
    every train row lands in the validation fold exactly once per seed, so
    stacking those in a (seeds, n_rows) matrix and averaging over seeds
    gives one deterministic, leakage-free OOF score per row.
    """
    n = len(y)
    seed_oof = np.zeros((len(seeds), n), dtype=np.float64)

    for seed_idx, seed in enumerate(seeds):
        splitter = StratifiedKFold(n_splits=n_folds, shuffle=True, random_state=seed)
        for train_idx, valid_idx in splitter.split(X, y):
            params = train_module.base_lgbm_params(scale_pos_weight, seed)
            train_set = lgb.Dataset(
                X.iloc[train_idx], label=y[train_idx], feature_name=list(FEATURE_NAMES)
            )
            valid_set = lgb.Dataset(
                X.iloc[valid_idx],
                label=y[valid_idx],
                feature_name=list(FEATURE_NAMES),
                reference=train_set,
            )
            booster = lgb.train(
                params,
                train_set,
                num_boost_round=train_module.NUM_BOOST_ROUND_MAX,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(train_module.EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            best_iteration = booster.best_iteration if booster.best_iteration else booster.current_iteration()
            seed_oof[seed_idx, valid_idx] = booster.predict(X.iloc[valid_idx], num_iteration=best_iteration)

    return seed_oof.mean(axis=0)


# ---------------------------------------------------------------------------
# Platt scaling (Platt 1999) — fit on val, applied to any raw score.
# ---------------------------------------------------------------------------


def fit_platt(scores: np.ndarray, y: np.ndarray) -> Tuple[float, float]:
    """(A, B) such that P(synthetic|score) = 1 / (1 + exp(A*score + B)).

    Uses Platt's original regularized targets (his 1999 SVM paper, §2): the
    0/1 labels are replaced by t+ = (N+ + 1)/(N+ + 2) and t- = 1/(N- + 2)
    before fitting, so the sigmoid isn't pulled to +/-infinity trying to
    perfectly separate an oft-small val split (71 rows here). The negative
    log-likelihood below is convex in (A, B); an analytic gradient is
    supplied so BFGS converges to the same optimum on every rerun.
    """
    n_pos = int((y == 1).sum())
    n_neg = int((y == 0).sum())
    if n_pos == 0 or n_neg == 0:
        raise ValueError("fit_platt requires both classes present in y")

    t_pos = (n_pos + 1.0) / (n_pos + 2.0)
    t_neg = 1.0 / (n_neg + 2.0)
    t = np.where(y == 1, t_pos, t_neg)

    def neg_log_likelihood(params: np.ndarray) -> float:
        a, b = params
        z = a * scores + b
        return float(np.sum(np.logaddexp(0.0, z) - (1.0 - t) * z))

    def grad(params: np.ndarray) -> np.ndarray:
        a, b = params
        z = a * scores + b
        residual = expit(z) - (1.0 - t)
        return np.array([float(np.sum(residual * scores)), float(np.sum(residual))])

    a0 = 0.0
    b0 = float(np.log((n_neg + 1.0) / (n_pos + 1.0)))
    result = minimize(neg_log_likelihood, x0=[a0, b0], jac=grad, method="BFGS")
    if not result.success:
        result = minimize(neg_log_likelihood, x0=[a0, b0], method="Nelder-Mead")
    a, b = result.x
    return float(a), float(b)


def apply_platt(scores: np.ndarray, a: float, b: float) -> np.ndarray:
    """P(synthetic|score) = 1 / (1 + exp(A*score + B)), computed stably."""
    return expit(-(a * np.asarray(scores, dtype=np.float64) + b))


# ---------------------------------------------------------------------------
# Threshold selection: max balanced accuracy on train OOF, ties -> cost.
# ---------------------------------------------------------------------------


def balanced_accuracy(y: np.ndarray, pred: np.ndarray) -> float:
    pos = y == 1
    neg = y == 0
    n_pos = int(pos.sum())
    n_neg = int(neg.sum())
    tpr = float((pred[pos] == 1).sum()) / n_pos if n_pos else 0.0
    tnr = float((pred[neg] == 0).sum()) / n_neg if n_neg else 0.0
    return (tpr + tnr) / 2.0


def asymmetric_cost(y: np.ndarray, pred: np.ndarray, cost_fp: float = COST_FP, cost_fn: float = COST_FN) -> float:
    fp = int(((pred == 1) & (y == 0)).sum())
    fn = int(((pred == 0) & (y == 1)).sum())
    return cost_fp * fp + cost_fn * fn


def select_threshold(probs: np.ndarray, y: np.ndarray) -> Tuple[float, Dict[str, Any]]:
    """Threshold maximising balanced accuracy on (probs, y).

    Candidates are every achievable decision boundary: each observed
    probability value (decision rule is `prob >= threshold`) plus one
    sentinel just above the max (the "classify nothing as synthetic"
    option). Ties in balanced accuracy — a flat plateau, common with ~300
    calls — are broken by the asymmetric cost 3*FP + 1*FN (§8.4: prefer the
    threshold that blocks fewer legitimate customers); further ties are
    broken by the smallest threshold value, for full determinism.
    """
    probs = np.asarray(probs, dtype=np.float64)
    y = np.asarray(y)
    candidates = np.unique(probs)
    if candidates.size == 0:
        raise ValueError("select_threshold requires at least one probability")
    hi_sentinel = np.nextafter(candidates.max(), np.inf)
    candidates = np.concatenate([candidates, [hi_sentinel]])

    best_ba = -1.0
    best_thresholds: List[float] = []
    for thr in candidates:
        pred = (probs >= thr).astype(int)
        ba = balanced_accuracy(y, pred)
        if ba > best_ba:
            best_ba = ba
            best_thresholds = [thr]
        elif ba == best_ba:
            best_thresholds.append(thr)

    costs = [(thr, asymmetric_cost(y, (probs >= thr).astype(int))) for thr in best_thresholds]
    min_cost = min(c for _, c in costs)
    tied_by_cost = [thr for thr, c in costs if c == min_cost]
    threshold = float(min(tied_by_cost))
    threshold = min(max(threshold, _THRESHOLD_EPS), 1.0 - _THRESHOLD_EPS)

    info = {
        "ba_train_oof": best_ba,
        "n_ba_candidates": len(candidates),
        "n_tied_ba": len(best_thresholds),
        "n_tied_cost": len(tied_by_cost),
        "min_cost": min_cost,
    }
    return threshold, info


# ---------------------------------------------------------------------------
# Calibration quality metrics.
# ---------------------------------------------------------------------------


def brier_score(probs: np.ndarray, y: np.ndarray) -> float:
    probs = np.asarray(probs, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    return float(np.mean((probs - y) ** 2))


def expected_calibration_error(probs: np.ndarray, y: np.ndarray, n_bins: int = ECE_N_BINS) -> float:
    """Standard equal-width-bin ECE: sum over bins of (bin weight) * |acc - conf|."""
    probs = np.asarray(probs, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    n = len(y)
    if n == 0:
        return 0.0
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    ece = 0.0
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs <= hi) if i == n_bins - 1 else (probs >= lo) & (probs < hi)
        count = int(mask.sum())
        if count == 0:
            continue
        bin_conf = float(probs[mask].mean())
        bin_acc = float(y[mask].mean())
        ece += (count / n) * abs(bin_acc - bin_conf)
    return float(ece)


def _reliability_bin_points(probs: np.ndarray, y: np.ndarray, n_bins: int = ECE_N_BINS) -> Tuple[List[float], List[float]]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    centers: List[float] = []
    accs: List[float] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs <= hi) if i == n_bins - 1 else (probs >= lo) & (probs < hi)
        if not np.any(mask):
            continue
        centers.append(float(probs[mask].mean()))
        accs.append(float(y[mask].mean()))
    return centers, accs


def write_reliability_curve(
    val_raw: np.ndarray, val_platt: np.ndarray, y_val: np.ndarray, path: Path, n_bins: int = ECE_N_BINS
) -> None:
    raw_c, raw_a = _reliability_bin_points(val_raw, y_val, n_bins)
    platt_c, platt_a = _reliability_bin_points(val_platt, y_val, n_bins)

    fig, ax = plt.subplots(figsize=(5, 5))
    ax.plot([0, 1], [0, 1], linestyle="--", color="gray", label="perfect calibration")
    ax.plot(raw_c, raw_a, marker="o", label="before (raw)")
    ax.plot(platt_c, platt_a, marker="o", label="after (Platt)")
    ax.set_xlabel("predicted probability")
    ax.set_ylabel("observed frequency (synthetic)")
    ax.set_title("Reliability curve — val split")
    ax.set_xlim(0.0, 1.0)
    ax.set_ylim(0.0, 1.0)
    ax.legend(loc="upper left")
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


# ---------------------------------------------------------------------------
# calibration.json schema — hand-rolled (no `jsonschema` dependency) but
# shaped like a JSON Schema: required keys, types, enums, numeric bounds.
# ---------------------------------------------------------------------------

CALIBRATION_JSON_SCHEMA: Dict[str, Dict[str, Any]] = {
    "type": {"type": str, "enum": {"platt"}},
    "a": {"type": (int, float)},
    "b": {"type": (int, float)},
    "threshold": {"type": (int, float), "minimum": 0.0, "maximum": 1.0, "exclusive": True},
    "threshold_rule": {"type": str, "enum": {"max balanced accuracy on train OOF, ties -> 3FP+1FN"}},
    "fitted_on": {"type": str, "enum": {"val"}},
    "ba_train_oof": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "ba_val": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "ece_val": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "brier_val_before": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "brier_val_after": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "confidence_rule": {"type": str, "enum": {CONFIDENCE_RULE}},
}


def validate_calibration_schema(obj: Dict[str, Any]) -> None:
    """Raises ValueError on the first violation of CALIBRATION_JSON_SCHEMA."""
    for key, rule in CALIBRATION_JSON_SCHEMA.items():
        if key not in obj:
            raise ValueError(f"calibration.json missing required key: {key!r}")
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, rule["type"]):
            raise ValueError(f"calibration.json key {key!r} has wrong type: {type(value).__name__}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"calibration.json key {key!r} must be one of {rule['enum']}, got {value!r}")
        if "minimum" in rule:
            ok = value > rule["minimum"] if rule.get("exclusive") else value >= rule["minimum"]
            if not ok:
                raise ValueError(f"calibration.json key {key!r}={value} below minimum {rule['minimum']}")
        if "maximum" in rule:
            ok = value < rule["maximum"] if rule.get("exclusive") else value <= rule["maximum"]
            if not ok:
                raise ValueError(f"calibration.json key {key!r}={value} above maximum {rule['maximum']}")


def save_calibration(calibration: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(calibration, f, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-path", type=Path, default=VAD_PARQUET)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--calibration-path", type=Path, default=CALIBRATION_PATH)
    parser.add_argument("--reliability-path", type=Path, default=RELIABILITY_PNG)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    contract.assert_contract_matches_extractor()

    df = load_all_features(args.features_path)
    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)

    X_train = train_df[list(FEATURE_NAMES)]
    y_train = label_to_target(train_df["label"])
    X_val = val_df[list(FEATURE_NAMES)]
    y_val = label_to_target(val_df["label"])

    scale_pos_weight = compute_scale_pos_weight(y_train)

    print("Recomputing train out-of-fold raw scores (reruns train.py's CV protocol)...")
    train_oof_raw = compute_train_oof_scores(X_train, y_train, scale_pos_weight)

    if not args.model_path.is_file():
        raise FileNotFoundError(f"{args.model_path} not found — run `python -m train.train` first (T017).")
    booster = lgb.Booster(model_file=str(args.model_path))
    val_raw = booster.predict(X_val)

    a, b = fit_platt(val_raw, y_val)
    val_platt = apply_platt(val_raw, a, b)
    train_oof_platt = apply_platt(train_oof_raw, a, b)

    # Isotonic regression, reported for comparison only — Platt is what
    # ships (two floats, robust on a ~70-row val split; isotonic can
    # overfit that few points, per T018's spec).
    isotonic = IsotonicRegression(y_min=0.0, y_max=1.0, out_of_bounds="clip")
    isotonic.fit(val_raw, y_val)
    val_isotonic = isotonic.predict(val_raw)
    brier_val_isotonic = brier_score(val_isotonic, y_val)

    threshold, threshold_info = select_threshold(train_oof_platt, y_train)

    def _ba_at(thr: float) -> float:
        return balanced_accuracy(y_val, (val_platt >= thr).astype(int))

    ba_val = _ba_at(threshold)
    ba_val_minus = _ba_at(max(0.0, threshold - SENSITIVITY_DELTA))
    ba_val_plus = _ba_at(min(1.0, threshold + SENSITIVITY_DELTA))

    ece_val = expected_calibration_error(val_platt, y_val)
    brier_val_before = brier_score(val_raw, y_val)
    brier_val_after = brier_score(val_platt, y_val)

    calibration: Dict[str, Any] = {
        "type": "platt",
        "a": a,
        "b": b,
        "threshold": threshold,
        "threshold_rule": "max balanced accuracy on train OOF, ties -> 3FP+1FN",
        "fitted_on": "val",
        "ba_train_oof": threshold_info["ba_train_oof"],
        "ba_val": ba_val,
        "ece_val": ece_val,
        "brier_val_before": brier_val_before,
        "brier_val_after": brier_val_after,
        "confidence_rule": CONFIDENCE_RULE,
        # Extra, informational fields beyond T018's core schema — the
        # threshold-sensitivity and isotonic-comparison numbers the task
        # description asks to *report*, kept out of CALIBRATION_JSON_SCHEMA's
        # required set since only {type, a, b, threshold, ...} is frozen.
        "ba_val_threshold_minus_0_05": ba_val_minus,
        "ba_val_threshold_plus_0_05": ba_val_plus,
        "brier_val_isotonic_comparison": brier_val_isotonic,
    }

    validate_calibration_schema(calibration)
    save_calibration(calibration, args.calibration_path)
    write_reliability_curve(val_raw, val_platt, y_val, args.reliability_path)

    print(
        f"Platt: a={a:.4f} b={b:.4f} threshold={threshold:.4f} "
        f"(ba_train_oof={threshold_info['ba_train_oof']:.4f})"
    )
    print(
        f"val: ba={ba_val:.4f} (thr-0.05={ba_val_minus:.4f}, thr+0.05={ba_val_plus:.4f}) "
        f"ece={ece_val:.4f} brier_before={brier_val_before:.4f} brier_after={brier_val_after:.4f} "
        f"brier_isotonic={brier_val_isotonic:.4f}"
    )
    print(f"wrote {args.calibration_path}")
    print(f"wrote {args.reliability_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
