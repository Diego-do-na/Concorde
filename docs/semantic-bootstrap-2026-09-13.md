# Semantic fusion weight — bootstrap stability check (2026-09-13, fork `experimental/v2`)

**Outcome: the semantic layer stays disabled.** With the deployed ASR model
(`ggml-tiny`), the fused verdict never differs from the behavioral-only
verdict on a single call — on `val` (71) or on `train` out-of-fold (282) —
for the shipped weight `w = -0.8`, and no weight in `[-3, 3]` moves balanced
accuracy on `val` at all. The only detectable effect is a *positive* `w` of
`+0.05` on train OOF, driven by 3 calls, i.e. the opposite sign of the
shipped artifact and far too little evidence to ship.

## What was measured (real production path, not the Python transcript cache)

`semantic_fusion.json`'s `w = -0.8` was fitted in T044 on `base`-model
transcripts, and the bootstrap stability check that was set as its
condition never ran. The whisper transcript cache that check needs does not
exist on any machine or on the server, so instead both signals were
produced with the code that actually serves:

- `p_behavioral`: shipped `product/artifacts/model.onnx` + its Platt
  `a/b` + threshold, evaluated exactly as `inference/model.rs` does.
- `invention_score`: `semantic-dump` (T045's binary — Rust VAD, DTW probe
  detector, whisper-rs with `ggml-tiny.bin`, the model deployed on the
  server), run over all 353 calls (11 s for val, ~1 min for train, 6-way parallel).
- Fusion rule as in `semantic/fusion.rs` (`logit(p) + w·(inv − 0.5)`,
  `|Δp| ≤ 0.15`).
- Bootstrap: 200 resamples with replacement; on each, `w_opt` is the
  `w ∈ linspace(−3, 3, 121)` maximising BA of the fused verdict at the
  shipped threshold, with the honest tie rule `w_opt = 0` when no `w`
  beats behavioral-only.

## Results

| | val (n=71) | train OOF (n=282) |
|---|---|---|
| semantic available (probe found + answer) | 38 / 71 | 152 / 282 |
| calls within fusion reach (`|p − thr| < 0.15`) | 3 | 43 |
| …and semantic-available | 2 | 21 |
| …and `invention_score ≠ 0.5` (only these can move) | **0** | **3** |
| BA behavioral-only | 0.8637 | 0.8658 |
| BA fused, `w = −0.8` | 0.8637 (0 verdicts change) | 0.8658 (0 verdicts change) |
| `w_opt` on the full set | 0.00 | +0.05 (BA 0.8702, +1 call) |
| bootstrap `w_opt` sign (200×) | 0% < 0 · **100% = 0** · 0% > 0 | 0% < 0 · 32% = 0 · **68% > 0** |
| bootstrap BA gain of `w = −0.8`, p5/p50/p95 | 0 / 0 / 0 | 0 / 0 / 0 |

Answer types produced by `tiny` on the answer turn: val — other 25,
assertion_product 7, denial 3, assertion_numeric 2, hedge 1; train — other
108, assertion_product 20, denial 15, assertion_numeric 6, hedge 3.

## Why it is inert

Fusion can only move a verdict when the call is within `max_delta_p` of the
threshold **and** the probe was detected **and** the answer classified as
something other than `other` (`invention_score = 0.5` makes the fusion term
exactly zero). On `val` the intersection is empty (the two reachable
available calls are both `other`); on train it is 3 calls. The negative
sign fitted in T044 comes from `base`-model transcripts, which are not what
the server runs.

## Decision

`CONCORDE_SEMANTIC_ENABLED` stays `false` in every deployment. Enabling it
with the deployed model would be a no-op on the verdict while adding ASR
latency and a nonzero `busy`/`timeout` path; enabling it with a *positive*
`w` would rest on 3 calls. This document is the evidence the T044 condition
asked for; it does not approve the layer.

Reproduce: `semantic-dump <wav> --whisper-model ggml-tiny.bin` per call,
then the bootstrap scripts kept in this session's scratchpad
(`bootstrap_w.py`, `bootstrap_w_train.py`) — logic is fully described above.
