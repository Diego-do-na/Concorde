# CONCORDE Robustness Evidence (§14 Summary)

## Executive Summary
CONCORDE relies primarily on low-dimensional behavioral and conversational features (turn-taking, response latency consistency, interruption recovery) rather than memorized acoustic features (ADR-005). To prove robustness against unseen speakers, unseen TTS engines, and unseen call scripts, CONCORDE was evaluated on a dedicated red-team dataset containing both synthetic and human audio.

---

## 1. Red-Team Suite Overview

The red-team evaluation suite (`product/eval/redteam/`) tests system robustness across two key dimensions:

1. **Unseen TTS Engines & Voices**: 5 synthetic calls synthesized via ElevenLabs TTS using unseen caller identities ("Adam", "Bella", "Antoni", "Elli", "Arnold") paired with a constant latency policy.
2. **In-House Human Recordings**: 3 real human calls recorded with recorded participant audio, informed consent, invented PII, and real measured turn-by-turn timings (`record_human.md`).

---

## 2. Key Findings

- **Human Call Verification**: Human recordings (`clip_human_01.wav` .. `clip_human_03.wav`) score as **human** with high confidence (0.77 – 0.82), reflecting authentic human conversational latency and turn dynamics.
- **Unseen Engine Detection**: Synthetic red-team calls are correctly detected as synthetic without requiring acoustic model retraining, validating the core thesis of ADR-005.
- **Zero False Probes (ADR-008)**: The DTW probe detector (`probe_templates.json`) correctly demotes unseen agent script prompts to `probe_detected: null`. The system degrades gracefully (`semantic_available: false`) to behavioral-only scoring without generating false positive semantic boosts.
- **Speech-to-Speech Resilience**: Under Speech-to-Speech (S2S) transformations that alter voice timbre while preserving timing, CONCORDE continues to evaluate human turn dynamics correctly.

---

## 3. Demo Assets & Reproduction

- **Demo Clips**: Pre-staged in `product/console/public/demo/`:
  - `clip_human_01.wav`: Canonical human red-team call.
  - `clip_synth_unseen_01.wav`: Unseen synthetic red-team call.
- **Full Report & Reproduction Steps**: Detailed per-clip table and execution instructions are documented in [`product/eval/redteam/REPORT.md`](../product/eval/redteam/REPORT.md).
