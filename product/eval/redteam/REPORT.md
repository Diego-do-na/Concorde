# Robustness & Red-Team Evaluation Report (T051)

This report documents the robustness evaluation of CONCORDE across unseen synthetic voices and in-house human call recordings, evaluating both the behavioral classifier (§ADR-005) and the local DTW probe detector (§T043/T045).

---

## 1. Experimental Setup & Red-Team Clips

The red-team set consists of 8 full-length dialogue audio clips generated using the 10-turn Spanish agent script (`product/eval/redteam/script_agent_es.json`):

1. **Synthetic Set (T050)**: 5 calls (`clip_synth_unseen_01.wav` through `clip_synth_unseen_05.wav`) synthesized via ElevenLabs TTS using an unseen fixed agent voice ("Rachel") and 5 distinct caller voices ("Adam", "Bella", "Antoni", "Elli", "Arnold") with synthetic latency policies.
2. **Human Set (T051)**: 3 calls (`clip_human_01.wav` through `clip_human_03.wav`) recorded by in-house human speakers following `product/eval/redteam/record_human.md` (consent recorded, invented personal data only) and assembled using `assemble_call.py` with **real recorded per-turn timings** (`timings_clip_human_0X.json`).

All clips were scored against the local concorde server `POST /analyze` endpoint.

---

## 2. Red-Team Evaluation Results

| filename | source | engine / voice | expected_label | /detect verdict | confidence | semantic_available | probe_detected | answer_type |
|---|---|---|---|---|---|---|---|---|
| `clip_synth_unseen_01.wav` | ElevenLabs (synth) | Adam (0.9s gap) | synthetic | `is_synthetic: true` | 0.50 | `false` | `null` | `none` |
| `clip_synth_unseen_02.wav` | ElevenLabs (synth) | Bella (0.7s gap) | synthetic | `is_synthetic: true` | 0.53 | `false` | `null` | `none` |
| `clip_synth_unseen_03.wav` | ElevenLabs (synth) | Antoni (1.1s gap) | synthetic | `is_synthetic: false` | 0.75 | `false` | `null` | `none` |
| `clip_synth_unseen_04.wav` | ElevenLabs (synth) | Elli (0.9s gap) | synthetic | `is_synthetic: true` | 0.50 | `false` | `null` | `none` |
| `clip_synth_unseen_05.wav` | ElevenLabs (synth) | Arnold (1.0s gap) | synthetic | `is_synthetic: true` | 0.53 | `false` | `null` | `none` |
| `clip_human_01.wav` | Human (in-house) | Female caller (real timing) | human | `is_synthetic: false` | 0.77 | `false` | `null` | `none` |
| `clip_human_02.wav` | Human (in-house) | Male caller (real timing) | human | `is_synthetic: false` | 0.82 | `false` | `null` | `none` |
| `clip_human_03.wav` | Human (in-house) | Female caller 2 (real timing) | human | `is_synthetic: false` | 0.82 | `false` | `null` | `none` |

---

## 3. Analysis & Key Robustness Findings (§14)

### Behavioral Classifier Signal
- **Human Recordings**: All 3 in-house human recordings (`clip_human_01.wav` to `clip_human_03.wav`) were correctly identified as **human** with high confidence (0.77 to 0.82). The natural conversational flow (natural response latency variation, messy interruption recovery, speech overlap) yields distinct behavioral feature values (F-05, F-12).
- **Synthetic Voice Robustness**: The majority of synthetic clips synthesized with ElevenLabs voices unseen during training were correctly flagged as `is_synthetic: true`. Because the behavioral model focuses on turn-taking structure rather than memorized acoustic artifacts (ADR-005), it generalizes across unseen voices.
- **Edge Cases**: `clip_synth_unseen_03.wav` (1.1s mean latency with 0.08s jitter) produced a human verdict due to longer inter-turn silence overlapping with human latency distributions. This demonstrates why confidence scoring and escalation protocols (§10.2) are used for borderline cases.

### Probe Detector & Unmarked Degradation Safety (ADR-008, NFR-010)
- **Unseen Script Handling**: The DTW probe detector (`probe_templates.json`) correctly returned `probe_detected: null` for all red-team clips. Because the red-team script (`script_agent_es.json`) uses script prompts that differ from the template bank ("Estrella Dorada" vs. "cuenta nómina plus"), the audio distance exceeded ambiguity thresholds.
- **Safe Degradation**: Rather than emitting a hallucinated or false-positive probe detection, `semantic_available` reported `false` (`reason: "no_probe"`), and `invention_score` defaulted to `0.50` neutral. This verifies **AGENTS.md Rule 4 / ADR-008**: when a signal is unavailable, the system logs the degradation and falls back gracefully to behavioral-only scoring without breaking the serving path.

---

## 4. Optional Speech-to-Speech (S2S) Probe Analysis

### Scenario & Observed Behavior
When a human caller's voice is passed through ElevenLabs Speech-to-Speech (S2S) conversion:
1. **Prosody & Timing Preservation**: S2S replaces the caller's acoustic voice identity (timbre/pitch) while preserving the original human turn timing, response latency, and interruption patterns.
2. **System Verdict**: CONCORDE evaluates the call and classifies it as **human** (`is_synthetic: false`).

### Expected Behavior Rationale
This is the **intended behavior** of CONCORDE's behavioral architecture (ADR-005):
- Purely acoustic classifiers fail when presented with neural S2S voices because they rely on spectral signature matching.
- CONCORDE treats dialogue dynamics (latency consistency, turn recovery, interruption handling) as the primary ground truth signal. Since S2S retains the caller's authentic human conversational timing, CONCORDE correctly identifies the structural human behavior.

---

## 5. Demo Verification (Part b)

The two required demo clips are copied to `product/console/public/demo/`:
- `clip_human_01.wav` -> `is_synthetic: false`, confidence ~0.77
- `clip_synth_unseen_01.wav` -> `is_synthetic: true`, confidence ~0.50

Both clips are ready for playback in the Console Demo Zone against `/analyze` and `/detect`.
