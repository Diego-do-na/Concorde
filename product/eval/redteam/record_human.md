Protocol to record in-house human red-team clips

Goal
- Record 3 human calls that mimic the agent script used by `gen_synthetic.py`.
- Consent must be recorded (verbal consent at start of recording and note in metadata).
- Use invented personal data only (no real PII).

Recording setup
- Use a headset so the agent audio can be played into one ear while the participant speaks into the mic.
- Mix/monitor: play the agent channel (the same agent script audio used for synth calls) in the participant's right ear; record the participant's microphone (caller) normally. Keep a reference copy of the agent-side audio used for each recording.
- Record a stereo WAV at 8 kHz, 16-bit, two channels where channel 0 = caller (participant mic) and channel 1 = agent (playback). This matches the dataset convention used by `assemble_call.py`.

Procedure
1. Start a fresh WAV recorder and verbally record consent at the start of the file (short phrase: "I consent to this recording for testing, I confirm the data I will provide is invented. Name: <initials>").
2. Play the agent script (per-turn agent prompts) through one ear so the participant hears it and replies naturally. Do not add artificial latency — keep natural response timing.
3. Stop the recorder when the script completes.
4. Repeat to produce 3 distinct human calls. Name outputs:
   - clip_human_01_raw.wav
   - clip_human_02_raw.wav
   - clip_human_03_raw.wav

Timings and assembly
- After recording, measure the real per-turn timings (agent turn start, caller reply start) for each turn. Use Audacity or any editor and export a JSON mapping:

  {
    "0": {"agent_start_s": 0.00, "caller_start_s": 2.30},
    "1": {"agent_start_s": 5.00, "caller_start_s": 7.10},
    ...
  }

- Save that JSON as `timings_<clipname>.json` next to per-turn clips.
- Use `assemble_call.py` to assemble a clean two-channel 8 kHz call from per-turn agent/caller clips or (preferred) if your recorder already produced a proper two-channel file, you can skip assembly and instead rename the raw file to `clip_human_01.wav` after verifying channels.

Example (assemble from per-turn WAVs):

  python3 product/eval/redteam/assemble_call.py \
    --script product/eval/redteam/script_agent_es.json \
    --clips-dir product/eval/redteam/_clips_clip_human_01 \
    --out product/eval/redteam/out/clip_human_01.wav \
    --timings product/eval/redteam/out/timings_clip_human_01.json

Demo copy
- Copy one canonical human clip to the console demo folder for the dashboard:

  cp product/eval/redteam/out/clip_human_01.wav product/console/public/demo/clip_human_01.wav

Notes
- Recordings and assembled outputs are dataset-like artifacts; keep them outside git (the `product/console/.gitignore` already ignores demo WAVs). Place intermediate/working WAVs under `product/eval/redteam/out/`.
- Document any deviations (noise, overlap) in the clip's metadata next to the WAV.

