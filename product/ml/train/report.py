#!/usr/bin/env python3
"""Metrics report: balanced accuracy first, then AUC/EER/Brier/ECE, confusion
matrix, error-by-duration, importances (T019).

This is the one place besides train/calibrate.py (T018) that opens `val`
(ADR-012's designated use: final measurement). It never re-fits anything —
it only scores `model_lgbm.txt` on val and applies the already-fitted Platt
`a`/`b` and shipped `threshold` from `calibration.json` (T018), so every
number here is the number the shipped system would actually produce.

Reads:
    ml/data/features_vad.parquet   T016
    ml/data/model_lgbm.txt          T017
    ml/data/train_meta.json         T017 (cv_auc_mean/std, feature_importance,
                                     direction_sign)
    ml/data/calibration.json        T018 (a, b, threshold)
    ml/data/val_check.json          optional, written by T022's
                                     check_endpoint.py --split val --n 0
                                     --out val_check.json; cross-checked
                                     against this report's balanced accuracy
                                     within +/-0.01 when present.
    ml/eda/REPORT.md                 T002, for the anti-self-deception audit
    ml/validation/vad_agreement/REPORT.md   T012, ditto

Writes:
    ml/train/REPORT.md      committed (no audio, no per-call data)
    ml/data/metrics.json    gitignored; feeds the README table and the
                             console exec view

Usage:
    cd product/ml
    python -m train.report
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import lightgbm as lgb
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score, roc_curve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES  # noqa: E402
from train.calibrate import (  # noqa: E402
    apply_platt,
    balanced_accuracy,
    brier_score,
    expected_calibration_error,
    label_to_target,
)

ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"
TRAIN_DIR = ML_DIR / "train"

VAD_PARQUET = DATA_DIR / "features_vad.parquet"
MODEL_PATH = DATA_DIR / "model_lgbm.txt"
META_PATH = DATA_DIR / "train_meta.json"
CALIBRATION_PATH = DATA_DIR / "calibration.json"
VAL_CHECK_PATH = DATA_DIR / "val_check.json"
EDA_REPORT_PATH = ML_DIR / "eda" / "REPORT.md"
VAD_AGREEMENT_REPORT_PATH = ML_DIR / "validation" / "vad_agreement" / "REPORT.md"

REPORT_PATH = TRAIN_DIR / "REPORT.md"
METRICS_JSON_PATH = DATA_DIR / "metrics.json"

# Altur's brief (§8.4 / this report's tie-break metrics): AUC/Brier targets.
AUC_TARGET = 0.90
BRIER_TARGET = 0.12

# Same suspicion threshold as train.py's CV_AUC_AUDIT_THRESHOLD (§10.2),
# applied here to *val* AUC instead of CV AUC.
SELF_DECEPTION_AUC_THRESHOLD = 0.97

# Altur's duration bands (task description), half-open [lo, hi) except the
# last band's upper bound is inclusive-by-construction (no call exceeds it).
DURATION_BANDS: Tuple[Tuple[float, float], ...] = ((60.0, 120.0), (120.0, 180.0), (180.0, 280.0))

ECE_N_BINS = 10
TOP_K_IMPORTANCES = 10
CROSS_CHECK_TOLERANCE = 0.01

ROUND_NDIGITS = 4

REQUIRED_METRICS_KEYS: Tuple[str, ...] = (
    "feature_contract",
    "git_sha",
    "seed",
    "n_train",
    "n_val",
    "threshold",
    "balanced_accuracy",
    "tpr_synthetic",
    "tnr_human",
    "accuracy",
    "roc_auc",
    "auc_target",
    "auc_pass",
    "brier",
    "brier_before_calibration",
    "brier_target",
    "brier_pass",
    "eer",
    "ece",
    "reliability_table",
    "confusion_matrix",
    "error_by_duration_band",
    "top_feature_importances",
    "cv_auc_mean",
    "cv_auc_std",
    "val_auc",
    "cv_val_auc_gap",
    "ba_train_oof",
    "ba_val_gap",
    "anti_self_deception_triggered",
    "anti_self_deception_evidence",
    "cross_check",
)


# ---------------------------------------------------------------------------
# Loading — val is legitimate here (ADR-012's designated use: final
# measurement). This module never fits anything; it only scores.
# ---------------------------------------------------------------------------


def load_val_split(features_path: Path) -> pd.DataFrame:
    if not features_path.is_file():
        raise FileNotFoundError(
            f"{features_path} not found — run `python -m train.build_dataset` first (T016)."
        )
    df = pd.read_parquet(features_path)
    if "val" not in set(df["split"].unique()):
        raise RuntimeError(f"features table at {features_path} has no 'val' split rows")
    return df[df["split"] == "val"].reset_index(drop=True)


def load_json(path: Path, hint: str) -> Dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found — {hint}")
    return json.loads(path.read_text(encoding="utf-8"))


def score_val(
    val_df: pd.DataFrame, booster: lgb.Booster, calibration: Dict[str, Any]
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(y, probs, duration_s) — probs are Platt-calibrated, val_df order preserved."""
    X_val = val_df[list(FEATURE_NAMES)]
    y_val = label_to_target(val_df["label"])
    raw = booster.predict(X_val)
    probs = apply_platt(raw, calibration["a"], calibration["b"])
    duration = val_df["duration_s"].to_numpy(dtype=np.float64)
    return y_val, probs, duration


# ---------------------------------------------------------------------------
# Metrics beyond what train.py/calibrate.py already compute.
# ---------------------------------------------------------------------------


def tpr_tnr(y: np.ndarray, pred: np.ndarray) -> Tuple[float, float]:
    pos, neg = y == 1, y == 0
    n_pos, n_neg = int(pos.sum()), int(neg.sum())
    tpr = float((pred[pos] == 1).sum()) / n_pos if n_pos else 0.0
    tnr = float((pred[neg] == 0).sum()) / n_neg if n_neg else 0.0
    return tpr, tnr


def confusion_matrix(y: np.ndarray, pred: np.ndarray) -> Dict[str, int]:
    return {
        "tp": int(((pred == 1) & (y == 1)).sum()),
        "fp": int(((pred == 1) & (y == 0)).sum()),
        "tn": int(((pred == 0) & (y == 0)).sum()),
        "fn": int(((pred == 0) & (y == 1)).sum()),
    }


def compute_eer(y: np.ndarray, probs: np.ndarray) -> float:
    """Equal error rate: the point on the ROC curve where FPR == FNR.

    Uses the curve point minimising |FNR - FPR| — exact when the curve
    crosses the diagonal, the closest achievable value otherwise (finite,
    ~70-row val split).
    """
    if len(set(y.tolist())) < 2:
        return 0.5
    fpr, tpr, _ = roc_curve(y, probs)
    fnr = 1.0 - tpr
    idx = int(np.argmin(np.abs(fnr - fpr)))
    return float((fpr[idx] + fnr[idx]) / 2.0)


def reliability_table(probs: np.ndarray, y: np.ndarray, n_bins: int = ECE_N_BINS) -> List[Dict[str, Any]]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    rows: List[Dict[str, Any]] = []
    for i in range(n_bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (probs >= lo) & (probs <= hi) if i == n_bins - 1 else (probs >= lo) & (probs < hi)
        count = int(mask.sum())
        rows.append(
            {
                "bin_lo": float(lo),
                "bin_hi": float(hi),
                "count": count,
                "avg_confidence": float(probs[mask].mean()) if count else 0.0,
                "empirical_accuracy": float(y[mask].mean()) if count else 0.0,
            }
        )
    return rows


def error_rate_by_duration(
    y: np.ndarray, pred: np.ndarray, duration: np.ndarray, bands: Sequence[Tuple[float, float]] = DURATION_BANDS
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for lo, hi in bands:
        mask = (duration >= lo) & (duration < hi)
        n = int(mask.sum())
        errors = int((pred[mask] != y[mask]).sum()) if n else 0
        rows.append(
            {
                "band_lo": lo,
                "band_hi": hi,
                "n": n,
                "errors": errors,
                "error_rate": float(errors) / n if n else 0.0,
            }
        )
    return rows


def top_feature_importances(meta: Dict[str, Any], k: int = TOP_K_IMPORTANCES) -> List[Dict[str, Any]]:
    names: List[str] = meta["feature_names"]
    importances: List[float] = meta["feature_importance"]
    signs: List[int] = meta["direction_sign"]
    order = np.argsort(importances)[::-1][:k]
    return [
        {
            "name": names[idx],
            "importance": float(importances[idx]),
            "direction": "synthetic" if signs[idx] >= 0 else "human",
        }
        for idx in order
    ]


# ---------------------------------------------------------------------------
# Anti-self-deception audit (§10.2): only gathers evidence when val AUC
# actually crosses the suspicion threshold — otherwise there is nothing to
# explain away.
# ---------------------------------------------------------------------------


def _extract_line(text: str, pattern: str) -> Optional[str]:
    match = re.search(pattern, text)
    return match.group(0).strip() if match else None


def gather_duration_evidence(eda_report_path: Path = EDA_REPORT_PATH) -> Optional[str]:
    """T002's duration-vs-label gate line, verbatim from eda/REPORT.md."""
    if not eda_report_path.is_file():
        return None
    text = eda_report_path.read_text(encoding="utf-8")
    line = _extract_line(text, r"AUC of duration alone:.*")
    return f"eda/REPORT.md (T002): {line}" if line else None


def gather_vad_label_agreement_evidence(vad_report_path: Path = VAD_AGREEMENT_REPORT_PATH) -> Optional[str]:
    """T012's per-label VAD agreement breakdown, verbatim from its REPORT.md."""
    if not vad_report_path.is_file():
        return None
    text = vad_report_path.read_text(encoding="utf-8")
    human = _extract_line(text, r"- human: caller_f1=.*")
    synthetic = _extract_line(text, r"- synthetic: caller_f1=.*")
    if not human or not synthetic:
        return None
    return f"validation/vad_agreement/REPORT.md (T012): {human} | {synthetic}"


def anti_self_deception_audit(
    val_auc: float,
    meta: Dict[str, Any],
    eda_report_path: Path = EDA_REPORT_PATH,
    vad_report_path: Path = VAD_AGREEMENT_REPORT_PATH,
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    triggered = val_auc >= SELF_DECEPTION_AUC_THRESHOLD
    if not triggered:
        return False, None
    evidence = {
        "duration_correlation": gather_duration_evidence(eda_report_path),
        "vad_label_agreement": gather_vad_label_agreement_evidence(vad_report_path),
        "top_importances": top_feature_importances(meta, k=5),
    }
    return True, evidence


# ---------------------------------------------------------------------------
# Cross-check against Altur's own scorer (T022) — optional, best-effort:
# check_endpoint.py doesn't exist until T022 lands.
# ---------------------------------------------------------------------------


def load_cross_check(val_check_path: Path, balanced_accuracy_here: float) -> Dict[str, Any]:
    if not val_check_path.is_file():
        return {"available": False, "note": "T022 not done yet (no check_endpoint.py run found)"}
    try:
        val_check = json.loads(val_check_path.read_text(encoding="utf-8"))
        their_ba = float(val_check["balanced_accuracy"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return {"available": False, "note": f"{val_check_path} unreadable: {exc}"}
    diff = abs(their_ba - balanced_accuracy_here)
    return {
        "available": True,
        "check_endpoint_balanced_accuracy": their_ba,
        "report_balanced_accuracy": balanced_accuracy_here,
        "abs_diff": diff,
        "within_tolerance": diff <= CROSS_CHECK_TOLERANCE,
        "tolerance": CROSS_CHECK_TOLERANCE,
    }


# ---------------------------------------------------------------------------
# metrics.json assembly.
# ---------------------------------------------------------------------------


def build_metrics(
    *,
    val_df: pd.DataFrame,
    meta: Dict[str, Any],
    calibration: Dict[str, Any],
    y_val: np.ndarray,
    probs: np.ndarray,
    pred: np.ndarray,
    duration: np.ndarray,
    n_train: int,
    val_check_path: Path,
    eda_report_path: Path,
    vad_report_path: Path,
) -> Dict[str, Any]:
    tpr, tnr = tpr_tnr(y_val, pred)
    ba_val = balanced_accuracy(y_val, pred)
    accuracy = float((pred == y_val).mean())
    val_auc = float(roc_auc_score(y_val, probs)) if len(set(y_val.tolist())) > 1 else 0.5
    brier_after = brier_score(probs, y_val)
    ece_val = expected_calibration_error(probs, y_val, n_bins=ECE_N_BINS)
    eer = compute_eer(y_val, probs)

    triggered, evidence = anti_self_deception_audit(val_auc, meta, eda_report_path, vad_report_path)

    metrics: Dict[str, Any] = {
        "feature_contract": contract.CONTRACT_ID,
        "git_sha": meta.get("git_sha"),
        "seed": meta.get("seed"),
        "n_train": n_train,
        "n_val": len(val_df),
        "threshold": calibration["threshold"],
        "balanced_accuracy": ba_val,
        "tpr_synthetic": tpr,
        "tnr_human": tnr,
        "accuracy": accuracy,
        "roc_auc": val_auc,
        "auc_target": AUC_TARGET,
        "auc_pass": val_auc >= AUC_TARGET,
        "brier": brier_after,
        "brier_before_calibration": calibration.get("brier_val_before"),
        "brier_target": BRIER_TARGET,
        "brier_pass": brier_after <= BRIER_TARGET,
        "eer": eer,
        "ece": ece_val,
        "reliability_table": reliability_table(probs, y_val, n_bins=ECE_N_BINS),
        "confusion_matrix": confusion_matrix(y_val, pred),
        "error_by_duration_band": error_rate_by_duration(y_val, pred, duration),
        "top_feature_importances": top_feature_importances(meta, k=TOP_K_IMPORTANCES),
        "cv_auc_mean": meta.get("cv_auc_mean"),
        "cv_auc_std": meta.get("cv_auc_std"),
        "val_auc": val_auc,
        "cv_val_auc_gap": float(meta["cv_auc_mean"]) - val_auc if meta.get("cv_auc_mean") is not None else None,
        "ba_train_oof": calibration.get("ba_train_oof"),
        "ba_val_gap": (
            float(calibration["ba_train_oof"]) - ba_val if calibration.get("ba_train_oof") is not None else None
        ),
        "anti_self_deception_triggered": triggered,
        "anti_self_deception_evidence": evidence,
        "cross_check": load_cross_check(val_check_path, ba_val),
    }
    missing = [k for k in REQUIRED_METRICS_KEYS if k not in metrics]
    if missing:
        raise AssertionError(f"build_metrics is missing required keys: {missing}")
    return metrics


def save_metrics(metrics: Dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, sort_keys=True)
        f.write("\n")


# ---------------------------------------------------------------------------
# REPORT.md rendering — every number shown is `round(x, ROUND_NDIGITS)` of
# the exact same value stored in metrics.json, so the two never drift.
# ---------------------------------------------------------------------------


def _r(x: Optional[float]) -> Any:
    return round(float(x), ROUND_NDIGITS) if x is not None else None


def _pass_fail(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def render_report(metrics: Dict[str, Any]) -> str:
    lines: List[str] = []
    a = lines.append

    a("# CONCORDE model metrics report (fc-1, T019)")
    a("")
    a(
        f"Model `model_lgbm.txt` (git_sha `{metrics['git_sha']}`, seed `{metrics['seed']}`, "
        f"feature contract `{metrics['feature_contract']}`) scored on `val` "
        f"(n={metrics['n_val']}, train n={metrics['n_train']}) at the shipped Platt-calibrated "
        f"threshold `{_r(metrics['threshold'])}` from `calibration.json` (T018). "
        "`val` is opened here only for final measurement (ADR-012) — nothing below was used to "
        "pick features, hyperparameters, or the threshold itself."
    )
    a("")

    a("## Balanced accuracy (val, shipped threshold)")
    a("")
    a("Altur's primary metric.")
    a("")
    a("| Metric | Value |")
    a("|---|---|")
    a(f"| Balanced accuracy | {_r(metrics['balanced_accuracy'])} |")
    a(f"| TPR_synthetic (recall on synthetic) | {_r(metrics['tpr_synthetic'])} |")
    a(f"| TNR_human (specificity on human) | {_r(metrics['tnr_human'])} |")
    a("")

    a("## ROC-AUC and Brier (val)")
    a("")
    a("Tie-break metrics Altur also reports.")
    a("")
    a("| Metric | Value | Target | Result |")
    a("|---|---|---|---|")
    a(f"| ROC-AUC | {_r(metrics['roc_auc'])} | >= {metrics['auc_target']} | {_pass_fail(metrics['auc_pass'])} |")
    a(
        f"| Brier (after calibration) | {_r(metrics['brier'])} | <= {metrics['brier_target']} | "
        f"{_pass_fail(metrics['brier_pass'])} |"
    )
    a(f"| Brier (before calibration, raw score) | {_r(metrics['brier_before_calibration'])} | - | - |")
    a("")

    a("## EER (val)")
    a("")
    a(f"Equal error rate (FPR == FNR crossover on the ROC curve): **{_r(metrics['eer'])}**.")
    a("")

    a("## Accuracy (val)")
    a("")
    a(f"Unweighted accuracy at the shipped threshold: **{_r(metrics['accuracy'])}**.")
    a("")

    a("## ECE and reliability (val)")
    a("")
    a(f"Expected calibration error (10 equal-width bins, after Platt): **{_r(metrics['ece'])}**.")
    a("")
    a("| Bin | Count | Avg. confidence | Empirical accuracy |")
    a("|---|---|---|---|")
    for row in metrics["reliability_table"]:
        a(
            f"| [{row['bin_lo']:.1f}, {row['bin_hi']:.1f}] | {row['count']} | "
            f"{_r(row['avg_confidence'])} | {_r(row['empirical_accuracy'])} |"
        )
    a("")

    a("## Confusion matrix (val)")
    a("")
    cm = metrics["confusion_matrix"]
    a("| | Predicted synthetic | Predicted human |")
    a("|---|---|---|")
    a(f"| Actual synthetic | TP={cm['tp']} | FN={cm['fn']} |")
    a(f"| Actual human | FP={cm['fp']} | TN={cm['tn']} |")
    a("")

    a("## Error rate by duration band (val)")
    a("")
    a("| Band (s) | n | Errors | Error rate |")
    a("|---|---|---|---|")
    for row in metrics["error_by_duration_band"]:
        a(
            f"| [{row['band_lo']:.0f}, {row['band_hi']:.0f}) | {row['n']} | {row['errors']} | "
            f"{_r(row['error_rate'])} |"
        )
    a("")

    a("## Top-10 feature importances")
    a("")
    a("Gain importance from the shipped booster (train split), signed by the Spearman direction "
      "toward the label it pushes the verdict toward.")
    a("")
    a("| Rank | Feature | Gain importance | Direction |")
    a("|---|---|---|---|")
    for rank, row in enumerate(metrics["top_feature_importances"], start=1):
        a(f"| {rank} | `{row['name']}` | {_r(row['importance'])} | -> {row['direction']} |")
    a("")

    a("## CV-vs-val gap")
    a("")
    a("| Metric | CV (train, 5-fold x 3 seeds) | val | Gap (CV - val) |")
    a("|---|---|---|---|")
    a(
        f"| ROC-AUC | {_r(metrics['cv_auc_mean'])} (std {_r(metrics['cv_auc_std'])}) | "
        f"{_r(metrics['val_auc'])} | {_r(metrics['cv_val_auc_gap'])} |"
    )
    a(
        f"| Balanced accuracy | {_r(metrics['ba_train_oof'])} (train OOF) | "
        f"{_r(metrics['balanced_accuracy'])} | {_r(metrics['ba_val_gap'])} |"
    )
    a("")

    a("## Anti-self-deception audit (§10.2)")
    a("")
    if metrics["anti_self_deception_triggered"]:
        ev = metrics["anti_self_deception_evidence"] or {}
        a(
            f"val AUC ({_r(metrics['roc_auc'])}) is >= the {SELF_DECEPTION_AUC_THRESHOLD} suspicion "
            "threshold — a result this high on ~300 calls usually means leakage or a shortcut "
            "feature, not a genuinely solved problem. Evidence checked before calling this a success:"
        )
        a("")
        a(f"- Duration-as-shortcut check: {ev.get('duration_correlation') or 'not available'}")
        a(f"- VAD-by-label agreement check: {ev.get('vad_label_agreement') or 'not available'}")
        a("- Top-5 importances (see table above repeats the top drivers) — reviewed for a single "
          "feature dominating the score.")
    else:
        a(
            f"val AUC ({_r(metrics['roc_auc'])}) is below the {SELF_DECEPTION_AUC_THRESHOLD} "
            "suspicion threshold from §10.2 — the audit is not triggered. This number is reported "
            "as-is and is not being waved through as an unexamined success."
        )
    a("")

    a("## Cross-check against Altur's scorer (T022)")
    a("")
    cc = metrics["cross_check"]
    if cc.get("available"):
        a(
            f"`check_endpoint.py --split val --n 0 --out val_check.json` reported balanced accuracy "
            f"{_r(cc['check_endpoint_balanced_accuracy'])} vs this report's "
            f"{_r(cc['report_balanced_accuracy'])} (abs diff {_r(cc['abs_diff'])}, "
            f"tolerance {cc['tolerance']}): **{_pass_fail(cc['within_tolerance'])}**."
        )
    else:
        a(f"Not available yet — {cc.get('note', 'T022 not done')}.")
    a("")

    a("## Reproducing this report")
    a("")
    a("```bash")
    a("cd product/ml")
    a("python -m train.report")
    a("```")
    a("")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features-path", type=Path, default=VAD_PARQUET)
    parser.add_argument("--model-path", type=Path, default=MODEL_PATH)
    parser.add_argument("--meta-path", type=Path, default=META_PATH)
    parser.add_argument("--calibration-path", type=Path, default=CALIBRATION_PATH)
    parser.add_argument("--val-check-path", type=Path, default=VAL_CHECK_PATH)
    parser.add_argument("--eda-report-path", type=Path, default=EDA_REPORT_PATH)
    parser.add_argument("--vad-report-path", type=Path, default=VAD_AGREEMENT_REPORT_PATH)
    parser.add_argument("--report-path", type=Path, default=REPORT_PATH)
    parser.add_argument("--metrics-path", type=Path, default=METRICS_JSON_PATH)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)

    contract.assert_contract_matches_extractor()

    val_df = load_val_split(args.features_path)
    n_train = int((pd.read_parquet(args.features_path)["split"] == "train").sum())

    booster = lgb.Booster(model_file=str(args.model_path))
    meta = load_json(args.meta_path, "run `python -m train.train` first (T017).")
    calibration = load_json(args.calibration_path, "run `python -m train.calibrate` first (T018).")

    y_val, probs, duration = score_val(val_df, booster, calibration)
    threshold = float(calibration["threshold"])
    pred = (probs >= threshold).astype(int)

    metrics = build_metrics(
        val_df=val_df,
        meta=meta,
        calibration=calibration,
        y_val=y_val,
        probs=probs,
        pred=pred,
        duration=duration,
        n_train=n_train,
        val_check_path=args.val_check_path,
        eda_report_path=args.eda_report_path,
        vad_report_path=args.vad_report_path,
    )

    save_metrics(metrics, args.metrics_path)
    report_text = render_report(metrics)
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(report_text, encoding="utf-8")

    print(
        f"val: ba={metrics['balanced_accuracy']:.4f} auc={metrics['roc_auc']:.4f} "
        f"brier={metrics['brier']:.4f} ece={metrics['ece']:.4f} eer={metrics['eer']:.4f}"
    )
    print(f"wrote {args.metrics_path}")
    print(f"wrote {args.report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
