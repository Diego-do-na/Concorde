# CONCORDE model metrics report (fc-1, T019)

Model `model_lgbm.txt` (git_sha `5d539a8f32752f2d6892b01742a89e4f108f54b8`, seed `20260912`, feature contract `fc-1`) scored on `val` (n=71, train n=282) at the shipped Platt-calibrated threshold `0.4198` from `calibration.json` (T018). `val` is opened here only for final measurement (ADR-012) — nothing below was used to pick features, hyperparameters, or the threshold itself.

## Balanced accuracy (val, shipped threshold)

Altur's primary metric.

| Metric | Value |
|---|---|
| Balanced accuracy | 0.8466 |
| TPR_synthetic (recall on synthetic) | 0.8824 |
| TNR_human (specificity on human) | 0.8108 |

## ROC-AUC and Brier (val)

Tie-break metrics Altur also reports.

| Metric | Value | Target | Result |
|---|---|---|---|
| ROC-AUC | 0.9436 | >= 0.9 | PASS |
| Brier (after calibration) | 0.0936 | <= 0.12 | PASS |
| Brier (before calibration, raw score) | 0.0941 | - | - |

## EER (val)

Equal error rate (FPR == FNR crossover on the ROC curve): **0.1129**.

## Accuracy (val)

Unweighted accuracy at the shipped threshold: **0.8451**.

## ECE and reliability (val)

Expected calibration error (10 equal-width bins, after Platt): **0.0965**.

| Bin | Count | Avg. confidence | Empirical accuracy |
|---|---|---|---|
| [0.0, 0.1] | 24 | 0.0468 | 0.0 |
| [0.1, 0.2] | 5 | 0.1256 | 0.2 |
| [0.2, 0.3] | 1 | 0.2451 | 1.0 |
| [0.3, 0.4] | 2 | 0.3696 | 0.5 |
| [0.4, 0.5] | 4 | 0.4429 | 0.25 |
| [0.5, 0.6] | 1 | 0.5383 | 0.0 |
| [0.6, 0.7] | 3 | 0.6723 | 1.0 |
| [0.7, 0.8] | 4 | 0.7453 | 0.5 |
| [0.8, 0.9] | 15 | 0.863 | 0.9333 |
| [0.9, 1.0] | 12 | 0.918 | 0.9167 |

## Confusion matrix (val)

| | Predicted synthetic | Predicted human |
|---|---|---|
| Actual synthetic | TP=30 | FN=4 |
| Actual human | FP=7 | TN=30 |

## Error rate by duration band (val)

| Band (s) | n | Errors | Error rate |
|---|---|---|---|
| [60, 120) | 9 | 4 | 0.4444 |
| [120, 180) | 51 | 6 | 0.1176 |
| [180, 280) | 11 | 1 | 0.0909 |

## Top-10 feature importances

Gain importance from the shipped booster (train split), signed by the Spearman direction toward the label it pushes the verdict toward.

| Rank | Feature | Gain importance | Direction |
|---|---|---|---|
| 1 | `silence_break_delay_mean` | 433.7558 | -> synthetic |
| 2 | `silence_break_delay_cv` | 420.0626 | -> synthetic |
| 3 | `turn_count_caller` | 382.8858 | -> human |
| 4 | `resp_latency_median` | 138.7038 | -> synthetic |
| 5 | `fragmentation_rate` | 125.1036 | -> human |
| 6 | `caller_turn_dur_std` | 103.0353 | -> synthetic |
| 7 | `short_turn_ratio` | 97.7012 | -> human |
| 8 | `speech_balance` | 80.6705 | -> human |
| 9 | `caller_turn_dur_cv` | 75.1718 | -> human |
| 10 | `caller_bargein_count` | 67.9075 | -> human |

## CV-vs-val gap

| Metric | CV (train, 5-fold x 3 seeds) | val | Gap (CV - val) |
|---|---|---|---|
| ROC-AUC | 0.9654 (std 0.0197) | 0.9436 | 0.0218 |
| Balanced accuracy | 0.9262 (train OOF) | 0.8466 | 0.0796 |

## Anti-self-deception audit (§10.2)

val AUC (0.9436) is below the 0.97 suspicion threshold from §10.2 — the audit is not triggered. This number is reported as-is and is not being waved through as an unexamined success.

## Cross-check against Altur's scorer (T022)

Not available yet — T022 not done yet (no check_endpoint.py run found).

## Reproducing this report

```bash
cd product/ml
python -m train.report
```

