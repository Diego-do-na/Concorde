# Red-team call assembler + ElevenLabs synthetic-caller clips (T050)

Generates fully synthetic banking-support calls to red-team CONCORDE against
a TTS engine (ElevenLabs) that is absent from both `train` and `val` — the
same "unseen engine" condition the hidden judging set is expected to use
(AGENTS.md §"Technical thesis").

## Files

- `script_agent_es.json` — the 10-turn agent-side call script (Spanish),
  original text, following the dataset's typical banking customer-service
  flow: identity check, reason for call, a repeat-back request, a question
  about a non-existent product (`"Estrella Dorada"` — invented, does not
  exist), one deliberate mid-sentence interruption, one 3 s silence where
  the caller doesn't respond, and a close. Each turn carries `agent_text`
  and (when the caller replies) `caller_reply_text`.
- `assemble_call.py` — pure signal assembly: takes the script plus a
  directory of per-turn WAV clips (`agent_<id>.wav`, `caller_<id>.wav`,
  already synthesized or recorded elsewhere) and lays them onto a stereo
  timeline at 8 kHz / 16-bit, **channel 0 = caller, channel 1 = agent**
  (matching the dataset's channel convention, see `ml/common/dataset.py`).
  Supports either a constant-latency policy (`--latency constant:<mean_s>:<jitter_s>`,
  used here) or real measured per-turn timings (`--timings turns.json`, used
  by T051's human recordings instead of a policy). Contains no TTS/ElevenLabs
  calls and no dataset access — reusable by both T050 and T051.
- `gen_synthetic.py` — calls ElevenLabs TTS (`eleven_multilingual_v2`) to
  synthesize every turn of `script_agent_es.json` for 5 configurations (one
  fixed agent voice, 5 distinct caller voices, 5 different latency-jitter
  settings), assembles each with `assemble_call.py`, and writes
  `clip_synth_unseen_01.wav` … `clip_synth_unseen_05.wav` to `out/`
  (gitignored — see `product/.gitignore`). Copies `clip_synth_unseen_01.wav`
  to `product/console/public/demo/` for the dashboard's demo zone.

## No dataset audio

Nothing under `product/eval/` reads `CONCORDE_DATASET_DIR`, `manifest.csv`,
or any dataset audio/turns file. Every clip here is freshly synthesized
speech from original script text (see AGENTS.md §"No dataset in the repo,
no speaker ID").

## ElevenLabs scope used

`ELEVENLABS_API_KEY` — scope already decided and unchanged by this task:
**Text to Speech, Speech to Speech, Voices Read, Models Access, User
Access**. No other permission is requested. Set it in `product/.env`
(gitignored; see `product/.env.example`) before running `gen_synthetic.py`.
The 5 premade ElevenLabs voice IDs used (`AGENT_VOICE_ID`,
`CALLER_VOICE_IDS` in `gen_synthetic.py`) are public/well-known voices, not
identities we control — this task does not implement or need any
speaker-identification functionality.

## Running it

```bash
cd product
set -a; source .env; set +a       # needs ELEVENLABS_API_KEY
python eval/redteam/gen_synthetic.py
```

Requires `ffmpeg` on PATH (used to decode ElevenLabs' MP3 response to WAV
before resampling).

## Verification performed

(`REPORT.md` is T051's scope, not T050's — that task builds the full
multi-clip robustness table across both synthetic and human red-team
clips. The verification below is this task's own record.)

All 5 generated clips decode as stereo / 8 kHz / 16-bit, and `vad-dump`
finds 9 caller turns in each (≥ 8 required):

| clip | duration | caller turns (vad-dump) |
|---|---|---|
| clip_synth_unseen_01.wav | 118.2s | 9 |
| clip_synth_unseen_02.wav | 116.6s | 9 |
| clip_synth_unseen_03.wav | 119.3s | 9 |
| clip_synth_unseen_04.wav | 118.1s | 9 |
| clip_synth_unseen_05.wav | 122.2s | 9 |

All 5 were POSTed to the public `https://getconcorde.tech/detect`:

| clip | is_synthetic | confidence |
|---|---|---|
| clip_synth_unseen_01.wav | false | 0.50 |
| clip_synth_unseen_02.wav | false | 0.50 |
| clip_synth_unseen_03.wav | false | 0.50 |
| clip_synth_unseen_04.wav | false | 0.50 |
| clip_synth_unseen_05.wav | false | 0.50 |

**Finding, not a failure of this task**: every clip — and even an empty
POST body — currently gets back the exact fallback verdict
`{"is_synthetic": false, "confidence": 0.50}`. `GET /health` on the same
deployment reports `"model_version":"none"`, i.e. no model is loaded on the
public deployment yet, so `/detect` is on its fallback path for every
input right now. These clips are correctly built and reachable; re-running
the same POSTs once a model is deployed (T021/T023) should be the next
check, expected to lean synthetic given they're all-ElevenLabs calls.
