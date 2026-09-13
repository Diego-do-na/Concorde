# Semantic layer (F-23 / FR-014): design, fusion rule, and live availability

Status: **fully local, optional, additive** — see AGENTS.md's technical
thesis (§1, ADR-005): the semantic layer is never the core signal, and it
must never be allowed to threaten the behavioral pipeline's timeline or
availability.

## What runs where

| Stage | Where | Task |
|---|---|---|
| Probe-turn detection (audio only, no ASR) | `/detect`'s Rust serving path (`api/`) | T043 → T045 |
| Whisper.cpp transcription of the caller's answer turn | offline, `ml/semantic/transcribe.py` | T042 |
| Answer-type classification + `invention_score` lookup | offline reference in `ml/semantic/rules.py`; ported to Rust for the serving path | T042 → T045 |
| Runtime fusion into the served verdict | `/detect`'s Rust serving path | this doc + `ml/semantic/ab_report.py` |

Everything upstream of the fusion step is **local-only** (no audio or text
ever leaves the machine — the approved route for F-23, see
`ml/semantic/transcribe.py`'s own docstring). The offline pipeline
(`transcribe.py` → `probe_detector.py` → `rules.py`) is a *reference*
implementation the Rust port is parity-tested against, never something
`/detect` calls into directly — whisper.cpp's own latency is far too high
for `/detect`'s SLA at the transcript-generation stage, which is exactly
why the probe detector (T043) runs from raw audio, and why the invention
score itself has to be computed on whatever ASR *is* fast enough to run
inline (see "Live availability" below).

## Offline scoring pipeline (T044)

`ml/semantic/score_dataset.py` runs the **exact same sequence** T045's
Rust path runs, using only artifacts already frozen by earlier tasks:

1. `probe_detector.py`'s exported template bank
   (`product/artifacts/probe_templates.json`) scores every agent turn of a
   call and picks the best (confidently-present) match — pure audio, no
   ASR. A score in the bank's ambiguity zone (T043: `ambiguous_rate =
   0.4336` on this dataset — a documented, known limitation, not smoothed
   over) degrades to "probe not detected" (AGENTS.md rule 4 / ADR-008).
2. The first caller turn after the detected probe turn — T043's fixed
   definition of "the answer turn".
3. That turn's cached whisper.cpp transcript text (T042), separately for
   the `tiny` and `base` models, so the two can be compared honestly.
4. `rules.py`'s fixed answer-type rule table → `invention_score`.

This deliberately never reads T042's own (whisper-derived)
`probe_ground_truth.json` to *pick* the probe turn — only its cached
transcript text, once a turn has already been chosen from audio alone.
Reusing the ASR-derived ground truth for turn selection would leak a
much stronger signal than the actual pure-audio detector T045 ships, and
overstate this offline measurement's realism.

Output: `ml/data/semantic_scores.csv` — `anon_id, model, probe_detected,
answer_type, invention_score, semantic_available`, two rows per call (one
per whisper model). `semantic_available=False` (with the neutral
`invention_score=0.5`) whenever the probe wasn't confidently detected or
the call has no cached transcript for that model — never a silently
imputed real-looking number.

## FR-014 A/B and the runtime fusion (`ml/semantic/ab_report.py`)

**(1) A/B**: fc-1 alone vs fc-1 + `invention_score`, T017's exact protocol
(same params/seeds/folds), evaluated separately for `tiny` and `base`
transcripts. See `ml/semantic/AB_REPORT.md` for the numbers.

**(2) Runtime fusion** — the rule that would actually run inside
`/detect`:

```
logit(p_final) = logit(p_behavioral) + w * (invention_score - 0.5)
clamp: |p_final - p_behavioral| <= max_delta_p   (default 0.15)
```

`p_behavioral` is the shipped fc-1-only calibrated probability
(`ml/data/calibration.json`, T017/T018) — this file never retrains or
recalibrates the behavioral model. `w` is chosen on **TRAIN
out-of-fold** predictions to maximise the fused verdict's balanced
accuracy at the shipped threshold (Altur's primary metric, §8.4), never
on val (ADR-012). Val is used only to *report* BA/AUC/Brier of the fused
verdict and the fraction of val verdicts that flip relative to
behavioral-only.

**Honest outcome rule**: if a whisper model's fused val BA does not
exceed behavioral-only's val BA, `w` is forced to 0 for that model — the
layer stays visible in `/analyze`'s explanation but never moves
`/detect`'s verdict. See `product/artifacts/semantic_fusion.json` for the
artifact actually written (schema documented in
`product/artifacts/README.md`) and `AB_REPORT.md` for both models' full
numbers side by side.

## Live availability: what T057 must confirm

`ab_report.py`'s `whisper_model` choice is an **accuracy-only**
comparison. It is not, on its own, the answer to "which whisper model
does the deployed server run" — that answer has a harder, independent
constraint T057 already measured: `docs/asr-server-benchmark.md`, on the
actual Vultr `vc2-2c-4gb` deployment target:

| model | ≤6 s turn p95 | vs. 1200 ms SLA |
|---|---|---|
| `ggml-tiny.bin` | ~900 ms | ✅ passes |
| `ggml-base.bin` | ~1280 ms | ❌ fails |

**`ggml-base.bin` is already disqualified for the live inline path on the
current server size**, independent of anything this task measured. If
`ab_report.py`'s A/B favors `base` on accuracy, that is useful evidence
for a *future* decision (a bigger instance, an async/best-effort
semantic path that doesn't block `/detect`'s response, or accepting
`tiny`'s numbers as the honest ceiling) — it is not, by itself, license
to ship `base` inline today. Per AGENTS.md's RACI, that trade-off is an
architecture decision (Paul/Diego), not something either T044 or T057
resolves unilaterally.

**A finding that needs human review before shipping, not just noting**:
on the current 353-call sample, `ab_report.py`'s `base`-transcript fusion
selects **`w = -0.8`** — a *higher* `invention_score` (a confident
assertion) nudges the verdict *toward human*, the opposite sign from the
design rationale in `rules.py`'s module docstring (a template-following
system inventing a confident answer was expected to push toward
synthetic). The honest-outcome rule only checks that the fused val BA
improves, not that the sign matches the intended story, so this passed
mechanically. With only 353 calls (and `semantic_available` on barely
70% of them, per the offline scoring numbers above), a sign flip like
this is exactly the kind of small-sample artifact §10.2's own
anti-self-deception audit exists to catch elsewhere in this pipeline —
Diego (modeling, per the RACI in AGENTS.md §13.1) should look at this
before this fusion rule is treated as final, not just before `base` vs
`tiny` is decided.

**Practically**: unless that trade-off is revisited, the semantic layer
that can actually run in `/detect`'s live path is the `tiny`-transcript
`invention_score` and whatever `w`/`max_delta_p` `ab_report.py` computed
for `tiny` — unaffected by `base`'s numbers except as a documented
ceiling for what better ASR *would* buy.

**Expected live availability, honestly stated**: even with `tiny`
selected, `semantic_available` on this dataset is bounded by two
independent degradation points multiplying together — the probe
detector's `ambiguous_rate` (0.4336, T043) and whatever fraction of
identified answer turns transcribe to non-empty, classifiable text
(T042's DIGEST.md measured a non-trivial empty-transcript rate on short
caller interjections). The realistic expectation is that the semantic
layer is unavailable (degrading to the neutral `invention_score=0.5`,
i.e. no nudge) for a **majority** of live calls — this is why AGENTS.md
classifies it as SHOULD-tier and additive, not MUST-tier, and why the
fusion clamp (`max_delta_p`) exists at all: even when available, it can
only nudge the behavioral verdict, never override it.
