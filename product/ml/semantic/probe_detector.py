#!/usr/bin/env python3
"""Probe-turn detector: log-mel + subsequence-DTW template bank (T043).

Detects the fixed agent probe turn ("esto es sobre su cuenta nómina plus o
sobre su crédito verde", see `transcribe.PROBE_PHRASE`) directly from
*audio*, with no ASR involved — this is what the Rust production detector
(T045) ports, so it can run in `/detect`'s serving path without paying
whisper.cpp's latency (ADR-003's own-VAD path already finds turn
boundaries; this module only decides *which* agent turn is the probe one).

Method (frozen, matches the reference measured 2026-09-12 — see
product/ml/semantic/DIGEST.md's sibling note and AGENTS.md §"probe
detector"): 32-band log-mel at 8 kHz (the dataset's native rate, no
upsampling needed here unlike transcribe.py's whisper path), n_fft 256
(32 ms) hop 80 (10 ms), per-band z-normalisation over the clip's own
frames, L2-normalised frame vectors, matched against a bank of reference
templates with **open-begin/open-end (subsequence) DTW** using cosine
cost, normalised by template length. A call's detector score is the min
over the template bank (best match wins); scores are picked so that
*lower is better* (0 = perfect match).

The template bank is built once from a handful of TRAIN calls whose probe
turn is short (~2.5 s — the phrase alone, no adjacent agent speech) using
T042's ground truth (`transcribe.build_probe_ground_truth`), then
validated on every call in the dataset that has ground truth (train +
val alike — this only measures the *audio* detector, ADR-012's "val is
intocable" governs model *training*, not this kind of offline QA check).
`val` is never used to pick the template bank or the threshold.

Exports `product/artifacts/probe_templates.json` — the file T045's Rust
detector loads (see product/artifacts/README.md).

Usage:
    cd product/ml
    python3 semantic/probe_detector.py                 # build + validate + export
    python3 semantic/probe_detector.py --validate-only  # re-validate the exported bank

Deliberately numpy/scipy-only (no librosa) so template-matching stays
fast and dependency-light — see DIGEST.md's per-call runtime line.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # product/ml/ on sys.path

from common.dataset import load_manifest, load_turns, wav_path  # noqa: E402
from semantic.transcribe import (  # noqa: E402
    CACHE_DIR,
    GROUND_TRUTH_PATH,
    read_stereo_wav,
)

SCHEMA_VERSION = "probe-templates-v1"

# ---- log-mel parameters (frozen; also written into the exported JSON) ----
SAMPLE_RATE = 8000          # dataset's native rate — no resampling
N_FFT = 256                 # 32 ms window @ 8 kHz
HOP = 80                    # 10 ms hop @ 8 kHz
N_MELS = 32
FMIN = 50.0
FMAX = 4000.0               # Nyquist @ 8 kHz
EPS = 1e-10

# ---- template selection ----
_TARGET_TEMPLATE_DUR_S = (1.8, 3.2)   # "the phrase alone" -- short probe turns only
_N_TEMPLATES = 3
_MIN_TURN_FRAMES = 20  # below this many frames a query can't possibly host the phrase

# transcribe.find_probe_turn() sometimes matches the probe phrase across a
# PAIR of adjacent agent turns (turns.json splits the sentence around a
# brief caller interjection or a near-silent agent blip -- see its own
# docstring) but the ground truth only records the trailing turn's index,
# not the span's start. Left uncorrected this hands us a ~0.3-0.7 s audio
# fragment (confirmed empirically: every train-positive outlier above
# score 0.59 had probe_score==1.0 text-match but a <0.75 s turn) for a
# ~2.5 s phrase, which silently poisons both template selection and
# validation. `_recover_probe_span()` below walks back over the
# immediately preceding agent turn(s) to reconstruct the full span.
_MIN_PROBE_SPAN_S = 1.2
_MAX_MERGE_GAP_S = 1.0

ARTIFACTS_DIR = Path(__file__).resolve().parents[2] / "artifacts"
EXPORT_PATH = ARTIFACTS_DIR / "probe_templates.json"


# --------------------------------------------------------------------------
# Log-mel front end (pure numpy/scipy, no librosa)
# --------------------------------------------------------------------------

def _hz_to_mel(hz: np.ndarray) -> np.ndarray:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: np.ndarray) -> np.ndarray:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _mel_filterbank(
    sr: int = SAMPLE_RATE, n_fft: int = N_FFT, n_mels: int = N_MELS,
    fmin: float = FMIN, fmax: float = FMAX,
) -> np.ndarray:
    """Triangular mel filterbank, shape (n_mels, n_fft // 2 + 1)."""
    n_bins = n_fft // 2 + 1
    mel_pts = np.linspace(_hz_to_mel(np.array(fmin)), _hz_to_mel(np.array(fmax)), n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    bin_pts = np.floor((n_fft + 1) * hz_pts / sr).astype(int)
    bin_pts = np.clip(bin_pts, 0, n_bins - 1)

    fb = np.zeros((n_mels, n_bins), dtype=np.float64)
    for m in range(1, n_mels + 1):
        left, center, right = bin_pts[m - 1], bin_pts[m], bin_pts[m + 1]
        if center == left:
            center += 1
        if right == center:
            right += 1
        for k in range(left, center):
            fb[m - 1, k] = (k - left) / (center - left)
        for k in range(center, min(right, n_bins)):
            fb[m - 1, k] = (right - k) / (right - center)
    return fb


_MEL_FB = _mel_filterbank()


def _frame_signal(samples: np.ndarray, n_fft: int = N_FFT, hop: int = HOP) -> np.ndarray:
    """(n_frames, n_fft) framed view, Hamming-windowed. `samples` is float64 in [-1, 1]."""
    n = len(samples)
    if n < n_fft:
        samples = np.pad(samples, (0, n_fft - n))
        n = n_fft
    n_frames = 1 + (n - n_fft) // hop
    if n_frames < 1:
        n_frames = 1
    window = np.hamming(n_fft)
    frames = np.empty((n_frames, n_fft), dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        frames[i] = samples[start:start + n_fft] * window
    return frames


def log_mel_spectrogram(samples_i16: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """int16 mono samples -> (n_frames, N_MELS) log-mel energies, raw (not yet normalised)."""
    if sr != SAMPLE_RATE:
        raise ValueError(f"probe_detector expects {SAMPLE_RATE} Hz audio, got {sr}")
    samples = samples_i16.astype(np.float64) / 32768.0
    frames = _frame_signal(samples)
    spectrum = np.fft.rfft(frames, n=N_FFT, axis=1)
    power = (spectrum.real ** 2 + spectrum.imag ** 2) / N_FFT
    mel_energy = power @ _MEL_FB.T  # (n_frames, N_MELS)
    return np.log(mel_energy + EPS)


def normalize_frames(log_mel: np.ndarray) -> np.ndarray:
    """Per-band z-normalisation over this clip's own frames, then L2-normalise
    each frame vector to unit norm (both over axis matching the reference
    implementation measured 2026-09-12)."""
    mean = log_mel.mean(axis=0, keepdims=True)
    std = log_mel.std(axis=0, keepdims=True)
    std[std < 1e-8] = 1.0
    z = (log_mel - mean) / std
    norms = np.linalg.norm(z, axis=1, keepdims=True)
    norms[norms < 1e-8] = 1.0
    return z / norms


def extract_features(samples_i16: np.ndarray, sr: int = SAMPLE_RATE) -> np.ndarray:
    """Full front end: int16 samples -> (n_frames, N_MELS) L2-normalised frames."""
    return normalize_frames(log_mel_spectrogram(samples_i16, sr))


# --------------------------------------------------------------------------
# Open-begin/open-end (subsequence) DTW, cosine cost
# --------------------------------------------------------------------------

def subsequence_dtw_score(template: np.ndarray, query: np.ndarray) -> float:
    """Best-matching-subsequence DTW distance of `template` (n, d) against
    `query` (m, d), both L2-normalised row-wise so cosine similarity is a
    plain dot product. Open begin AND open end on the query axis: the
    template may start matching anywhere in the query and the match may
    end anywhere -- this is what lets a short template locate the phrase
    inside a longer (or slightly clipped) query turn. Cost is normalised
    by the template length so scores are comparable across templates of
    different duration. Returns a distance (0 = perfect match, larger =
    worse); never negative.
    """
    n, m = len(template), len(query)
    if n == 0 or m == 0:
        return float("inf")
    cost = 1.0 - (template @ query.T)  # cosine distance in [0, 2]
    D = np.full((n, m), np.inf, dtype=np.float64)
    D[0, :] = cost[0, :]  # open begin: template's first frame can align anywhere in query
    for i in range(1, n):
        D[i, 0] = D[i - 1, 0] + cost[i, 0]
        for j in range(1, m):
            D[i, j] = cost[i, j] + min(D[i - 1, j], D[i, j - 1], D[i - 1, j - 1])
    return float(D[n - 1, :].min() / n)  # open end: best over any ending column


def detector_score(templates: Sequence[np.ndarray], query: np.ndarray) -> float:
    """Min-over-templates subsequence-DTW score for one query clip (best match wins)."""
    if query.shape[0] < 1:
        return float("inf")
    return min(subsequence_dtw_score(t, query) for t in templates)


# --------------------------------------------------------------------------
# Ground truth / dataset plumbing
# --------------------------------------------------------------------------

def _load_ground_truth() -> dict:
    if not GROUND_TRUTH_PATH.is_file():
        raise FileNotFoundError(
            f"{GROUND_TRUTH_PATH} not found -- run "
            "`python3 semantic/transcribe.py --models base` first (T042) to "
            "build the probe ground truth this module trains/validates against."
        )
    with GROUND_TRUTH_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _channel0_turn_samples(anon_id: str, start: float, end: float) -> np.ndarray:
    stereo, sr = read_stereo_wav(wav_path(anon_id))
    if sr != SAMPLE_RATE:
        raise ValueError(f"{anon_id}: expected {SAMPLE_RATE} Hz, got {sr}")
    s = max(0, int(round(start * sr)))
    e = min(stereo.shape[0], int(round(end * sr)))
    return stereo[s:e, 1]  # channel 1 = agent, carries the probe phrase


def _recover_probe_span(turns: Sequence[Tuple[int, float, float]], idx: int) -> Tuple[float, float]:
    """(start, end) of the full probe phrase, walking back over immediately
    preceding agent turns when `turns[idx]` alone is too short to be the
    phrase (see the module-level note next to `_MIN_PROBE_SPAN_S`)."""
    ch, start, end = turns[idx]
    j = idx - 1
    while (end - start) < _MIN_PROBE_SPAN_S and j >= 0:
        pch, pstart, pend = turns[j]
        if pch != ch or (start - pend) > _MAX_MERGE_GAP_S:
            break
        start = pstart
        j -= 1
    return start, end


def _agent_probe_clip(
    anon_id: str, gt_entry: dict
) -> Tuple[Optional[Tuple[np.ndarray, float]], Optional[str]]:
    """((samples, duration_s), reject_reason) for the ground-truth probe
    turn of one call. `reject_reason` is None on success; otherwise the
    clip is None and the reason says why this call's ground truth was
    excluded rather than silently used:

      - "no_ground_truth": T042 found no probe turn for this call at all.
      - "bad_channel": the recorded index isn't an agent turn (shouldn't
        happen, defensive).
      - "implausible_duration": even after `_recover_probe_span()` tries
        to reconstruct a split phrase, the span is still too short to
        physically contain the ~2.5 s probe sentence. Empirically this is
        a real T042 ground-truth failure mode (confirmed 2026-09-12,
        T043): several `probe_found=True` entries had a perfect
        text-match score (1.0) on turns as short as 0.28-0.72 s, with no
        adjacent agent turn to merge into -- almost certainly whisper.cpp
        segment offsets reassigned to the wrong (too-short) turn window
        by `assign_segments_to_windows()`'s nearest-offset heuristic, not
        an actual split-sentence case. Per AGENTS.md rule 4/ADR-008 this
        must be excluded and counted, never silently trusted because the
        text score alone looked perfect.
    """
    if not gt_entry.get("probe_found"):
        return None, "no_ground_truth"
    turns = load_turns(anon_id)
    idx = gt_entry["probe_turn_index"]
    if idx >= len(turns) or turns[idx][0] != 1:
        return None, "bad_channel"
    start, end = _recover_probe_span(turns, idx)
    if (end - start) < _MIN_PROBE_SPAN_S:
        return None, "implausible_duration"
    return (_channel0_turn_samples(anon_id, start, end), (end - start)), None


# --------------------------------------------------------------------------
# Template bank construction
# --------------------------------------------------------------------------

def build_template_bank(gt: dict, n_templates: int = _N_TEMPLATES) -> Tuple[List[np.ndarray], List[str]]:
    """Picks `n_templates` TRAIN calls whose probe turn is short (the
    phrase alone, `_TARGET_TEMPLATE_DUR_S`) and turns each into an
    L2-normalised log-mel template. Deterministic (sorted anon_id order,
    no RNG) so re-running reproduces the exact same bank byte-for-byte."""
    manifest = load_manifest()
    train_ids = set(manifest.loc[manifest["split"] == "train", "anon_id"])

    candidates: List[Tuple[str, np.ndarray, float]] = []
    for anon_id in sorted(gt["calls"]):
        if anon_id not in train_ids:
            continue
        entry = gt["calls"][anon_id]
        clip, _reject_reason = _agent_probe_clip(anon_id, entry)
        if clip is None:
            continue
        samples, dur = clip
        if _TARGET_TEMPLATE_DUR_S[0] <= dur <= _TARGET_TEMPLATE_DUR_S[1]:
            candidates.append((anon_id, samples, dur))

    if len(candidates) < n_templates:
        raise RuntimeError(
            f"only {len(candidates)} TRAIN calls have a probe turn in "
            f"{_TARGET_TEMPLATE_DUR_S} s -- need >= {n_templates} to build the bank"
        )

    # Spread picks across the candidate pool (evenly spaced indices) rather
    # than the first N, so the bank isn't three near-duplicate recordings.
    step = len(candidates) / n_templates
    picks = [candidates[int(i * step)] for i in range(n_templates)]

    templates = [extract_features(samples) for _, samples, _ in picks]
    ids = [anon_id for anon_id, _, _ in picks]
    return templates, ids


# --------------------------------------------------------------------------
# Threshold selection + full-dataset validation
# --------------------------------------------------------------------------

def score_all_calls(
    templates: Sequence[np.ndarray], gt: dict
) -> Tuple[Dict[str, dict], Dict[str, int]]:
    """(results, excluded_counts). `results[anon_id]` carries detector_score()
    plus label (positive = probe present per T042's ground truth), manifest
    split and dataset label -- everything the validation report needs.
    `excluded_counts` tallies `_agent_probe_clip()`'s `reject_reason`s over
    every `probe_found=True` call -- this is the honest count of how many
    calls this module refused to trust T042's ground truth for (see
    `_agent_probe_clip`'s docstring), reported explicitly rather than
    folded silently into "coverage"."""
    manifest = load_manifest().set_index("anon_id")
    results: Dict[str, dict] = {}
    excluded_counts: Dict[str, int] = {}
    for anon_id, entry in gt["calls"].items():
        if anon_id not in manifest.index:
            continue
        turns = load_turns(anon_id)
        # Query clip: the whole call's agent (channel 1) audio concatenated
        # -- this is what the Rust detector will actually see (it doesn't
        # know in advance which turn is the probe; it scores agent turns).
        # For validation we score the call's identified probe turn window
        # when ground truth says it's present (recall check) and, for
        # negatives / false-positive rate, every other agent turn.
        row = manifest.loc[anon_id]
        if entry.get("probe_found"):
            clip, reject_reason = _agent_probe_clip(anon_id, entry)
            if clip is None:
                excluded_counts[reject_reason] = excluded_counts.get(reject_reason, 0) + 1
                continue
            samples, _dur = clip
            query = extract_features(samples)
            score = detector_score(templates, query)
            results[anon_id] = {
                "positive": True,
                "score": score,
                "label": row["label"],
                "split": row["split"],
            }
        else:
            # No probe in ground truth -> every agent turn is a true negative
            # candidate; the call-level score is the BEST (lowest) one, i.e.
            # the hardest case for the false-positive check.
            best = float("inf")
            for ch, start, end in turns:
                if ch != 1:
                    continue
                samples = _channel0_turn_samples(anon_id, start, end)
                if len(samples) < _MIN_TURN_FRAMES * HOP:
                    continue
                query = extract_features(samples)
                best = min(best, detector_score(templates, query))
            if best == float("inf"):
                continue
            results[anon_id] = {
                "positive": False,
                "score": best,
                "label": row["label"],
                "split": row["split"],
            }
    return results, excluded_counts


def pick_threshold(results: Dict[str, dict]) -> Tuple[float, float, float]:
    """(threshold, present_bound, absent_bound), all derived from TRAIN
    scores only (ADR-012 -- val never influences this).

    The two bounds are each a *safety* guarantee against TRAIN, not a
    plain best-vs-worst split -- get the direction wrong here (an earlier
    version of this function did) and "present_bound" ends up above real
    negative scores, which is precisely how a detector manufactures
    confident false positives instead of preventing them:

    - `present_bound` must sit strictly BELOW every TRAIN negative score
      (`min(neg_scores)`) -- otherwise some negative would score `<=
      present_bound` and get classified `True`, a real false positive.
    - `absent_bound` must sit strictly ABOVE every TRAIN positive score
      (`max(pos_scores)`) -- otherwise some positive would score `>=
      absent_bound` and get classified `False` with unwarranted confidence.

    When the classes separate cleanly on TRAIN (`max(pos) < min(neg)`),
    those two bounds ARE `max(pos)`/`min(neg)` themselves -- the widest
    zone that's still safe. When TRAIN scores overlap (`max(pos) >=
    min(neg)`, the realistic case for a 3-template audio-only bank), the
    naive bounds would be unsafe, so the zone is shrunk inward by an
    epsilon past each observed extreme instead -- this widens the
    **ambiguous zone** to swallow the whole overlap region rather than
    resolve it by guessing. A score inside that zone gets neither "closer
    to the positives" nor "closer to the negatives" from any evidence
    this bank actually has -- see `classify_score()`, which is what
    callers must use instead of a bare `score <= threshold` comparison
    (AGENTS.md rule 4 / ADR-008: an unavailable signal must degrade to
    null, logged, never be silently guessed one way or the other).
    `threshold` (the zone's midpoint) is kept only as a single-number
    summary for logging/back-compat -- it is NOT what `classify_score()`
    uses, and is not itself safe to threshold against.
    """
    train = {k: v for k, v in results.items() if v["split"] == "train"}
    pos_scores = [v["score"] for v in train.values() if v["positive"]]
    neg_scores = [v["score"] for v in train.values() if not v["positive"]]
    if not pos_scores or not neg_scores:
        raise RuntimeError("need both positive and negative TRAIN scores to pick a threshold")
    worst_pos = max(pos_scores)
    best_neg = min(neg_scores)
    eps = 1e-6
    if worst_pos < best_neg:
        present_bound, absent_bound = worst_pos, best_neg
    else:
        present_bound, absent_bound = best_neg - eps, worst_pos + eps
    assert present_bound < absent_bound  # true by construction in both branches above
    threshold = round((present_bound + absent_bound) / 2.0, 6)
    return threshold, round(present_bound, 6), round(absent_bound, 6)


def classify_score(score: float, present_bound: float, absent_bound: float) -> Optional[bool]:
    """True = probe confidently present, False = confidently absent, None =
    **ambiguous -- degrade to "probe not detected" for F-23 purposes**
    rather than guess (this IS the "unmarked degradation is forbidden"
    rule from AGENTS.md/ADR-008, applied to this detector: the caller must
    log/mark the degradation, never silently impute a verdict here).

    `score <= present_bound` means at least as good a match as the worst
    training positive ever was -- confidently present. `score >=
    absent_bound` means at least as bad a match as the best training
    negative ever was -- confidently absent. Anything strictly between the
    two bounds is outside what this bank has direct evidence for either
    way, so it returns None instead of picking a side."""
    if score <= present_bound:
        return True
    if score >= absent_bound:
        return False
    return None


def validation_report(
    results: Dict[str, dict], threshold: float, present_bound: float, absent_bound: float,
) -> dict:
    """Two views of the same scores, on purpose:

    - recall/precision/false_positive_rate use the bare `score <= threshold`
      cut -- this is what T043(b)'s pass/fail gates (recall >= 0.90, FPR
      <= 0.10) are checked against, and matches "positives scored <=
      0.440, true negatives >= 0.449" style reporting.
    - `ambiguous` uses `classify_score()` (present_bound/absent_bound) to
      show what the detector's ACTUAL degrade-to-null rule does with the
      same scores: `forced_false_positives_avoided` is the count of
      negative calls that the bare threshold cut would have called a
      false positive but the bounds correctly demote to "ambiguous" (None,
      not a guess) instead -- the concrete evidence that this isn't just
      a threshold with a different name.
    """
    positives = [v for v in results.values() if v["positive"]]
    negatives = [v for v in results.values() if not v["positive"]]

    tp = sum(1 for v in positives if v["score"] <= threshold)
    fp = sum(1 for v in negatives if v["score"] <= threshold)
    recall = tp / len(positives) if positives else 0.0
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    fpr = fp / len(negatives) if negatives else 0.0

    by_label: Dict[str, dict] = {}
    for label in ("human", "synthetic"):
        subset = [v for v in positives if v["label"] == label]
        detected = sum(1 for v in subset if v["score"] <= threshold)
        by_label[label] = {
            "n": len(subset),
            "detected": detected,
            "rate": round(detected / len(subset), 4) if subset else 0.0,
        }
    rates = [d["rate"] for d in by_label.values() if d["n"] > 0]
    label_gap = round(max(rates) - min(rates), 4) if len(rates) == 2 else 0.0

    margins = [v["score"] for v in positives] + [v["score"] for v in negatives]

    pos_calls = [classify_score(v["score"], present_bound, absent_bound) for v in positives]
    neg_calls = [classify_score(v["score"], present_bound, absent_bound) for v in negatives]
    ambiguous_positive = sum(1 for c in pos_calls if c is None)
    ambiguous_negative = sum(1 for c in neg_calls if c is None)
    confident_false_positives = sum(1 for c in neg_calls if c is True)  # a real problem if > 0
    forced_false_positives_avoided = sum(
        1 for v, c in zip(negatives, neg_calls) if v["score"] <= threshold and c is None
    )

    return {
        "n_positive": len(positives),
        "n_negative": len(negatives),
        "recall": round(recall, 4),
        "precision": round(precision, 4),
        "false_positive_rate": round(fpr, 4),
        "threshold": threshold,
        "detection_rate_by_label": by_label,
        "detection_rate_gap": label_gap,
        "score_min": round(min(margins), 4) if margins else None,
        "score_max": round(max(margins), 4) if margins else None,
        "positive_score_max": round(max((v["score"] for v in positives), default=0.0), 4),
        "negative_score_min": round(min((v["score"] for v in negatives), default=0.0), 4),
        "ambiguous_zone": {
            "present_bound": present_bound,
            "absent_bound": absent_bound,
            "n_ambiguous_positive": ambiguous_positive,
            "n_ambiguous_negative": ambiguous_negative,
            "ambiguous_rate": round(
                (ambiguous_positive + ambiguous_negative) / max(len(results), 1), 4
            ),
            "confident_false_positives": confident_false_positives,
            "forced_false_positives_avoided": forced_false_positives_avoided,
        },
    }


# --------------------------------------------------------------------------
# Export
# --------------------------------------------------------------------------

def export_bank(
    templates: Sequence[np.ndarray],
    template_ids: Sequence[str],
    threshold: float,
    present_bound: float,
    absent_bound: float,
) -> dict:
    payload = {
        "version": SCHEMA_VERSION,
        "mel_params": {
            "sample_rate": SAMPLE_RATE,
            "n_fft": N_FFT,
            "hop": HOP,
            "n_mels": N_MELS,
            "fmin": FMIN,
            "fmax": FMAX,
        },
        "templates": [t.tolist() for t in templates],
        "template_source_calls": list(template_ids),
        "threshold": threshold,
        "min_turn_frames": _MIN_TURN_FRAMES,
        # AGENTS.md rule 4 / ADR-008: a score strictly between these two
        # bounds must degrade to "probe not detected" (F-23 unavailable,
        # logged) rather than be forced into a present/absent guess --
        # see classify_score(). `threshold` above is only their midpoint,
        # kept for any consumer that wants one summary number.
        "ambiguity_bounds": {"present_le": present_bound, "absent_ge": absent_bound},
    }
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EXPORT_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(payload, f)
    tmp.replace(EXPORT_PATH)
    return payload


def load_bank(path: Path = EXPORT_PATH) -> Tuple[List[np.ndarray], float, dict]:
    with path.open("r", encoding="utf-8") as f:
        payload = json.load(f)
    templates = [np.array(t, dtype=np.float64) for t in payload["templates"]]
    return templates, payload["threshold"], payload


def classify(score: float, payload: dict) -> Optional[bool]:
    """classify_score() against a loaded bank's exported ambiguity bounds --
    the function T045's Rust port and any other consumer should call
    instead of comparing against `threshold` directly."""
    bounds = payload["ambiguity_bounds"]
    return classify_score(score, bounds["present_le"], bounds["absent_ge"])


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validate-only", action="store_true", help="skip (re)building the bank; validate the exported one")
    parser.add_argument("--n-templates", type=int, default=_N_TEMPLATES)
    args = parser.parse_args(argv)

    gt = _load_ground_truth()

    if args.validate_only:
        templates, threshold, payload = load_bank()
        bounds = payload["ambiguity_bounds"]
        present_bound, absent_bound = bounds["present_le"], bounds["absent_ge"]
        print(f"[probe_detector] loaded bank v{payload['version']}, {len(templates)} templates, threshold={threshold}")
        t0 = time.time()
        results, excluded = score_all_calls(templates, gt)
        per_call_s = (time.time() - t0) / max(len(results), 1)
        report = validation_report(results, threshold, present_bound, absent_bound)
        report["excluded_ground_truth"] = excluded
        print(json.dumps(report, indent=2))
        print(f"[probe_detector] mean runtime per call: {per_call_s * 1000:.1f} ms (informational)")
        return 0

    templates, template_ids = build_template_bank(gt, args.n_templates)
    print(f"[probe_detector] built {len(templates)} templates from {template_ids}")
    t0 = time.time()
    results, excluded = score_all_calls(templates, gt)
    per_call_s = (time.time() - t0) / max(len(results), 1)
    print(f"[probe_detector] excluded ground-truth calls (train+val): {excluded}")
    print(f"[probe_detector] mean runtime per call: {per_call_s * 1000:.1f} ms (informational)")
    threshold, present_bound, absent_bound = pick_threshold(results)

    # Validate BEFORE exporting, not after: confident_false_positives is
    # the real safety gate for this detector (ambiguous_rate is a known,
    # reported limitation, never a reason to block export -- see
    # product/ml/README.md's probe-detector section). Refusing to write
    # the artifact when a negative call would be confidently misclassified
    # is the whole point of computing this bound in the first place.
    report = validation_report(results, threshold, present_bound, absent_bound)
    report["excluded_ground_truth"] = excluded
    print(json.dumps(report, indent=2))
    confident_fp = report["ambiguous_zone"]["confident_false_positives"]
    if confident_fp != 0:
        print(
            f"[probe_detector] REFUSING to export {EXPORT_PATH}: "
            f"confident_false_positives={confident_fp} (must be 0) -- "
            "this bank would confidently misclassify at least one "
            "probe-absent call as present",
            file=sys.stderr,
        )
        return 1

    payload = export_bank(templates, template_ids, threshold, present_bound, absent_bound)
    print(
        f"[probe_detector] exported {EXPORT_PATH} "
        f"(threshold={threshold}, ambiguous zone=({present_bound}, {absent_bound}))"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
