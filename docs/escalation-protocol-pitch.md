Escalation protocol — Active defence (one-screen pitch)

Purpose: map model output to three runtime states and operator handoffs; design-only pitch for Paul.

States → verdictOf
- VERIFIED — confidence ≫ threshold. Action: continue; `verdictOf: VERIFIED`.
- REVIEW — confidence within ±0.20 of threshold. Action: short agent probe, re-extract features, re-score; `verdictOf: REVIEW`.
- SYNTHETIC — confidence ≪ threshold. Action: transfer to human fraud investigator with `/analyze` payload; `verdictOf: SYNTHETIC`.

REVIEW flow (brief)
- Agent issues a compliant, non-identifying verification prompt, captures reply, runs VAD+feature extractor, and re-scores within the latency budget. If still ambiguous or budget-exceeded, escalate to human review.

Key constraints
- Latency vs false-positive cost: favor minimizing false positives; keep automated re-checks bounded (single-digit seconds). If re-check exceeds budget or risks UX, transfer to human.
- Default review window: ±0.20 around threshold (tune via CV-calibrated confidence and fc-1 parity).

Console & handoff
- `REVIEW QUEUE` shows length, oldest-wait, avg-rescore-latency; operators can replay/rescore and accept/reject. Transfer payload (`/analyze`) includes WAV pointer, transcript, fc-1 vector, confidence, verdict, prompt+reply, timestamps, and rationale.

Acceptance
- Draft for Paul verification. After sign-off add README anchor link for the pitch.
