# artifacts/ — versioned model deliverables (ADR-004, NFR-009)

Unlike `ml/data/` (gitignored, AGENTS.md/NFR-011), this directory's
contents are the actual served model and **are** committed — they contain
no audio, no per-call features, and no speaker information, only a trained
classifier and its provenance metadata.

## Files

- **`feature_contract_fc-1.json`** — the frozen fc-1 feature name/order
  (§9). Both the Python (`ml/features/extract.py`) and Rust
  (`api/src/features/`) extractors are asserted against this file; it is
  the single source of truth for the vector's shape.
- **`model.onnx`** — the currently-shipped model: a LightGBM binary
  classifier converted to ONNX, exposing a single output — `P(class=1)`
  ("synthetic", ADR-006) — as a `(N,)` float tensor for a `(N, 23)` float
  input. Produced by `ml/export/export_onnx.py` (T021).
- **`model.onnx.meta.json`** — the sidecar the API's `Model::load` reads
  alongside `model.onnx` and validates before it will serve a request
  (FR-006: startup aborts on any `feature_contract`/`feature_names`
  mismatch). Schema (see also `product/api/README.md`'s "Model sidecar
  schema" section, which is the normative copy):

  ```jsonc
  {
    "model_version": "concorde-b-<n>",      // bumped by export_onnx.py
    "feature_contract": "fc-1",
    "calibration": { "type": "platt", "a": <float>, "b": <float> },
    "threshold": <float in (0,1)>,           // BA-optimal, chosen on train OOF (T018)
    "git_sha": <string|null>,                // commit the model was trained at
    "seed": <int|null>,                      // train.py's SEED
    "manifest_sha256": <string|null>,        // sha256 of the manifest.csv used
    "feature_names": [ ...23 fc-1 names, in order... ],
    "train_feature_means": [ ...23 floats... ],
    "train_feature_stds": [ ...23 floats... ],
    "feature_importance": [ ...23 floats... ],
    "direction_sign": [ ...23 ints (+1/-1)... ]
  }
  ```

- **`CHANGELOG.md`** — one appended line per exported `model_version`,
  carrying the same `git_sha`/`seed`/`manifest_sha256` triple needed to
  reproduce it.
- **`probe_templates.json`** — the probe-turn detector's template bank
  (T043): finds the fixed agent probe turn *directly from audio*, with no
  ASR involved, so the Rust detector (T045) can run it in `/detect`'s
  serving path. Produced by `ml/semantic/probe_detector.py` from 3 TRAIN
  calls whose probe turn is short (the phrase alone, ~2.5 s), validated on
  every call with T042's ground truth. Schema:

  ```jsonc
  {
    "version": "probe-templates-v1",
    "mel_params": { "sample_rate": 8000, "n_fft": 256, "hop": 80, "n_mels": 32, "fmin": 50.0, "fmax": 4000.0 },
    "templates": [ [ [ ...32 floats... ], ... ], ... ],  // 3 templates, each (frames x 32)
    "template_source_calls": [ "<anon_id>", ... ],
    "threshold": <float>,          // zone midpoint, TRAIN only (ADR-012) -- logging/back-compat ONLY, not what classify_score() uses
    "min_turn_frames": <int>,      // below this, a query can't host the phrase
    "ambiguity_bounds": { "present_le": <float>, "absent_ge": <float> }  // see below
  }
  ```

  Detection: 32-band log-mel (per-band z-normalised, L2-normalised frames)
  matched against each template with open-begin/open-end subsequence DTW
  (cosine cost, normalised by template length); a call's score is the min
  over the bank. **The consumer must use `classify_score(score,
  ambiguity_bounds.present_le, ambiguity_bounds.absent_ge)`, never a bare
  `score <= threshold` comparison**: `score <= present_le` means
  confidently "probe present", `score >= absent_ge` means confidently
  "probe absent", and anything strictly between the two bounds is the
  **ambiguous zone** — F-23 must degrade to unavailable there (logged),
  not guess (AGENTS.md rule 4 / ADR-008). This is not a corner case:
  measured 2026-09-12 on the full 353-call dataset, `ambiguous_rate =
  0.4336` — **known, documented limitation**, not smoothed over (see the
  root README's Known Limitations table and `product/ml/README.md`'s
  probe-detector section for the full validation table, including why
  `confident_false_positives = 0` is the real safety gate here instead of
  a naive threshold's false-positive rate).

## Reproducing a version

Every entry is fully determined by three things recorded in its
`CHANGELOG.md` line / meta sidecar:

1. **`git_sha`** — check out that commit (feature extractors and training
   code as they existed then).
2. **A dataset with the matching `manifest_sha256`** — the altur-challenge
   practice dataset is never committed (§13.4, NFR-011); get a copy whose
   `manifest.csv` hashes to the recorded value.
3. **`seed`** — `train/train.py`'s `SEED` constant, already baked into
   that commit.

Then re-run the pipeline in order, from `product/ml/`:

```bash
export CONCORDE_DATASET_DIR=/path/to/altur-challenge-dataset
python -m train.build_dataset      # T016 — writes ml/data/features_{ref,vad}.parquet
python -m train.train              # T017 — writes ml/data/model_lgbm.txt, train_meta.json
python -m train.calibrate          # T018 — writes ml/data/calibration.json
python -m export.export_onnx       # T021 — writes artifacts/model.onnx(.meta.json), appends CHANGELOG.md
```

Each step refuses to run against the wrong inputs (fc-1 contract checks,
the T012 VAD-agreement gate, `val`/`train` split guards) rather than
silently producing a mismatched artifact — see each script's own
docstring and `product/ml/README.md`.

## Never committed here

`ml/data/*` (the parquet feature tables, `model_lgbm.txt`,
`calibration.json`, `train_meta.json`, the reliability-curve PNG) stay
gitignored — they're either regeneratable from the dataset + this
directory's provenance fields, or (the dataset itself) forbidden from the
repo outright (§13.4).
