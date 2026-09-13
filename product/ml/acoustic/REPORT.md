Acoustic sub-signal training & validation report
===============================================

This document records the build/validation of the compact 8-dim acoustic
sub-signal and the tiny LightGBM trained on it. It is intentionally
concise — see the code in `product/ml/acoustic/` for exact reproducibility.

Validation checklist
- Parity test of descriptor calculation on 5 golden calls: target max-abs-error <= 1e-4 (TODO: run)
- Val AUC of the acoustic-only model: (TODO: report once training run completes)
- ElevenLabs generalisation check: (TODO: report model score on provided ElevenLabs clips)

Files produced by the pipeline (local, gitignored):
- `ml/data/acoustic_model_lgbm.txt` — LightGBM textual booster (train-only)
- `product/artifacts/acoustic.onnx.meta.json` — sidecar meta describing feature names/means/stds/importances

Notes
- Deliberately low-dimensional to limit TTS-engine memorisation.
- This artifact is an optional third vote. `/detect` remains behavioral-only
  unless explicit approval is given to fuse acoustic+behavioral in production.

