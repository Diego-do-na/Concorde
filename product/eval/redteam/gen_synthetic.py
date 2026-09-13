"""Generate 5 synthetic-caller red-team calls (T050) via ElevenLabs TTS.

For each of the 5 configurations below: synthesizes every agent/caller turn
in script_agent_es.json with a distinct ElevenLabs voice pair (agent voice
fixed across all 5 calls; caller voice + latency jitter vary per call so the
set probes several unseen-engine voice/timing combinations), assembles the
call with assemble_call.py, and writes it to product/eval/redteam/out/
(gitignored). clip_synth_unseen_01.wav is additionally copied to
product/console/public/demo/ for the dashboard's demo zone.

Requires ELEVENLABS_API_KEY (scope: TTS, Speech to Speech, Voices Read,
Models Access, User Access — see product/.env.example). NO dataset audio is
read anywhere in this file — every clip here is freshly synthesized.

Usage (from product/):
    set -a; source .env; set +a
    python eval/redteam/gen_synthetic.py
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from assemble_call import (  # noqa: E402
    ConstantLatencyPolicy,
    assemble,
    load_script,
    write_stereo_wav,
)

HERE = Path(__file__).parent
SCRIPT_PATH = HERE / "script_agent_es.json"
OUT_DIR = HERE / "out"
CONSOLE_DEMO_DIR = HERE.parent.parent / "console" / "public" / "demo"

ELEVENLABS_TTS_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_MODEL_ID = "eleven_multilingual_v2"  # Spanish support, any voice

# Well-known public ElevenLabs premade voices (multilingual). The agent voice
# stays fixed across all 5 calls; each call gets a different caller voice so
# the set exercises several TTS engine "unseen" identities.
AGENT_VOICE_ID = "21m00Tcm4TlvDq8ikWAM"  # "Rachel"
CALLER_VOICE_IDS = [
    "pNInz6obpgDQGcFmaJgB",  # "Adam"
    "EXAVITQu4vr4xnSDxMaL",  # "Bella"
    "ErXwobaYiN019PkySvjV",  # "Antoni"
    "MF3mGyEYCl7XYWbV9V6O",  # "Elli"
    "VR6AewLTigWG4xSOukaG",  # "Arnold"
]

# (caller_voice_id, latency_mean_s, latency_jitter_s, output_name)
CALL_CONFIGS = [
    (CALLER_VOICE_IDS[0], 0.9, 0.05, "clip_synth_unseen_01.wav"),
    (CALLER_VOICE_IDS[1], 0.7, 0.05, "clip_synth_unseen_02.wav"),
    (CALLER_VOICE_IDS[2], 1.1, 0.08, "clip_synth_unseen_03.wav"),
    (CALLER_VOICE_IDS[3], 0.9, 0.02, "clip_synth_unseen_04.wav"),
    (CALLER_VOICE_IDS[4], 1.0, 0.10, "clip_synth_unseen_05.wav"),
]


def _require_api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise SystemExit(
            "ELEVENLABS_API_KEY is not set. Copy product/.env.example to "
            "product/.env, fill it in, then `set -a; source .env; set +a` "
            "before running this script."
        )
    return key


def _tts(text: str, voice_id: str, api_key: str) -> bytes:
    """Synthesize `text` with ElevenLabs TTS, return raw MP3 bytes."""
    resp = requests.post(
        ELEVENLABS_TTS_URL.format(voice_id=voice_id),
        headers={"xi-api-key": api_key, "Content-Type": "application/json"},
        json={
            "text": text,
            "model_id": ELEVENLABS_MODEL_ID,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        },
        timeout=60,
    )
    resp.raise_for_status()
    return resp.content


def _mp3_to_wav(mp3_bytes: bytes, out_path: Path) -> None:
    """Decode MP3 to a mono WAV via ffmpeg (assemble_call.py resamples/downmixes)."""
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        mp3_path = f.name
    try:
        import subprocess

        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-i", mp3_path, str(out_path)],
            check=True,
        )
    finally:
        os.unlink(mp3_path)


def synthesize_turns(turns: list[dict], agent_voice: str, caller_voice: str, api_key: str, clips_dir: Path) -> None:
    clips_dir.mkdir(parents=True, exist_ok=True)
    for turn in turns:
        tid = turn["id"]
        agent_path = clips_dir / f"agent_{tid}.wav"
        if not agent_path.exists():
            mp3 = _tts(turn["agent_text"], agent_voice, api_key)
            _mp3_to_wav(mp3, agent_path)

        if turn.get("caller_reply_text") is not None:
            caller_path = clips_dir / f"caller_{tid}.wav"
            mp3 = _tts(turn["caller_reply_text"], caller_voice, api_key)
            _mp3_to_wav(mp3, caller_path)


def main() -> None:
    api_key = _require_api_key()
    turns = load_script(SCRIPT_PATH)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for caller_voice, mean_s, jitter_s, out_name in CALL_CONFIGS:
        clips_dir = OUT_DIR / f"_clips_{out_name.removesuffix('.wav')}"
        print(f"[{out_name}] synthesizing turns with caller voice {caller_voice}...")
        synthesize_turns(turns, AGENT_VOICE_ID, caller_voice, api_key, clips_dir)

        policy = ConstantLatencyPolicy(mean_s, jitter_s)
        stereo = assemble(turns, clips_dir, policy)
        out_path = OUT_DIR / out_name
        write_stereo_wav(out_path, stereo)
        print(f"[{out_name}] wrote {out_path} ({len(stereo) / 8000:.1f}s)")

    demo_target = CONSOLE_DEMO_DIR / "clip_synth_unseen_01.wav"
    CONSOLE_DEMO_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy(OUT_DIR / "clip_synth_unseen_01.wav", demo_target)
    print(f"copied clip_synth_unseen_01.wav -> {demo_target}")


if __name__ == "__main__":
    main()
