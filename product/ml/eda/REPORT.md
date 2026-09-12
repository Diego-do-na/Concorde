# CONCORDE — EDA Gates Report (spec §10.1)

Dataset dir: `/home/paul/hackmty26/hackmty26`

## Gate 1 — class balance per split

| split | n | human | synthetic | % human | % synthetic |
|---|---|---|---|---|---|
| train | 282 | 113 | 169 | 40.1 | 59.9 |
| val | 71 | 37 | 34 | 52.1 | 47.9 |

Beyond 60/40 in any split (literal gate): **False**.
Apply class_weight: **True**.
Decision: apply class_weight in training (train sits on/over the 60/40 boundary).

## Gate 2 — overlap-event density (R-04)

| population | fraction with >=1 overlap event | median overlap events |
|---|---|---|
| overall | 94.6% | 4.0 |
| human | 98.0% | 6.5 |
| synthetic | 92.1% | 3.0 |

R-04 gate (< 40% of calls with overlap): **not triggered**.
Decision: R-04 NOT triggered: F-07..F-13 keep full weight.

## Gate 3 — duration vs label

Mean duration — human: 149.9 s, synthetic: 146.5 s.
Point-biserial correlation (duration, is_synthetic): -0.056.
AUC of duration alone: 0.475 (discriminative power 0.525).
Duration-as-shortcut gate (discriminative power >= 0.6): **not triggered**.
Decision: duration does not leak the label; excluded anyway — not in fc-1.

## Gate 4 — manifest hash + WAV format census

`manifest.csv` sha256: `4fa5ac3f25f2bc1fbff9a06a89f621fcca30db5f1443bbd164368193f1d7c544`

WAV files checked: 353

| (channels, sample_rate_hz, sample_width_bytes) | count |
|---|---|
| (2, 8000, 2) | 353 |

All stereo/8 kHz/16-bit: **True**.

speaker-id column present in manifest.csv: **False**. No speaker identifier exists in this dataset, so ADR-012's "speaker-grouped CV inside train" is not literally implementable. Fallback for T016/T017: stratified K-fold by anon_id inside train; val remains the only speaker-disjoint measurement — never approximate grouping by clustering voices (that would itself be a form of speaker identification, forbidden by §13.4/NFR-011).

