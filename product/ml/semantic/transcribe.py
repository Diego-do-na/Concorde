#!/usr/bin/env python3
"""Local whisper.cpp transcription of the dataset + probe ground truth (T042).

Fully local semantic layer (approved route: no audio or text ever leaves
this machine). For every call in `manifest.csv`, transcribes BOTH channels
(0 = caller, 1 = agent) with BOTH ggml models (tiny, base; language "es")
using the whisper.cpp CLI built from source (see product/ml/README.md's
semantic section), split on `turns/<id>.json` boundaries (practice-only
ground truth, ADR-003 — never available at serving time).

Design note on why this is fast (~1 s/agent-channel measured on base @ 4
threads, whole dataset both models both channels ~30-60 min): each call's
per-channel turns are resampled 8 kHz -> 16 kHz and concatenated into ONE
clip with a short silence gap between turns, so whisper-cli runs ONCE per
(call, channel, model) instead of once per turn. whisper's own segment
timestamps (in the concatenated clip's timeline) are then reassigned back
to the original turn windows by nearest-offset — this is what lets
"per call and per turn" transcripts come out of one subprocess call.

Cache: cache/transcripts/<model>/<anon_id>.json (gitignored,
product/.gitignore's `ml/semantic/cache/`) — resumable: a call already
cached for a given model is skipped entirely (zero whisper invocations),
so `nohup python3 transcribe.py &` can be killed and re-run safely.

Also computes probe ground truth for T043: per call, the agent turn whose
transcript contains the fixed probe phrase "esto es sobre su cuenta
nómina plus o sobre su crédito verde" (fuzzy-matched against whisper.cpp's
own ASR variants, e.g. "no mina plus") and the first caller turn after it
-> cache/probe_ground_truth.json.

Usage:
    cd product/ml
    python3 semantic/transcribe.py                       # both models, all splits
    python3 semantic/transcribe.py --models base          # base only
    python3 semantic/transcribe.py --split train --limit 5  # smoke test
    nohup python3 semantic/transcribe.py > semantic/cache/transcribe.log 2>&1 &

Requires CONCORDE_DATASET_DIR (see product/.env.example) and a built
whisper.cpp CLI (product/ml/semantic/whisper.cpp/build/bin/whisper-cli by
default, override with --whisper-bin).
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import subprocess
import sys
import tempfile
import time
import unicodedata
import wave
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.dataset import iter_split, load_turns, wav_path  # noqa: E402

SEMANTIC_DIR = Path(__file__).resolve().parent
CACHE_DIR = SEMANTIC_DIR / "cache"
TRANSCRIPTS_DIR = CACHE_DIR / "transcripts"
GROUND_TRUTH_PATH = CACHE_DIR / "probe_ground_truth.json"
DEFAULT_WHISPER_BIN = SEMANTIC_DIR / "whisper.cpp" / "build" / "bin" / "whisper-cli"
DEFAULT_MODELS_DIR = SEMANTIC_DIR / "models"

MODEL_FILES = {
    "tiny": "ggml-tiny.bin",
    "base": "ggml-base.bin",
}

SAMPLE_RATE_IN = 8000
SAMPLE_RATE_OUT = 16000
TURN_GAP_S = 0.30  # silence inserted between concatenated turns

# The fixed probe phrase (§ T043) an agent turn is checked against, plus
# the ASR spelling variants of "nómina plus" that the base model measurably
# produces on this dataset. Fuzzy matching (see probe_match_score) also
# tolerates variants not listed here.
PROBE_PHRASE = "esto es sobre su cuenta nómina plus o sobre su crédito verde"
PROBE_PHRASE_VARIANTS = (
    PROBE_PHRASE,
    "esto es sobre su cuenta no mina plus o sobre su crédito verde",
    "esto es sobre su cuenta no mine plus o sobre su crédito verde",
    "esto es sobre su cuenta nomina fluz o sobre su crédito verde",
)
PROBE_MATCH_THRESHOLD = 0.60


# --------------------------------------------------------------------------
# Text normalisation + fuzzy probe matching (also used by rules.py's tests)
# --------------------------------------------------------------------------

def normalize_text(text: str) -> str:
    """lowercase, strip accents/punctuation, collapse whitespace."""
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


_PROBE_NORMS = [normalize_text(p) for p in PROBE_PHRASE_VARIANTS]
_PROBE_WORD_COUNT = len(_PROBE_NORMS[0].split())

# Cheap pre-filter: every phrase variant retains at least one of these even
# under heavy ASR corruption (measured on this dataset) -- "verde"/"berde"
# covers the b/v merger common in Spanish ASR. Skipping the expensive
# sliding-window comparison below when none of these appear cuts runtime by
# ~10x, since most turns/turn-pairs in a call don't mention this at all.
_ANCHOR_TOKENS = ("cuenta", "verde", "berde")


def probe_match_score(text: str) -> float:
    """Best fuzzy-match ratio of `text` against the probe phrase (0..1).

    Slides a window of roughly the probe phrase's word count over `text`
    (so the score isn't diluted by unrelated words before/after it in the
    same turn) and takes the best ratio against any of the known ASR
    spelling variants, plus a whole-string comparison as a fallback for
    short turns.
    """
    norm = normalize_text(text)
    if not norm:
        return 0.0
    if not any(tok in norm for tok in _ANCHOR_TOKENS):
        return 0.0
    words = norm.split()
    best = 0.0
    lo = max(1, _PROBE_WORD_COUNT - 3)
    hi = _PROBE_WORD_COUNT + 4
    for win_len in range(lo, hi + 1):
        if win_len > len(words):
            break
        for start in range(0, len(words) - win_len + 1):
            window = " ".join(words[start : start + win_len])
            for target in _PROBE_NORMS:
                ratio = difflib.SequenceMatcher(None, window, target).ratio()
                if ratio > best:
                    best = ratio
    for target in _PROBE_NORMS:
        ratio = difflib.SequenceMatcher(None, norm, target).ratio()
        if ratio > best:
            best = ratio
    return best


# --------------------------------------------------------------------------
# Audio I/O
# --------------------------------------------------------------------------

def read_stereo_wav(path: Path) -> Tuple[np.ndarray, int]:
    """(samples[N, nchannels] int16, sample_rate)."""
    with wave.open(str(path), "rb") as w:
        nch = w.getnchannels()
        sr = w.getframerate()
        n = w.getnframes()
        raw = w.readframes(n)
    data = np.frombuffer(raw, dtype=np.int16).reshape(-1, nch)
    return data, sr


def write_mono_wav(path: Path, samples_i16: np.ndarray, sr: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples_i16.tobytes())


def build_channel_clip(
    channel_samples: np.ndarray,
    sr_in: int,
    intervals: Sequence[Tuple[float, float]],
    sr_out: int = SAMPLE_RATE_OUT,
    gap_s: float = TURN_GAP_S,
) -> Tuple[np.ndarray, List[Tuple[float, float]]]:
    """Resamples each [start,end) interval to `sr_out` and concatenates them
    with `gap_s` silence between, returning (clip_i16, windows) where
    windows[i] = (start_s, end_s) of interval i in the concatenated clip's
    own timeline.
    """
    if not intervals:
        return np.zeros(0, dtype=np.int16), []

    pieces: List[np.ndarray] = []
    windows: List[Tuple[float, float]] = []
    cursor = 0.0
    gap = np.zeros(int(round(gap_s * sr_out)), dtype=np.float32)

    for start, end in intervals:
        s = max(0, int(round(start * sr_in)))
        e = min(len(channel_samples), int(round(end * sr_in)))
        seg = channel_samples[s:e]
        if seg.size < 2:
            seg16 = np.zeros(1, dtype=np.float32)
        else:
            seg16 = resample_poly(seg.astype(np.float32) / 32768.0, sr_out, sr_in)
        dur = len(seg16) / sr_out
        windows.append((cursor, cursor + dur))
        pieces.append(seg16)
        pieces.append(gap)
        cursor += dur + gap_s

    concat = np.concatenate(pieces)
    concat_i16 = np.clip(concat * 32768.0, -32768, 32767).astype(np.int16)
    return concat_i16, windows


def _nearest_window(mid_s: float, windows: Sequence[Tuple[float, float]]) -> Optional[int]:
    best_i, best_d = None, None
    for i, (s, e) in enumerate(windows):
        if s <= mid_s <= e:
            return i
        d = min(abs(mid_s - s), abs(mid_s - e))
        if best_d is None or d < best_d:
            best_d, best_i = d, i
    return best_i


def assign_segments_to_windows(
    transcription: Sequence[dict], windows: Sequence[Tuple[float, float]]
) -> List[str]:
    """Reassigns whisper-cli's `transcription[].offsets` (ms, concat-clip
    timeline) back to the original turn windows by nearest offset."""
    texts = ["" for _ in windows]
    for seg in transcription:
        offsets = seg.get("offsets", {})
        from_ms = offsets.get("from", 0)
        to_ms = offsets.get("to", from_ms)
        mid_s = ((from_ms + to_ms) / 2.0) / 1000.0
        idx = _nearest_window(mid_s, windows)
        if idx is None:
            continue
        piece = (seg.get("text") or "").strip()
        if not piece:
            continue
        texts[idx] = f"{texts[idx]} {piece}".strip() if texts[idx] else piece
    return texts


# --------------------------------------------------------------------------
# whisper.cpp CLI invocation
# --------------------------------------------------------------------------

def run_whisper_cli(
    whisper_bin: Path,
    model_path: Path,
    wav_file: Path,
    language: str = "es",
    threads: int = 4,
) -> dict:
    """Runs whisper-cli on `wav_file`, returns the parsed `-oj` JSON output."""
    with tempfile.TemporaryDirectory() as td:
        out_prefix = Path(td) / "out"
        cmd = [
            str(whisper_bin),
            "-m", str(model_path),
            "-f", str(wav_file),
            "-l", language,
            "-t", str(threads),
            "-np",
            "-oj",
            "-of", str(out_prefix),
        ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        json_path = out_prefix.with_suffix(".json")
        with json_path.open("r", encoding="utf-8") as f:
            return json.load(f)


# --------------------------------------------------------------------------
# Cache
# --------------------------------------------------------------------------

def cache_path(anon_id: str, model: str) -> Path:
    return TRANSCRIPTS_DIR / model / f"{anon_id}.json"


def is_cached(anon_id: str, model: str) -> bool:
    return cache_path(anon_id, model).is_file()


def transcribe_and_cache(
    anon_id: str,
    model: str,
    *,
    whisper_bin: Path,
    model_path: Path,
    threads: int = 4,
    language: str = "es",
    force: bool = False,
) -> bool:
    """Transcribes one call for one model and writes the cache file.

    Returns True if work was actually done, False if it was already cached
    (resumability: a second run does zero whisper invocations).
    """
    out_path = cache_path(anon_id, model)
    if out_path.is_file() and not force:
        return False

    turns = load_turns(anon_id)  # [(channel, start, end), ...] global, time-sorted
    stereo, sr = read_stereo_wav(wav_path(anon_id))

    channel_indices: Dict[int, List[int]] = {0: [], 1: []}
    for gi, (ch, _s, _e) in enumerate(turns):
        channel_indices.setdefault(ch, []).append(gi)

    text_by_global_index: Dict[int, str] = {}

    for channel, indices in channel_indices.items():
        if not indices or channel >= stereo.shape[1]:
            continue
        intervals = [(turns[gi][1], turns[gi][2]) for gi in indices]
        clip, windows = build_channel_clip(stereo[:, channel], sr, intervals)
        if not windows:
            continue
        with tempfile.TemporaryDirectory() as td:
            wav_tmp = Path(td) / f"{anon_id}_ch{channel}.wav"
            write_mono_wav(wav_tmp, clip, SAMPLE_RATE_OUT)
            result = run_whisper_cli(whisper_bin, model_path, wav_tmp, language, threads)
        texts = assign_segments_to_windows(result.get("transcription", []), windows)
        for gi, text in zip(indices, texts):
            text_by_global_index[gi] = text

    out_turns = [
        {
            "index": gi,
            "channel": ch,
            "start": s,
            "end": e,
            "text": text_by_global_index.get(gi, ""),
        }
        for gi, (ch, s, e) in enumerate(turns)
    ]

    out_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"anon_id": anon_id, "model": model, "language": language, "turns": out_turns}
    tmp_path = out_path.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
    tmp_path.replace(out_path)
    return True


# --------------------------------------------------------------------------
# Probe ground truth (T043)
# --------------------------------------------------------------------------

def find_probe_turn(turns: Sequence[dict]) -> Tuple[Optional[int], float]:
    """Best-scoring agent (channel 1) turn -- or adjacent PAIR of agent
    turns -- matching the probe phrase. Returns (end_index, score), where
    end_index is the last turn's global index in the winning span, or
    (None, best_score) if nothing clears PROBE_MATCH_THRESHOLD.

    Checking adjacent pairs matters: turns.json sometimes splits the agent's
    probe sentence in two around a brief caller interjection, or around a
    near-silent agent blip transcribed as empty text (both measured on this
    dataset, e.g. "...esto es sobre su cuenta nomina" / "plus o sobre su
    crédito verde..." as two separate turns) -- a single-turn-only check
    misses those. Empty-text turns are dropped before pairing so "next
    turn" means the next one with actual content, not the next index.
    """
    agent_turns = [t for t in turns if t["channel"] == 1 and t["text"].strip()]
    best_idx, best_score = None, 0.0
    for i, turn in enumerate(agent_turns):
        score = probe_match_score(turn["text"])
        if score > best_score:
            best_idx, best_score = turn["index"], score
        if i + 1 < len(agent_turns):
            pair_text = f"{turn['text']} {agent_turns[i + 1]['text']}".strip()
            pair_score = probe_match_score(pair_text)
            if pair_score > best_score:
                best_idx, best_score = agent_turns[i + 1]["index"], pair_score
    if best_score >= PROBE_MATCH_THRESHOLD:
        return best_idx, best_score
    return None, best_score


def find_answer_turn(turns: Sequence[dict], probe_index: int) -> Optional[int]:
    """First caller (channel 0) turn after `probe_index`."""
    for turn in turns:
        if turn["index"] > probe_index and turn["channel"] == 0:
            return turn["index"]
    return None


def build_probe_ground_truth(anon_ids: Sequence[str], model: str = "base") -> dict:
    calls: Dict[str, dict] = {}
    found = 0
    considered = 0
    for anon_id in anon_ids:
        path = cache_path(anon_id, model)
        if not path.is_file():
            continue
        considered += 1
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        turns = data["turns"]
        probe_idx, score = find_probe_turn(turns)
        if probe_idx is None:
            calls[anon_id] = {"probe_found": False, "probe_score": round(score, 4)}
            continue
        found += 1
        answer_idx = find_answer_turn(turns, probe_idx)
        entry = {
            "probe_found": True,
            "probe_turn_index": probe_idx,
            "probe_score": round(score, 4),
            "answer_turn_index": answer_idx,
        }
        if answer_idx is not None:
            probe_end = turns[probe_idx]["end"]
            answer_start = turns[answer_idx]["start"]
            entry["response_latency"] = round(answer_start - probe_end, 3)
        calls[anon_id] = entry
    coverage = (found / considered) if considered else 0.0
    return {
        "model": model,
        "considered": considered,
        "found": found,
        "coverage": round(coverage, 4),
        "calls": calls,
    }


def save_ground_truth(gt: dict) -> Path:
    GROUND_TRUTH_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = GROUND_TRUTH_PATH.with_suffix(".json.tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(gt, f, ensure_ascii=False, indent=2)
    tmp_path.replace(GROUND_TRUTH_PATH)
    return GROUND_TRUTH_PATH


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", default="tiny,base", help="comma-separated: tiny,base")
    parser.add_argument("--split", default=None, choices=[None, "train", "val"])
    parser.add_argument("--whisper-bin", type=Path, default=DEFAULT_WHISPER_BIN)
    parser.add_argument("--models-dir", type=Path, default=DEFAULT_MODELS_DIR)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--language", default="es")
    parser.add_argument("--limit", type=int, default=None, help="only first N calls (smoke test)")
    parser.add_argument("--force", action="store_true", help="re-transcribe even if cached")
    parser.add_argument(
        "--ground-truth-only",
        action="store_true",
        help="skip transcription, only (re)build probe_ground_truth.json from the base cache",
    )
    args = parser.parse_args(argv)

    anon_ids = [row.anon_id for row in iter_split(args.split)]
    if args.limit:
        anon_ids = anon_ids[: args.limit]
    models = [m.strip() for m in args.models.split(",") if m.strip()]

    if not args.ground_truth_only:
        if not args.whisper_bin.is_file():
            print(f"ERROR: whisper-cli not found at {args.whisper_bin}", file=sys.stderr)
            print("Build it once: see product/ml/README.md's semantic section.", file=sys.stderr)
            return 1

        total = len(anon_ids) * len(models)
        done = 0
        skipped = 0
        t0 = time.monotonic()
        print(f"[transcribe] {len(anon_ids)} calls x {len(models)} model(s) = {total} items", flush=True)

        for model in models:
            model_path = args.models_dir / MODEL_FILES[model]
            if not model_path.is_file():
                print(f"ERROR: model file not found: {model_path} (run get_models.sh)", file=sys.stderr)
                return 1
            for i, anon_id in enumerate(anon_ids, start=1):
                item_start = time.monotonic()
                worked = transcribe_and_cache(
                    anon_id,
                    model,
                    whisper_bin=args.whisper_bin,
                    model_path=model_path,
                    threads=args.threads,
                    language=args.language,
                    force=args.force,
                )
                n = (models.index(model) * len(anon_ids)) + i
                if worked:
                    done += 1
                    elapsed = time.monotonic() - t0
                    rate = elapsed / n if n else 0.0
                    remaining = (total - n) * rate
                    print(
                        f"[transcribe] [{n}/{total}] {model} {anon_id} "
                        f"({time.monotonic() - item_start:.1f}s, ETA {remaining/60:.1f} min)",
                        flush=True,
                    )
                else:
                    skipped += 1
                    if n % 25 == 0:
                        print(f"[transcribe] [{n}/{total}] {model} {anon_id} (cached, skipped)", flush=True)

        print(f"[transcribe] done: {done} transcribed, {skipped} already cached", flush=True)

    if "base" in models or args.ground_truth_only:
        gt = build_probe_ground_truth(anon_ids, model="base")
        save_ground_truth(gt)
        print(
            f"[transcribe] probe ground truth: {gt['found']}/{gt['considered']} calls "
            f"({gt['coverage']:.1%}) -> {GROUND_TRUTH_PATH}",
            flush=True,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
