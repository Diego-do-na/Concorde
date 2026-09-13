#!/usr/bin/env python3
"""FR-014 A/B (fc-1 vs fc-1+invention_score) + the runtime fusion rule (T044).

Two questions, both answered per whisper model (`tiny`, `base` — so this
also feeds T057's model-choice decision alongside its timing numbers):

(1) **A/B**, T017's exact protocol (train.train.run_cv / train_final_model,
    same params/seeds/folds): fc-1 alone vs fc-1 + `invention_score` as an
    extra column, val AUC / Brier / balanced accuracy for both.

(2) **Runtime fusion** — the rule `/detect` would actually apply:

        logit(p_final) = logit(p_behavioral) + w * (invention_score - 0.5)
        clamped so |p_final - p_behavioral| <= max_delta_p (0.15)

    `p_behavioral` is the shipped fc-1-only calibrated probability
    (`ml/data/calibration.json`, T017/T018 — never retrained here). `w` is
    chosen on TRAIN out-of-fold predictions to maximise the fused
    verdict's balanced accuracy at the shipped threshold (Altur's primary
    metric, §8.4) — never on val (ADR-012). Reported on val: BA, AUC,
    Brier of the fused verdict, and the fraction of val verdicts that
    flip relative to behavioral-only at the shipped threshold.

Honest outcome rule (this task's own instruction): if a whisper model's
fused val BA is not higher than behavioral-only val BA, `w` is set to 0 in
that model's result — the layer stays visible in `/analyze` but never
moves the verdict. The model actually written to `semantic_fusion.json`
is whichever of {tiny, base} has the higher fused val BA (ties -> base,
the better ASR); T057's timing numbers are still required before this is
final (see docs/semantic-layer.md).

Reads:
    ml/data/features_vad.parquet    T016
    ml/data/model_lgbm.txt          T017 (the shipped fc-1-only booster)
    ml/data/calibration.json        T018 (p_behavioral's a/b/threshold)
    ml/data/semantic_scores.csv     score_dataset.py (this task, part a)

Writes:
    ml/semantic/AB_REPORT.md
    product/artifacts/semantic_fusion.json

Usage:
    cd product/ml
    python3 semantic/ab_report.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy.special import expit, logit
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # product/ml/ on sys.path

from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES  # noqa: E402
from train import train as train_module  # noqa: E402
from train.calibrate import (  # noqa: E402
    apply_platt,
    balanced_accuracy,
    brier_score,
    compute_train_oof_scores,
)
from train.train import compute_scale_pos_weight, label_to_target  # noqa: E402

ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"
SEMANTIC_DIR = Path(__file__).resolve().parent
ARTIFACTS_DIR = ML_DIR.parent / "artifacts"

VAD_PARQUET = DATA_DIR / "features_vad.parquet"
MODEL_PATH = DATA_DIR / "model_lgbm.txt"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
SEMANTIC_SCORES_PATH = DATA_DIR / "semantic_scores.csv"

AB_REPORT_PATH = SEMANTIC_DIR / "AB_REPORT.md"
FUSION_PATH = ARTIFACTS_DIR / "semantic_fusion.json"

WHISPER_MODELS = ("tiny", "base")
MAX_DELTA_P_DEFAULT = 0.15
FUSION_ARTIFACT_VERSION = "semantic-fusion-v1"
_PROB_EPS = 1e-6
_W_GRID = np.round(np.arange(-4.0, 4.0 + 1e-9, 0.05), 2)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------


def load_features() -> pd.DataFrame:
    if not VAD_PARQUET.is_file():
        raise FileNotFoundError(f"{VAD_PARQUET} not found -- run `python -m train.build_dataset` (T016).")
    df = pd.read_parquet(VAD_PARQUET)
    missing = {"train", "val"} - set(df["split"].unique())
    if missing:
        raise RuntimeError(f"features table missing split(s) {sorted(missing)}")
    return df


def load_semantic_scores(model: str) -> pd.DataFrame:
    if not SEMANTIC_SCORES_PATH.is_file():
        raise FileNotFoundError(
            f"{SEMANTIC_SCORES_PATH} not found -- run `python3 semantic/score_dataset.py` first."
        )
    df = pd.read_csv(SEMANTIC_SCORES_PATH)
    df = df[df["model"] == model][["anon_id", "invention_score", "semantic_available"]]
    return df


def merge_invention_score(features: pd.DataFrame, semantic: pd.DataFrame) -> pd.DataFrame:
    """Left-joins `invention_score` onto `features` by anon_id. A call with
    no semantic row at all (shouldn't happen once score_dataset.py has run
    over the whole manifest, but defended anyway) gets the same neutral
    0.5 default score_dataset.py itself writes for an unavailable call."""
    merged = features.merge(semantic, on="anon_id", how="left")
    merged["invention_score"] = merged["invention_score"].fillna(0.5)
    merged["semantic_available"] = merged["semantic_available"].fillna(False)
    return merged


# ---------------------------------------------------------------------------
# (1) FR-014 A/B — T017's exact protocol, fc-1 alone vs fc-1 + invention_score
# ---------------------------------------------------------------------------


def _run_cv_for_features(
    X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, feature_names: Sequence[str]
) -> dict:
    """train_module.run_cv, but parameterised on `feature_names` instead of
    that module's hardcoded fc-1-only FEATURE_NAMES global — needed here
    since this file's whole point is comparing fc-1 against fc-1 + one
    extra column. Same params/seeds/folds/early-stopping as T017."""
    import lightgbm as lgb
    from sklearn.model_selection import StratifiedKFold

    fold_aucs: List[float] = []
    fold_best_iterations: List[int] = []
    for seed in train_module.CV_SEEDS:
        splitter = StratifiedKFold(n_splits=train_module.N_FOLDS, shuffle=True, random_state=seed)
        for train_idx, valid_idx in splitter.split(X, y):
            params = train_module.base_lgbm_params(scale_pos_weight, seed)
            train_set = lgb.Dataset(X.iloc[train_idx], label=y[train_idx], feature_name=list(feature_names))
            valid_set = lgb.Dataset(
                X.iloc[valid_idx], label=y[valid_idx], feature_name=list(feature_names), reference=train_set
            )
            booster = lgb.train(
                params,
                train_set,
                num_boost_round=train_module.NUM_BOOST_ROUND_MAX,
                valid_sets=[valid_set],
                callbacks=[lgb.early_stopping(train_module.EARLY_STOPPING_ROUNDS, verbose=False)],
            )
            best_iteration = booster.best_iteration if booster.best_iteration else booster.current_iteration()
            preds = booster.predict(X.iloc[valid_idx], num_iteration=best_iteration)
            y_valid = y[valid_idx]
            auc = float(roc_auc_score(y_valid, preds)) if len(set(y_valid.tolist())) > 1 else 0.5
            fold_aucs.append(auc)
            fold_best_iterations.append(max(1, int(best_iteration)))

    auc_arr = np.asarray(fold_aucs, dtype=np.float64)
    return {
        "fold_aucs": fold_aucs,
        "auc_mean": float(auc_arr.mean()),
        "auc_std": float(auc_arr.std(ddof=0)),
        "best_num_boost_round": max(1, int(round(float(np.mean(fold_best_iterations))))),
    }


def _train_final_for_features(
    X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, num_boost_round: int, feature_names: Sequence[str]
) -> "lgb.Booster":
    params = train_module.base_lgbm_params(scale_pos_weight, train_module.SEED)
    train_set = lgb.Dataset(X, label=y, feature_name=list(feature_names))
    return lgb.train(params, train_set, num_boost_round=num_boost_round)


def _compute_train_oof_for_features(
    X: pd.DataFrame, y: np.ndarray, scale_pos_weight: float, feature_names: Sequence[str]
) -> np.ndarray:
    """compute_train_oof_scores, parameterised the same way as
    `_run_cv_for_features` above (see its docstring)."""
    import lightgbm as lgb
    from sklearn.model_selection import StratifiedKFold

    n = len(y)
    seed_oof = np.zeros((len(train_module.CV_SEEDS), n), dtype=np.float64)
    for seed_idx, seed in enumerate(train_module.CV_SEEDS):
        splitter = StratifiedKFold(n_splits=train_module.N_FOLDS, shuffle=True, random_state=seed)
        for train_idx, valid_idx in splitter.split(X, y):
            params = train_module.base_lgbm_params(scale_pos_weight, seed)
            train_set = lgb.Dataset(X.iloc[train_idx], label=y[train_idx], feature_name=list(feature_names))
            valid_set = lgb.Dataset(
                X.iloc[valid_idx], label=y[valid_idx], feature_name=list(feature_names), reference=train_set
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


def run_ab_variant(df: pd.DataFrame, feature_names: Sequence[str]) -> Dict[str, Any]:
    """Trains+evaluates one feature-set variant with T017's protocol.
    Returns cv auc, val auc/brier/ba (own Platt fit + own OOF-selected
    threshold — each variant is calibrated on its own terms, exactly as
    T017/T018 would if this variant were the one actually shipped)."""
    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)

    X_train = train_df[list(feature_names)]
    y_train = label_to_target(train_df["label"])
    X_val = val_df[list(feature_names)]
    y_val = label_to_target(val_df["label"])

    scale_pos_weight = compute_scale_pos_weight(y_train)
    cv_result = _run_cv_for_features(X_train, y_train, scale_pos_weight, feature_names)

    booster = _train_final_for_features(
        X_train, y_train, scale_pos_weight, cv_result["best_num_boost_round"], feature_names
    )
    val_raw = booster.predict(X_val)
    auc_val = float(roc_auc_score(y_val, val_raw)) if len(set(y_val.tolist())) > 1 else 0.5

    # Own Platt fit on val (ADR-012's designated use) + own threshold
    # picked on train OOF, mirroring train/calibrate.py exactly.
    from train.calibrate import fit_platt, select_threshold  # local import, avoids cycle at module load

    oof_raw = _compute_train_oof_for_features(X_train, y_train, scale_pos_weight, feature_names)
    a, b = fit_platt(val_raw, y_val)
    val_platt = apply_platt(val_raw, a, b)
    oof_platt = apply_platt(oof_raw, a, b)
    threshold, _info = select_threshold(oof_platt, y_train)

    ba_val = balanced_accuracy(y_val, (val_platt >= threshold).astype(int))
    brier_val = brier_score(val_platt, y_val)

    return {
        "cv_auc_mean": cv_result["auc_mean"],
        "cv_auc_std": cv_result["auc_std"],
        "auc_val": auc_val,
        "ba_val": float(ba_val),
        "brier_val": float(brier_val),
        "threshold": float(threshold),
    }


def run_ab_for_whisper_model(df_all: pd.DataFrame, whisper_model: str) -> Dict[str, Any]:
    semantic = load_semantic_scores(whisper_model)
    df = merge_invention_score(df_all, semantic)

    fc1_result = run_ab_variant(df, FEATURE_NAMES)
    fc1_plus_result = run_ab_variant(df, list(FEATURE_NAMES) + ["invention_score"])

    n_available = int(df.loc[df["split"] == "train", "semantic_available"].sum())
    n_train = int((df["split"] == "train").sum())

    return {
        "whisper_model": whisper_model,
        "fc1": fc1_result,
        "fc1_plus_invention": fc1_plus_result,
        "delta_auc_val": fc1_plus_result["auc_val"] - fc1_result["auc_val"],
        "delta_ba_val": fc1_plus_result["ba_val"] - fc1_result["ba_val"],
        "semantic_available_train": n_available,
        "semantic_available_train_total": n_train,
    }


# ---------------------------------------------------------------------------
# (2) Runtime fusion
# ---------------------------------------------------------------------------


def _safe_logit(p: np.ndarray) -> np.ndarray:
    return logit(np.clip(p, _PROB_EPS, 1.0 - _PROB_EPS))


def fused_probability(
    p_behavioral: np.ndarray, invention_score: np.ndarray, w: float, max_delta_p: float
) -> np.ndarray:
    """logit(p_final) = logit(p_behavioral) + w*(invention_score-0.5),
    clamped so the fused probability never strays more than max_delta_p
    from p_behavioral -- the semantic layer nudges, it never overrides."""
    p_final = expit(_safe_logit(p_behavioral) + w * (invention_score - 0.5))
    lo = np.clip(p_behavioral - max_delta_p, 0.0, 1.0)
    hi = np.clip(p_behavioral + max_delta_p, 0.0, 1.0)
    return np.clip(p_final, lo, hi)


def load_behavioral_calibration() -> Dict[str, Any]:
    if not CALIBRATION_PATH.is_file():
        raise FileNotFoundError(f"{CALIBRATION_PATH} not found -- run `python -m train.calibrate` (T018).")
    with CALIBRATION_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def behavioral_probabilities(
    df_all: pd.DataFrame, calibration: Dict[str, Any]
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """(p_behavioral_train_oof, y_train, p_behavioral_val, y_val) using the
    SHIPPED fc-1-only booster (model_lgbm.txt) and its calibration.json —
    never retrained here, this is the artifact T017/T018 already froze."""
    if not MODEL_PATH.is_file():
        raise FileNotFoundError(f"{MODEL_PATH} not found -- run `python -m train.train` (T017).")
    booster = lgb.Booster(model_file=str(MODEL_PATH))

    train_df = df_all[df_all["split"] == "train"].reset_index(drop=True)
    val_df = df_all[df_all["split"] == "val"].reset_index(drop=True)
    X_train = train_df[list(FEATURE_NAMES)]
    X_val = val_df[list(FEATURE_NAMES)]
    y_train = label_to_target(train_df["label"])
    y_val = label_to_target(val_df["label"])

    scale_pos_weight = compute_scale_pos_weight(y_train)
    oof_raw = compute_train_oof_scores(X_train, y_train, scale_pos_weight)
    val_raw = booster.predict(X_val)

    a, b = calibration["a"], calibration["b"]
    p_train_oof = apply_platt(oof_raw, a, b)
    p_val = apply_platt(val_raw, a, b)
    return p_train_oof, y_train, p_val, y_val


def select_w(
    p_behavioral_train: np.ndarray,
    invention_train: np.ndarray,
    y_train: np.ndarray,
    threshold: float,
    max_delta_p: float,
    w_grid: np.ndarray = _W_GRID,
) -> Tuple[float, float]:
    """(best_w, best_ba_train_oof) maximising the fused verdict's balanced
    accuracy on TRAIN out-of-fold predictions at the shipped threshold.
    w=0 (pure behavioral) is itself a grid point, so the search can never
    do worse than the pre-existing baseline it's compared against."""
    best_w, best_ba = 0.0, -1.0
    for w in w_grid:
        fused = fused_probability(p_behavioral_train, invention_train, float(w), max_delta_p)
        ba = balanced_accuracy(y_train, (fused >= threshold).astype(int))
        if ba > best_ba:
            best_w, best_ba = float(w), float(ba)
    return best_w, best_ba


def evaluate_fusion_for_whisper_model(
    df_all: pd.DataFrame,
    whisper_model: str,
    calibration: Dict[str, Any],
    max_delta_p: float = MAX_DELTA_P_DEFAULT,
) -> Dict[str, Any]:
    semantic = load_semantic_scores(whisper_model)
    df = merge_invention_score(df_all, semantic)

    p_behav_train, y_train, p_behav_val, y_val = behavioral_probabilities(df, calibration)
    threshold = float(calibration["threshold"])

    train_df = df[df["split"] == "train"].reset_index(drop=True)
    val_df = df[df["split"] == "val"].reset_index(drop=True)
    inv_train = train_df["invention_score"].to_numpy(dtype=np.float64)
    inv_val = val_df["invention_score"].to_numpy(dtype=np.float64)

    w, ba_train_oof_fused = select_w(p_behav_train, inv_train, y_train, threshold, max_delta_p)

    ba_val_behavioral = float(balanced_accuracy(y_val, (p_behav_val >= threshold).astype(int)))
    auc_val_behavioral = float(roc_auc_score(y_val, p_behav_val)) if len(set(y_val.tolist())) > 1 else 0.5
    brier_val_behavioral = float(brier_score(p_behav_val, y_val))

    p_fused_val = fused_probability(p_behav_val, inv_val, w, max_delta_p)
    ba_val_fused = float(balanced_accuracy(y_val, (p_fused_val >= threshold).astype(int)))
    auc_val_fused = float(roc_auc_score(y_val, p_fused_val)) if len(set(y_val.tolist())) > 1 else 0.5
    brier_val_fused = float(brier_score(p_fused_val, y_val))

    verdict_behav = (p_behav_val >= threshold).astype(int)
    verdict_fused = (p_fused_val >= threshold).astype(int)
    flip_fraction_val = float(np.mean(verdict_behav != verdict_fused)) if len(verdict_behav) else 0.0

    # Honest outcome rule: fused must beat behavioral-only on val, or w=0.
    honest_w = w
    honest_note = f"fused val BA ({ba_val_fused:.4f}) > behavioral-only val BA ({ba_val_behavioral:.4f})"
    if ba_val_fused <= ba_val_behavioral:
        honest_w = 0.0
        p_fused_val = p_behav_val
        ba_val_fused = ba_val_behavioral
        auc_val_fused = auc_val_behavioral
        brier_val_fused = brier_val_behavioral
        flip_fraction_val = 0.0
        honest_note = (
            f"fused val BA ({ba_val_fused:.4f}) did not exceed behavioral-only val BA "
            f"({ba_val_behavioral:.4f}) -- w forced to 0 (honest outcome rule); the layer "
            "stays visible in /analyze but never moves the verdict"
        )

    return {
        "whisper_model": whisper_model,
        "w_selected_on_train_oof": w,
        "w": honest_w,
        "max_delta_p": max_delta_p,
        "threshold": threshold,
        "ba_train_oof_fused": ba_train_oof_fused,
        "ba_val_behavioral": ba_val_behavioral,
        "ba_val_fused": ba_val_fused,
        "auc_val_behavioral": auc_val_behavioral,
        "auc_val_fused": auc_val_fused,
        "brier_val_behavioral": brier_val_behavioral,
        "brier_val_fused": brier_val_fused,
        "flip_fraction_val": flip_fraction_val,
        "honest_note": honest_note,
    }


# ---------------------------------------------------------------------------
# semantic_fusion.json schema (hand-rolled, matches calibrate.py's style)
# ---------------------------------------------------------------------------

SEMANTIC_FUSION_SCHEMA: Dict[str, Dict[str, Any]] = {
    "version": {"type": str, "enum": {FUSION_ARTIFACT_VERSION}},
    "w": {"type": (int, float)},
    "max_delta_p": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "whisper_model": {"type": str, "enum": set(WHISPER_MODELS)},
    "fitted_on": {"type": str, "enum": {"train_oof"}},
    "ba_val_behavioral": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
    "ba_val_fused": {"type": (int, float), "minimum": 0.0, "maximum": 1.0},
}


def validate_semantic_fusion_schema(obj: Dict[str, Any]) -> None:
    for key, rule in SEMANTIC_FUSION_SCHEMA.items():
        if key not in obj:
            raise ValueError(f"semantic_fusion.json missing required key: {key!r}")
        value = obj[key]
        if isinstance(value, bool) or not isinstance(value, rule["type"]):
            raise ValueError(f"semantic_fusion.json key {key!r} has wrong type: {type(value).__name__}")
        if "enum" in rule and value not in rule["enum"]:
            raise ValueError(f"semantic_fusion.json key {key!r} must be one of {rule['enum']}, got {value!r}")
        if "minimum" in rule and value < rule["minimum"]:
            raise ValueError(f"semantic_fusion.json key {key!r}={value} below minimum {rule['minimum']}")
        if "maximum" in rule and value > rule["maximum"]:
            raise ValueError(f"semantic_fusion.json key {key!r}={value} above maximum {rule['maximum']}")


def build_fusion_artifact(result: Dict[str, Any]) -> Dict[str, Any]:
    artifact = {
        "version": FUSION_ARTIFACT_VERSION,
        "w": result["w"],
        "max_delta_p": result["max_delta_p"],
        "whisper_model": result["whisper_model"],
        "fitted_on": "train_oof",
        "ba_val_behavioral": result["ba_val_behavioral"],
        "ba_val_fused": result["ba_val_fused"],
        # extra, non-frozen fields -- kept for /analyze explainability and
        # docs, never validated by SEMANTIC_FUSION_SCHEMA above.
        "auc_val_behavioral": result["auc_val_behavioral"],
        "auc_val_fused": result["auc_val_fused"],
        "brier_val_behavioral": result["brier_val_behavioral"],
        "brier_val_fused": result["brier_val_fused"],
        "flip_fraction_val": result["flip_fraction_val"],
        "w_selected_on_train_oof": result["w_selected_on_train_oof"],
        "honest_note": result["honest_note"],
    }
    validate_semantic_fusion_schema(artifact)
    return artifact


def save_fusion_artifact(artifact: Dict[str, Any], path: Path = FUSION_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(artifact, f, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


# ---------------------------------------------------------------------------
# AB_REPORT.md
# ---------------------------------------------------------------------------


def render_report(ab_results: Sequence[Dict[str, Any]], fusion_results: Sequence[Dict[str, Any]], chosen_model: str) -> str:
    lines: List[str] = []
    lines.append("# Semantic layer A/B report (T044)\n")
    lines.append(
        "FR-014: fc-1 alone vs fc-1 + `invention_score` (F-23 candidate), same T017 "
        "training protocol, evaluated once per whisper model (`tiny`, `base`) since "
        "the answer-turn transcript quality depends on which model transcribed it.\n"
    )

    lines.append("## (1) A/B — fc-1 vs fc-1 + invention_score\n")
    lines.append(
        "| whisper model | variant | CV AUC (train) | val AUC | val BA | val Brier |"
    )
    lines.append("|---|---|---|---|---|---|")
    for r in ab_results:
        m = r["whisper_model"]
        for variant_name, key in (("fc-1", "fc1"), ("fc-1 + invention_score", "fc1_plus_invention")):
            v = r[key]
            lines.append(
                f"| {m} | {variant_name} | {v['cv_auc_mean']:.4f} +/- {v['cv_auc_std']:.4f} "
                f"| {v['auc_val']:.4f} | {v['ba_val']:.4f} | {v['brier_val']:.4f} |"
            )
        lines.append(
            f"| {m} | **delta (plus - fc1)** | | {r['delta_auc_val']:+.4f} | {r['delta_ba_val']:+.4f} | |"
        )
    lines.append("")

    lines.append("## (2) Runtime fusion\n")
    lines.append(
        "`logit(p_final) = logit(p_behavioral) + w*(invention_score-0.5)`, clamped "
        f"to `|p_final - p_behavioral| <= max_delta_p` (default {MAX_DELTA_P_DEFAULT}). "
        "`w` chosen on TRAIN out-of-fold predictions to maximise balanced accuracy "
        "of the fused verdict at the shipped threshold; val numbers are reported, "
        "never used to pick `w` (ADR-012).\n"
    )
    lines.append(
        "| whisper model | w (train-OOF-optimal) | w (shipped, honest rule) | "
        "val BA behavioral | val BA fused | val AUC behavioral | val AUC fused | "
        "val Brier behavioral | val Brier fused | flip fraction (val) |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for r in fusion_results:
        lines.append(
            f"| {r['whisper_model']} | {r['w_selected_on_train_oof']:.2f} | {r['w']:.2f} | "
            f"{r['ba_val_behavioral']:.4f} | {r['ba_val_fused']:.4f} | "
            f"{r['auc_val_behavioral']:.4f} | {r['auc_val_fused']:.4f} | "
            f"{r['brier_val_behavioral']:.4f} | {r['brier_val_fused']:.4f} | "
            f"{r['flip_fraction_val']:.4f} |"
        )
    lines.append("")
    for r in fusion_results:
        lines.append(f"- **{r['whisper_model']}**: {r['honest_note']}")
    lines.append("")
    lines.append(
        f"**Model written to `semantic_fusion.json`: `{chosen_model}`** (higher fused val BA "
        "between tiny/base, ties broken toward base). This is provisional pending T057's "
        "server-side timing measurement for both whisper models — see docs/semantic-layer.md."
    )
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-delta-p", type=float, default=MAX_DELTA_P_DEFAULT)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    contract.assert_contract_matches_extractor()

    df_all = load_features()
    calibration = load_behavioral_calibration()

    ab_results = [run_ab_for_whisper_model(df_all, m) for m in WHISPER_MODELS]
    fusion_results = [
        evaluate_fusion_for_whisper_model(df_all, m, calibration, args.max_delta_p)
        for m in WHISPER_MODELS
    ]

    chosen = max(fusion_results, key=lambda r: (r["ba_val_fused"], r["whisper_model"] == "base"))
    chosen_model = chosen["whisper_model"]

    report = render_report(ab_results, fusion_results, chosen_model)
    AB_REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"wrote {AB_REPORT_PATH}")

    artifact = build_fusion_artifact(chosen)
    save_fusion_artifact(artifact, path=FUSION_PATH)
    print(f"wrote {FUSION_PATH}")
    print(f"chosen whisper_model={chosen_model} w={artifact['w']} "
          f"ba_val_behavioral={artifact['ba_val_behavioral']:.4f} ba_val_fused={artifact['ba_val_fused']:.4f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
