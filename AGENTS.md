# AGENTS.md — shared conventions for CONCORDE contributors

CONCORDE is a trust layer for the banking phone channel (HackMTY 2026, Altur
Challenge): a recorded call comes in, the system decides whether the caller
(channel 0) is a real person or a synthetic voice. Full spec:
`docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf` (§ numbers below refer to it).
Read this file first — it is the load-bearing summary. Go to the PDF only for
detail (exact feature math, ADR rationale, risk register).

## Technical thesis (§1, ADR-005) — read before touching modeling code

Most teams will train an acoustic classifier on the raw waveform. We don't.
The primary signal is **behavioral/conversational**, not acoustic:

- The hidden judging set uses speakers *and TTS engines* absent from both
  `train` and `val`. A deep acoustic model on ~300 calls memorizes the
  specific TTS engine's artifacts and collapses on an unseen one. Low-
  dimensional conversational features (turn-taking, interruption recovery,
  response-latency consistency) capture a structural property of dialogue
  instead, and generalize.
- It's the only route that exploits **both channels** of the audio, which
  Altur's own brief emphasizes twice.
- The literal hint in Altur's brief — *"humans recover from interruption
  instantly and messily; machines recover consistently, and consistency is
  the signal"* — is directly operationalized as features F-05 and F-12 (§9).

Consequence: Rust/LightGBM/ONNX over a Python deep-audio-net stack (ADR-001,
ADR-002). Acoustic and semantic signals are additive/optional, never the
core, and never allowed to threaten the behavioral signal's timeline.

## Non-negotiable normative rules

These are frozen contracts. Breaking any of them silently produces a system
that looks fine locally and fails (or scores zero) in the graded round.

1. **`POST /detect` is the only scored artifact (§8.1, ADR-013, ADR-006).**
   - Response body is **exactly** `{"is_synthetic": <bool>, "confidence": <float in [0,1]>}` —
     no additional keys, ever. All rich/explanatory data goes in `POST
     /analyze` instead, never in `/detect`.
   - `/detect` **always returns HTTP 200**, even on invalid/malformed/empty
     input. It never emits a 5xx. On failure it returns the fallback verdict
     `{"is_synthetic": false, "confidence": 0.50}` and logs the incident
     internally (never masks it in dev — `CONCORDE_STRICT=1` propagates
     errors during development).
   - Request parsing must accept all four shapes from FR-003 without
     configuration (raw base64 body; JSON with any of `audio`,
     `audio_base64`, `wav`, `data`, `file`, `clip`, `content`; multipart;
     raw binary WAV) — this is the highest-impact ambiguity in the whole
     challenge (ADR-009, R-02).

2. **The feature contract `fc-1` is frozen (§9).**
   - F-01 … F-22 (plus optional F-23) in that **exact order** is the single
     source of truth for both the Python reference extractor and the Rust
     production extractor. Golden-vector parity between them (FR-005) is
     mandatory before training the final model.
   - Silent reordering of the vector is the single most dangerous possible
     defect in this system: it produces plausible-looking but wrong
     verdicts with no error raised. Any addition to the contract creates
     `fc-2`, requires a new ONNX export, and requires re-running the parity
     test. Never touch feature order casually.
   - The system must run its **own VAD** in the inference path (ADR-003,
     FR-004) — `turns/<id>.json` exists only in the practice dataset;
     `/detect` receives only the WAV. The VAD's output must be validated
     against the provided `turns.json` files (per-frame F1 ≥ 0.85) before
     the model is trained on it, or you get training/serving skew (R-01,
     the #1 silent risk in this project).

3. **MoSCoW priority (§3) is binding.** Never start a lower-priority item
   while a higher-priority one is incomplete, unless it's blocked by an
   external dependency. In order: **MUST** (`/detect` w/ defensive parsing +
   own VAD + behavioral features + ONNX inference; stable Vultr deployment;
   calibrated confidence; README) → **SHOULD** (dashboard w/ explainability,
   `/analyze` + `/health`, Gemini semantic layer behind a hard timeout,
   ElevenLabs red-team demo) → **COULD** (Tiger Data logging, acoustic
   signal as third vote, escalation protocol design, `.tech` domain).
   **WON'T**: Solana, Snowflake, MongoDB Atlas, training a deep net from
   scratch, online retraining. Depth beats breadth — see §3 for the exact
   cut criteria per item.

4. **Never emit an unmarked degradation.** If a signal (semantic, acoustic,
   storage) is unavailable, the verdict must reflect that internally and it
   must be logged — never silently imputed as if it were real data
   (ADR-008, principle in §7.2).

5. **`val` is intocable (ADR-012).** Only used for final measurement and
   calibration, never for iterative hyperparameter selection. Model
   selection happens via speaker-grouped CV inside `train`.

6. **No dataset in the repo or deployment artifacts, no speaker ID (§13.4,
   NFR-011).** `audio/`, `turns/`, `manifest.csv` and any audio-containing
   derivative are gitignored. Never implement speaker-identification
   functionality of any kind.

## Stack

- **API service**: Rust + Axum + Tokio, `hound` (WAV), `ort` (ONNX Runtime),
  `serde`, `tracing`. Lives in `api/`.
- **ML / offline pipeline**: Python, never in the serving path. Lives in
  `ml/` (`eda/`, `features/`, `train/`, `export/`, `semantic/`,
  `validation/`).
- **Dashboard**: React, dark-mode-first fintech console. Lives in
  `console/`.
- **Task orchestration**: Cauce (this repo's `scripts/`, `tasks.yaml`) — see
  below.

## Build / test

```bash
pip install -r requirements.txt   # Cauce scripts + boss-dashboard + model-requester deps
pytest                            # Python-side tests
# Rust and console build/test commands land with T001/T004 respectively —
# see api/README or console/README once those tasks are done.
```

## Coordination rules (Cauce)

- **Never edit `tasks.yaml` by hand.** Only `scripts/claim_task.py`,
  `scripts/finish_task.py`, and `scripts/add_task.py` may write to it. Hand
  edits race with other people's pushes and will be overwritten or corrupt
  someone else's claim.
- Claim a task with `python scripts/claim_task.py`, do the work in the
  worktree it creates, then finish with `python scripts/finish_task.py
  --task-id <id>`.
- One git branch per task: `task/<id>`.
- Every task's `scope` in `tasks.yaml` is a set of non-overlapping file
  paths (§13.2 of the spec) — this is what lets Cauce grant parallel claims
  across worktrees without merge conflicts. If your task needs to touch a
  file outside its declared scope, stop and get the scope corrected via
  `add_task.py`/manual re-scoping rather than editing outside it.
- Agents have **no architectural decision authority**. Any deviation from
  the feature contract (`fc-1`) or the API contract (§8) requires explicit
  human approval from Paul (architecture), Diego (modeling), or Néstor
  (frontend) as applicable — see §13.1 for the RACI.
- Discord notifications (`scripts/notify_discord.py`) are optional and
  degrade to a console print when `DISCORD_WEBHOOK_URL` /
  `DISCORD_BOT_TOKEN` aren't set. Nothing in this repo requires Discord to
  function.

## Where things live

- Full spec (source of truth for anything not covered above):
  `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf`
- Task board: `tasks.yaml` (schema + current 23 tasks from §13.2)
- Environment variables: §18.2 of the spec (`CONCORDE_MODEL_PATH`,
  `CONCORDE_FEATURE_CONTRACT`, `CONCORDE_THRESHOLD`,
  `CONCORDE_SEMANTIC_ENABLED`, `CONCORDE_SEMANTIC_TIMEOUT_MS`,
  `CONCORDE_MAX_BODY_BYTES`, `CONCORDE_STRICT`, `GEMINI_API_KEY`,
  `TIGERDATA_URL`)
