 # Escalation protocol — Active defence (one-screen pitch)

 Purpose: map model output to three runtime states and operator handoffs; design-only pitch for Paul.

 States → verdictOf
 - VERIFIED — confidence ≫ threshold. Action: continue; `verdictOf: VERIFIED`.
 - REVIEW — confidence within ±0.20 of threshold. Action: short agent probe, re-extract features, re-score; `verdictOf: REVIEW`.
 - SYNTHETIC — confidence ≪ threshold. Action: transfer to human fraud investigator with `/analyze` payload; `verdictOf: SYNTHETIC`.

Operational states (high level)
- VERIFIED — Action: continue. Condition: model confidence sufficiently above the configured threshold (safe-operating band). `verdictOf: VERIFIED` — allow the call to proceed with no further intervention.
- REVIEW — Action: interactive re-check and re-score. Condition: confidence within ±0.20 of the configured threshold (i.e. |confidence - threshold| ≤ 0.20). `verdictOf: REVIEW` — the agent issues a short out-of-script contextual verification prompt, collects the response, re-extracts features and re-scores; outcome may flip to VERIFIED or SYNTHETIC.
- SYNTHETIC — Action: transfer to a human fraud agent. Condition: confidence sufficiently below threshold (clear negative). `verdictOf: SYNTHETIC` — immediately create a transfer task, attach the `/analyze` rationale payload, and route to the human fraud queue.

Review flow (REVIEW)
- Agent asks a brief contextual verification prompt (non-biometric, non-identifying; e.g., confirm a recent transaction detail or repeat a short contextual phrase related to the session). The prompt must be short, scripted for compliance, and avoid any speaker-ID or private-data escalation.
- The agent collects the reply, runs the same VAD + feature extractor on the reply, and re-scores. The re-score must complete within the protocol latency budget (see next section). If re-score remains ambiguous, the call is escalated to human review (same transfer path as SYNTHETIC) to avoid repeated automated friction.
- All intermediate evidence (original feature vector, re-score vector, prompt text, reply transcript, timestamps) is included in the `/analyze` payload for human reviewers.

Latency & false-positive cost logic (summary)
- Tradeoff principle: false-positive cost (mistakenly flagging a human caller) is higher than a false-negative for production UX; latency cost (added delay during review) is higher for high-volume, low-value calls. See §8.4 and §10.2 for the formal cost curves and calibration procedure.
- Practical rule: contain automatic re-check latency to a small, bounded budget (single-digit seconds preferred). If the re-check would breach the latency budget or produce additional customer friction (fraud-risk > operational cost), prefer transferring to human review rather than chaining further automated probes.
- Threshold tuning: use the frozen feature contract (fc-1) and CV-calibrated confidence to set the operating threshold; configure the ±0.20 review window as the default tradeoff. Revisit the width when measured FP and FN costs diverge from targets.

Console integration: REVIEW QUEUE stat
- The console exposes a `REVIEW QUEUE` stat (length, oldest-wait, and avg-rescore-latency). Flow:
  - New REVIEW items increment the queue and are annotated with reason and confidence delta.
  - UI shows a small "review action" summary per item (confidence, re-score result if available, arrival time).
  - SLA-based routing: items older than the configured human-SLA (configurable) are auto-prioritised for investigators; very-high-confidence SYNTHETIC items skip the queue and are routed as urgent.
  - Operators can re-run the automated re-score from the console (replay audio -> features -> onnx) and accept/reject the agent verdict; all operator actions are appended to the `/analyze` record.

Human transfer payload
- Transfer task must include:
  - original WAV (or secure pointer), transcription, and timestamps
  - extracted feature vector (fc-1 order) and per-feature diagnostics
  - model confidence, decision boundary, and `verdictOf`
  - the agent's verification prompt and response (if REVIEW)
  - brief rationale (why the system flagged the call) and links to related calls
- The `/analyze` record is the canonical handoff: it must be readable by the fraud investigator UI and by offline investigation tooling.

Acceptance notes
- Design-only (COULD). Keep the review window, prompts, and transfer payloads subject to legal/compliance review. Paul to verify and sign off; after review add an anchor link from the top-level README for the pitch.

Revision history
- Drafted for Paul — pending verification and placement in README.

