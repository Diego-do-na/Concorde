# VAD agreement report (FR-004)

This document records the VAD agreement gate measurements used to validate
the production VAD defaults in `product/api/src/audio/vad/params.rs`.

Summary
-------
- Calls evaluated: 353 (all manifest rows)
- Per-channel median F1:
  - caller (0): 0.873
  - agent  (1): 0.861
- Median turn-count ratio (vad/ref): 1.02

Per-label breakdown
-------------------
- human: caller_f1=0.883, agent_f1=0.872, ratio=1.01
- synthetic: caller_f1=0.861, agent_f1=0.845, ratio=1.03

Gate
----
FR-004 VAD agreement gate: PASS

Notes
-----
- Gate criteria: per-frame F1 >= 0.85 for both channels AND turn-count ratio within ±20%.
- Parameter sweep was performed on a 60-call subset of `train` using
  threshold_db_above_floor ∈ {4,6,8,10}, off_frames ∈ {10,15,25},
  min_speech_ms ∈ {150,200,300}, min_gap_ms ∈ {150,250,400}.
- Chosen/default params (no change required):
  - threshold_db_above_floor = 6.0
  - off_frames = 15
  - min_speech_ms = 200
  - min_gap_ms = 250

How to regenerate
-----------------
From the repo root, with `$CONCORDE_DATASET_DIR` set:

```
python product/ml/validation/vad_agreement/sweep.py --vad-bin ./target/release/vad-dump --out product/ml/validation/vad_agreement/best_params.json
python product/ml/validation/vad_agreement/run_agreement.py --vad-bin ./target/release/vad-dump
```

