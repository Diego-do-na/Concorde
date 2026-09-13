#!/usr/bin/env python3
"""Offline F-23 scoring with the FULL local pipeline (T044).

For every call in `manifest.csv`, reproduces exactly the sequence T045's
Rust production path runs at serving time, using only the pieces already
frozen by earlier tasks (T042 transcribe.py, T043 probe_detector.py,
rules.py):

    1. run the exported probe-turn detector (T043's log-mel + subsequence
       DTW template bank, `product/artifacts/probe_templates.json`) over
       every agent (channel 1) turn of the call to find the probe turn —
       purely from audio, no ASR;
    2. the first caller (channel 0) turn after the detected probe turn
       (§ T043's fixed definition of "the answer turn");
    3. that turn's cached whisper.cpp transcript text (T042), for BOTH
       the `tiny` and `base` models, so `ab_report.py` can compare them;
    4. `rules.classify_answer_type()` / `rules.invention_score()` on that
       text.

This module intentionally never looks at T042's probe_ground_truth.json
(text-based, ASR-derived) to pick the probe turn — that would leak the
whisper-based ground truth into a number meant to measure the pure-audio
path T045 actually ships. It only borrows the ground-truth file's list of
calls that have a cached transcript, and the cached transcript text
itself, for step 3 above.

AGENTS.md rule 4 / ADR-008: when the probe isn't confidently detected (or
a call has no cached transcript for a given model), `semantic_available`
is False and `invention_score` is the neutral 0.5 default — never a
silently imputed real-looking number.

Writes:
    ml/data/semantic_scores.csv   anon_id, model, probe_detected,
                                   answer_type, invention_score,
                                   semantic_available   (2 rows/call: one
                                   per whisper model)

Idempotent: re-running overwrites the file deterministically (no RNG,
sorted anon_id iteration) — same inputs always produce the same CSV.

Usage:
    cd product/ml
    python3 semantic/score_dataset.py
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # product/ml/ on sys.path

from common.dataset import load_manifest, load_turns  # noqa: E402
from semantic import probe_detector, rules  # noqa: E402
from semantic.transcribe import TRANSCRIPTS_DIR, cache_path  # noqa: E402

ML_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ML_DIR / "data"
OUT_PATH = DATA_DIR / "semantic_scores.csv"

WHISPER_MODELS = ("tiny", "base")
NEUTRAL_INVENTION_SCORE = 0.5

CSV_FIELDS = [
    "anon_id",
    "model",
    "probe_detected",
    "answer_type",
    "invention_score",
    "semantic_available",
]


# --------------------------------------------------------------------------
# Step 1+2: probe detection + answer-turn selection, audio + turns only.
# --------------------------------------------------------------------------


def detect_probe_and_answer_index(
    anon_id: str, templates: Sequence[np.ndarray], payload: dict
) -> Tuple[bool, Optional[int]]:
    """(probe_detected, answer_turn_global_index).

    Scores every agent (channel 1) turn of `anon_id` against the template
    bank, keeps the best (lowest) score, and classifies it with the
    exported bank's ambiguity bounds (`probe_detector.classify`) — `None`
    (ambiguous) degrades to "not detected", same rule the Rust port must
    follow (§ probe_detector.classify_score's docstring).

    Given a confidently-detected probe turn, `answer_turn_global_index` is
    the global index (matching `turns.json` / the cached transcript's
    "index" field) of the first channel-0 turn after it, or None if there
    isn't one.
    """
    turns = load_turns(anon_id)  # [(channel, start, end), ...], global index = position
    min_turn_samples = payload["min_turn_frames"] * probe_detector.HOP

    best_score = float("inf")
    best_idx: Optional[int] = None
    for gi, (ch, start, end) in enumerate(turns):
        if ch != 1:
            continue
        samples = probe_detector._channel0_turn_samples(anon_id, start, end)
        if len(samples) < min_turn_samples:
            continue
        query = probe_detector.extract_features(samples)
        score = probe_detector.detector_score(templates, query)
        if score < best_score:
            best_score, best_idx = score, gi

    if best_idx is None:
        return False, None

    verdict = probe_detector.classify(best_score, payload)
    if verdict is not True:  # False (confidently absent) or None (ambiguous) both degrade
        return False, None

    for gi, (ch, _start, _end) in enumerate(turns):
        if gi > best_idx and ch == 0:
            return True, gi
    return True, None


# --------------------------------------------------------------------------
# Step 3+4: cached transcript lookup + rules.py.
# --------------------------------------------------------------------------


def answer_text_for_model(anon_id: str, model: str, answer_index: int) -> Optional[str]:
    """Cached whisper `model` transcript text for the turn at `answer_index`,
    or None if this call has no cached transcript for that model (T042
    hasn't transcribed it, e.g. a call whisper.cpp choked on) — a real
    degradation, not conflated with "the turn transcribed to empty text"
    (rules.py treats empty text as answer_type "other", which is not a
    degradation, just a data-quality fact about ASR — see rules.py's
    DIGEST note)."""
    path = cache_path(anon_id, model)
    if not path.is_file():
        return None
    import json

    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    for turn in data["turns"]:
        if turn["index"] == answer_index:
            return turn["text"]
    return None


def score_call(anon_id: str, templates: Sequence[np.ndarray], payload: dict) -> List[dict]:
    """One row per WHISPER_MODELS entry for this call."""
    probe_detected, answer_index = detect_probe_and_answer_index(anon_id, templates, payload)

    rows = []
    for model in WHISPER_MODELS:
        text = None
        if probe_detected and answer_index is not None:
            text = answer_text_for_model(anon_id, model, answer_index)

        if text is None:
            rows.append(
                {
                    "anon_id": anon_id,
                    "model": model,
                    "probe_detected": probe_detected,
                    "answer_type": "",
                    "invention_score": NEUTRAL_INVENTION_SCORE,
                    "semantic_available": False,
                }
            )
            continue

        analysis = rules.analyze_answer(text)
        rows.append(
            {
                "anon_id": anon_id,
                "model": model,
                "probe_detected": True,
                "answer_type": analysis["answer_type"],
                "invention_score": analysis["invention_score"],
                "semantic_available": True,
            }
        )
    return rows


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_rows(anon_ids: Sequence[str]) -> List[dict]:
    templates, _threshold, payload = probe_detector.load_bank()
    rows: List[dict] = []
    for anon_id in anon_ids:
        rows.extend(score_call(anon_id, templates, payload))
    return rows


def write_csv(rows: Sequence[dict], path: Path = OUT_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".csv.tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    tmp.replace(path)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-path", type=Path, default=OUT_PATH)
    parser.add_argument("--limit", type=int, default=None, help="smoke-test on the first N calls")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    manifest = load_manifest()
    anon_ids = sorted(manifest["anon_id"])
    if args.limit:
        anon_ids = anon_ids[: args.limit]

    rows = build_rows(anon_ids)
    write_csv(rows, args.out_path)

    n_calls = len(anon_ids)
    n_detected = sum(1 for r in rows if r["model"] == "base" and r["probe_detected"])
    n_available_base = sum(1 for r in rows if r["model"] == "base" and r["semantic_available"])
    print(
        f"wrote {args.out_path} ({len(rows)} rows, {n_calls} calls x "
        f"{len(WHISPER_MODELS)} models); probe detected in {n_detected}/{n_calls} calls "
        f"(base); semantic_available (base) {n_available_base}/{n_calls}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
