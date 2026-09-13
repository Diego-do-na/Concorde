"""Assemble a stereo 8 kHz 16-bit call WAV from an agent script + per-turn clips.

Consumes `script_agent_es.json` (or any script with the same shape) plus a
directory of already-synthesized or already-recorded per-turn WAV files
(`agent_<id>.wav`, `caller_<id>.wav`), and places them on a two-channel
timeline following the dataset's channel convention: **channel 0 = caller,
channel 1 = agent** (see ml/common/dataset.py).

This module contains no TTS/ElevenLabs calls and no dataset access — it is
pure signal assembly, reusable for both:
  - T050's synthetic calls (gen_synthetic.py supplies ElevenLabs-generated
    clips and a constant-latency policy), and
  - T051's human calls (record_human.md's clips + the *real* recorded
    turn timings, via --timings).

NO dataset audio is read or referenced anywhere in this file.
"""

from __future__ import annotations

import argparse
import json
import random
import wave
from pathlib import Path
from typing import Optional

import numpy as np
from scipy.signal import resample_poly

TARGET_SR = 8000
SAMPLE_WIDTH_BYTES = 2  # 16-bit PCM


def _read_wav_mono(path: Path) -> np.ndarray:
    """Read a WAV file (any sample rate, mono or stereo) as float32 mono at TARGET_SR."""
    with wave.open(str(path), "rb") as wf:
        n_channels = wf.getnchannels()
        sampwidth = wf.getsampwidth()
        framerate = wf.getframerate()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sampwidth != 2:
        raise ValueError(f"{path}: expected 16-bit PCM, got {sampwidth * 8}-bit")

    data = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if n_channels > 1:
        data = data.reshape(-1, n_channels).mean(axis=1)

    if framerate != TARGET_SR:
        # resample_poly wants integer up/down factors; reduce via gcd.
        from math import gcd

        g = gcd(int(framerate), TARGET_SR)
        up, down = TARGET_SR // g, int(framerate) // g
        data = resample_poly(data, up, down).astype(np.float32)

    return data


def _place(track: np.ndarray, clip: np.ndarray, start_sample: int) -> None:
    """Add `clip` into `track` at `start_sample`, growing is the caller's job."""
    end = start_sample + len(clip)
    track[start_sample:end] += clip[: max(0, len(track) - start_sample)]


class LatencyPolicy:
    """Gap (seconds) inserted between the end of an agent turn and the start
    of the caller's reply, for turns without an explicit interruption or
    measured real timing."""

    def gap_s(self) -> float:
        raise NotImplementedError


class ConstantLatencyPolicy(LatencyPolicy):
    def __init__(self, mean_s: float, jitter_s: float, rng: Optional[random.Random] = None):
        self.mean_s = mean_s
        self.jitter_s = jitter_s
        self.rng = rng or random.Random()

    def gap_s(self) -> float:
        g = self.rng.gauss(self.mean_s, self.jitter_s)
        return max(0.0, g)


def load_script(script_path: Path) -> list[dict]:
    data = json.loads(script_path.read_text(encoding="utf-8"))
    return data["turns"]


def assemble(
    turns: list[dict],
    clips_dir: Path,
    latency: LatencyPolicy,
    timings: Optional[dict] = None,
    trailing_silence_s: float = 0.6,
) -> np.ndarray:
    """Build the (n_samples, 2) int16 stereo array: col 0 = caller, col 1 = agent.

    `timings`, when given, maps str(turn_id) -> {"agent_start_s", "caller_start_s"}
    absolute seconds from call start (measured/real timings, e.g. T051's human
    recordings) and overrides the latency policy and interrupt offsets entirely
    for that turn.
    """
    # First pass: load every clip and, if not using real timings, lay out a
    # cursor-driven timeline.
    caller_chunks: list[tuple[int, np.ndarray]] = []
    agent_chunks: list[tuple[int, np.ndarray]] = []

    cursor = 0.0
    for turn in turns:
        tid = turn["id"]
        agent_path = clips_dir / f"agent_{tid}.wav"
        if not agent_path.exists():
            raise FileNotFoundError(f"missing agent clip for turn {tid}: {agent_path}")
        agent_clip = _read_wav_mono(agent_path)
        agent_dur_s = len(agent_clip) / TARGET_SR

        if timings is not None and str(tid) in timings:
            t = timings[str(tid)]
            agent_start_s = t["agent_start_s"]
            caller_start_s = t.get("caller_start_s")
        else:
            agent_start_s = cursor
            caller_start_s = None  # computed below

        agent_start_sample = round(agent_start_s * TARGET_SR)
        agent_chunks.append((agent_start_sample, agent_clip))
        agent_end_s = agent_start_s + agent_dur_s

        has_reply = turn.get("caller_reply_text") is not None
        caller_dur_s = 0.0
        if has_reply:
            caller_path = clips_dir / f"caller_{tid}.wav"
            if not caller_path.exists():
                raise FileNotFoundError(f"missing caller clip for turn {tid}: {caller_path}")
            caller_clip = _read_wav_mono(caller_path)
            caller_dur_s = len(caller_clip) / TARGET_SR

            if caller_start_s is None:
                if turn.get("expect_interruption"):
                    offset = turn.get("interrupt_offset_s", agent_dur_s / 2)
                    caller_start_s = agent_start_s + offset
                elif turn.get("caller_silence_s"):
                    caller_start_s = agent_end_s + turn["caller_silence_s"]
                else:
                    caller_start_s = agent_end_s + latency.gap_s()

            caller_start_sample = round(caller_start_s * TARGET_SR)
            caller_chunks.append((caller_start_sample, caller_clip))
            caller_end_s = caller_start_s + caller_dur_s
        else:
            # silence-only turn: still advance the cursor by the configured gap
            caller_end_s = agent_end_s + turn.get("caller_silence_s", 0.0)

        cursor = max(agent_end_s, caller_end_s) + 0.15  # small inter-turn breathing gap

    total_s = cursor + trailing_silence_s
    n_samples = round(total_s * TARGET_SR)
    # pad n_samples so no chunk placement overruns
    for start, clip in caller_chunks + agent_chunks:
        n_samples = max(n_samples, start + len(clip))

    caller_track = np.zeros(n_samples, dtype=np.float32)
    agent_track = np.zeros(n_samples, dtype=np.float32)
    for start, clip in caller_chunks:
        _place(caller_track, clip, start)
    for start, clip in agent_chunks:
        _place(agent_track, clip, start)

    stereo = np.stack([caller_track, agent_track], axis=1)
    peak = np.abs(stereo).max()
    if peak > 0:
        # normalize to avoid clipping across the whole call, leave headroom
        stereo = stereo / peak * 30000.0
    return stereo.astype(np.int16)


def write_stereo_wav(path: Path, stereo_int16: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(SAMPLE_WIDTH_BYTES)
        wf.setframerate(TARGET_SR)
        wf.writeframes(stereo_int16.tobytes())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--script", type=Path, required=True)
    ap.add_argument("--clips-dir", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument(
        "--latency",
        default="constant:0.9:0.05",
        help="constant:<mean_s>:<jitter_s> (default) — ignored for turns whose "
        "timing comes from --timings, or that mark expect_interruption/caller_silence_s",
    )
    ap.add_argument(
        "--timings",
        type=Path,
        default=None,
        help="optional JSON: {turn_id: {agent_start_s, caller_start_s}} of real "
        "measured timings (used by T051's human calls instead of a latency policy)",
    )
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for the latency policy")
    args = ap.parse_args()

    kind, mean_s, jitter_s = args.latency.split(":")
    assert kind == "constant", f"unsupported latency policy: {kind}"
    rng = random.Random(args.seed) if args.seed is not None else random.Random()
    policy = ConstantLatencyPolicy(float(mean_s), float(jitter_s), rng)

    timings = None
    if args.timings is not None:
        timings = json.loads(args.timings.read_text(encoding="utf-8"))

    turns = load_script(args.script)
    stereo = assemble(turns, args.clips_dir, policy, timings=timings)
    write_stereo_wav(args.out, stereo)
    dur_s = len(stereo) / TARGET_SR
    print(f"wrote {args.out} ({dur_s:.1f}s, stereo, {TARGET_SR} Hz, 16-bit)")


if __name__ == "__main__":
    main()
