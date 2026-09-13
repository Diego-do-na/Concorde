# Semantic layer A/B report (T044)

FR-014: fc-1 alone vs fc-1 + `invention_score` (F-23 candidate), same T017 training protocol, evaluated once per whisper model (`tiny`, `base`) since the answer-turn transcript quality depends on which model transcribed it.

## (1) A/B — fc-1 vs fc-1 + invention_score

| whisper model | variant | CV AUC (train) | val AUC | val BA | val Brier |
|---|---|---|---|---|---|
| tiny | fc-1 | 0.9654 +/- 0.0197 | 0.9436 | 0.8466 | 0.0936 |
| tiny | fc-1 + invention_score | 0.9669 +/- 0.0184 | 0.9444 | 0.8331 | 0.0962 |
| tiny | **delta (plus - fc1)** | | +0.0008 | -0.0135 | |
| base | fc-1 | 0.9654 +/- 0.0197 | 0.9436 | 0.8466 | 0.0936 |
| base | fc-1 + invention_score | 0.9662 +/- 0.0185 | 0.9467 | 0.8478 | 0.0936 |
| base | **delta (plus - fc1)** | | +0.0032 | +0.0012 | |

## (2) Runtime fusion

`logit(p_final) = logit(p_behavioral) + w*(invention_score-0.5)`, clamped to `|p_final - p_behavioral| <= max_delta_p` (default 0.15). `w` chosen on TRAIN out-of-fold predictions to maximise balanced accuracy of the fused verdict at the shipped threshold; val numbers are reported, never used to pick `w` (ADR-012).

| whisper model | w (train-OOF-optimal) | w (shipped, honest rule) | val BA behavioral | val BA fused | val AUC behavioral | val AUC fused | val Brier behavioral | val Brier fused | flip fraction (val) |
|---|---|---|---|---|---|---|---|---|---|
| tiny | 0.50 | 0.00 | 0.8466 | 0.8466 | 0.9436 | 0.9436 | 0.0936 | 0.0936 | 0.0000 |
| base | -0.80 | -0.80 | 0.8466 | 0.8601 | 0.9436 | 0.9420 | 0.0936 | 0.0942 | 0.0141 |

- **tiny**: fused val BA (0.8466) did not exceed behavioral-only val BA (0.8466) -- w forced to 0 (honest outcome rule); the layer stays visible in /analyze but never moves the verdict
- **base**: fused val BA (0.8601) > behavioral-only val BA (0.8466)

**Model written to `semantic_fusion.json`: `base`** (higher fused val BA between tiny/base, ties broken toward base). This is provisional pending T057's server-side timing measurement for both whisper models — see docs/semantic-layer.md.
