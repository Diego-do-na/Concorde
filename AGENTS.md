# AGENTS.md — shared conventions for CONCORDE contributors

CONCORDE is a trust layer for the banking phone channel (HackMTY 2026, Altur
Challenge): a recorded call comes in, the system decides whether the caller
(channel 0) is a real person or a synthetic voice. Full spec:
`docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf` (§ numbers below refer to it).
Read this file first — it is the load-bearing summary. Go to the PDF only for
detail (exact feature math, ADR rationale, risk register).

## Repo layout — two separate worlds

- **`orchestration/`** is Cauce: the task board, claim/finish scripts, the
  boss-dashboard and model-requester web apps. It is coordination tooling,
  not CONCORDE product code. You claim a task by running something under
  `orchestration/scripts/`, but the *work itself* — everything a task's
  `scope` points at — happens outside `orchestration/`, in `api/`, `ml/`,
  `console/`, `deploy/`, `docs/`, etc. Don't add product code under
  `orchestration/`, and don't add Cauce mechanics anywhere else.
- Everything else at the repo root is CONCORDE itself.

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
- **Task orchestration**: Cauce, entirely under `orchestration/` — see
  below.

## Build / test

```bash
pip install -r orchestration/requirements.txt   # Cauce scripts + boss-dashboard + model-requester deps
pytest                                           # Python-side tests
# Rust and console build/test commands land with T001/T004 respectively —
# see api/README or console/README once those tasks are done.
```

## Coordination rules (Cauce)

- **Never edit `orchestration/tasks.yaml` by hand.** Only
  `orchestration/scripts/claim_task.py`,
  `orchestration/scripts/finish_task.py`, and
  `orchestration/scripts/add_task.py` may write to it. Hand edits race with
  other people's pushes and will be overwritten or corrupt someone else's
  claim.
- **One-time per machine**: `./orchestration/scripts/setup.sh` — creates
  `orchestration/.venv`, installs `orchestration/requirements.txt`, and
  sets `git config user.name` if unset (that name becomes the default
  Cauce owner).
- **The daily loop, one command each way**:
  - `./orchestration/scripts/work.sh [owner] [agent]` — claims the next
    eligible task, rebases its worktree onto the latest `origin/main`
    (always, every launch — a dependency's merged code needs this to
    actually show up in your files, not just in `tasks.yaml`'s status),
    and launches the agent (`claude` by default, pass `cursor-agent` as
    the second arg) directly inside it with `--model` set from the task's
    `suggested_model` and the task's title+description as its initial
    prompt. Safe to run from anywhere (it always resolves the main
    checkout first).
  - `./orchestration/scripts/finish.sh <task-id>` — refuses if the branch
    has no commits beyond `main` (nothing to finish), then if it merges
    cleanly: pushes the branch, **merges it into `main` for real and
    pushes `main`** (not just a status flip — this is what actually lands
    your code where every new worktree branches from and where anyone
    browses it on GitHub), then marks the task done in `tasks.yaml`. Safe
    to run from inside the task's own worktree (the common case) or from
    the main checkout. Run `python orchestration/scripts/task_log.py
    <task-id>` first if you want a deterministic summary of what a
    session actually did before finishing it.
  - `./orchestration/scripts/autopilot.sh [owner] [agent]` — the loop
    version of `work.sh`: claims a task, launches the agent interactively
    (normal permission prompts, nothing bypassed), and when the agent's
    session ends it stops and asks you to confirm the diff stayed inside
    the task's declared scope before running `finish_task.py` and moving
    to the next eligible task. One invocation, keeps going until you quit
    it. If it's interrupted (or you answer "quit") with a task still
    claimed, re-running it resumes that same task's worktree instead of
    claiming a new one.
  - `./orchestration/scripts/fleet.sh [owner] [agent...]` — runs one
    `autopilot.sh` loop per agent CLI **in parallel** (auto-detects
    `claude`/`cursor-agent` on PATH if none given), each in its own Terminal.app
    window (macOS; prints the commands to run yourself otherwise), each
    under its own Cauce owner identity (`<owner>-<agent>`) so the two
    loops never mistake each other's in-flight claim for their own. When
    one agent finishes its task before the other, it grabs the next
    eligible one immediately instead of waiting. Safe by construction —
    verified under an actual concurrent race, not just sequential turns —
    because Cauce's scope-conflict check and its pull/rebase/retry on
    every `tasks.yaml` write already serialize concurrent claims
    correctly; nothing here bypasses permission prompts.
  - `./orchestration/scripts/dashboard.sh` — one person runs this to serve
    the read-only board monitor at `http://localhost:8000`.
  - The raw Python entry points (`claim_task.py`, `finish_task.py`,
    `poller.py`, `add_task.py`, `notify_discord.py`, `check_merge.py`,
    `task_log.py`) still work directly under `orchestration/scripts/` if
    you need more control than the wrappers give — but only from the main
    checkout, never from inside a task's own worktree (they refuse with a
    clear error if run from the wrong place, since every git operation
    they do — including the merge into `main` — has to happen there).
- Every git command that mutates refs (pull/fetch/push/rebase/merge/worktree
  add) in the scripts runs under one machine-wide lock, `orchestration/.git.lock`
  (`common.local_repo_lock()`, re-entrant; shell scripts go through
  `orchestration/scripts/locked_git.py`). This is what lets two `autopilot.sh`
  loops (claude + cursor-agent under `fleet.sh`) share one checkout without
  "cannot lock ref" / "divergent branches" collisions. If you add a git call to
  a script, route it through `run_git` or `locked_git.py`.
- One git branch per task: `task/<id>`.
- Every task's `scope` in `tasks.yaml` is a set of non-overlapping file
  paths (§13.2 of the spec) — this is what lets Cauce grant parallel claims
  across worktrees without merge conflicts. **This is a human/agent review
  responsibility, not something any script verifies for you** — whoever
  reviews a task before finishing it (in `autopilot.sh`'s prompt, or by
  hand) must check the diff only touches files inside the declared scope.
  If a task turns out to need work outside its own scope — e.g. finishing
  a DB schema surfaces the need for a query layer that's really a separate
  task — don't silently expand the current task's scope to cover it and
  don't let the agent edit those files. Either narrow the task's own
  Definition of Done so it self-verifies within its own scope (a schema
  task proves itself with its own fixture/migration test, not by writing
  the real query layer), or create the follow-up as its own task via
  `add_task.py` with `depends_on` pointing at the current one. Sequential
  dependency chains are the intended way to model "B needs A finished
  first" — not overlapping scope.
- Agents have **no architectural decision authority**. Any deviation from
  the feature contract (`fc-1`) or the API contract (§8) requires explicit
  human approval from Paul (architecture), Diego (modeling), or Néstor
  (frontend) as applicable — see §13.1 for the RACI.
- Discord notifications (`orchestration/scripts/notify_discord.py`) are
  optional and degrade to a console print when `DISCORD_WEBHOOK_URL` /
  `DISCORD_BOT_TOKEN` aren't set (put them in `orchestration/.env`, which
  is gitignored). Nothing in this repo requires Discord to function.

## Where things live

- Full spec (source of truth for anything not covered above):
  `docs/CONCORDE_Especificacion_Tecnica_v1.0.pdf`
- Task board: `orchestration/tasks.yaml`. A reference copy of the original
  23-task board from spec §13.2 is saved at
  `orchestration/tasks.concorde-v1-seed.yaml` in case it's useful to
  cross-check coverage against whatever board is live.
- Each task's `suggested_model` (claude aliases: `haiku`/`sonnet`/`opus`/
  `fable`) is forwarded to `claude` as `--model`; empty/unset falls back to
  `haiku`. cursor-agent uses a completely different model catalog (`gpt-
  5.4-nano-low`, `claude-sonnet-5-high`, etc. — see `cursor-agent
  --list-models`), so Claude-style values are never forwarded to it — it
  runs on `CAUCE_CURSOR_CHEAP_MODEL` (default `gpt-5-mini`) instead.
- Environment variables: §18.2 of the spec (`CONCORDE_MODEL_PATH`,
  `CONCORDE_FEATURE_CONTRACT`, `CONCORDE_THRESHOLD`,
  `CONCORDE_SEMANTIC_ENABLED`, `CONCORDE_SEMANTIC_TIMEOUT_MS`,
  `CONCORDE_MAX_BODY_BYTES`, `CONCORDE_STRICT`, `GEMINI_API_KEY`,
  `TIGERDATA_URL`)
