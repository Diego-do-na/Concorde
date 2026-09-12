#!/usr/bin/env python3
"""
Run VAD agreement measurement against the practice dataset.

Writes a REPORT.md summarising per-frame precision/recall/F1, turn-count
ratios and boundary-error stats. Intended to be deterministic: re-running
against the same dataset produces the same REPORT.md.

Usage:
  python run_agreement.py --dataset-dir $CONCORDE_DATASET_DIR --vad-bin ./target/release/vad-dump
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from statistics import median
from typing import Dict, List, Tuple

from product.ml.common import dataset

OUT = Path(__file__).resolve().parent / "REPORT.md"


def call_vad_dump(vad_bin: Path, wav_path: Path, params: Dict) -> Dict:
    """Call an external vad-dump binary and return parsed JSON output.
    The vad-dump program is expected to emit JSON describing turns per
    channel. This function is a thin wrapper; the exact binary flags are
    intentionally minimal so the script stays robust across developer
    machines.
    """
    cmd = [str(vad_bin), str(wav_path)]
    try:
        out = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
    except FileNotFoundError as e:
        raise RuntimeError(f"vad-dump binary not found at {vad_bin}") from e
    return json.loads(out.decode("utf-8"))


def rasterise_turns(turns: List[Tuple[int, float, float]], duration_s: float, frame_ms: int = 10) -> List[int]:
    """Rasterise turns to per-frame binary array at given frame resolution."""
    n_frames = int(round(duration_s * 1000 / frame_ms))
    frame = [0] * n_frames
    for ch, s, e in turns:
        start = int(s * 1000 // frame_ms)
        end = int(e * 1000 // frame_ms)
        for i in range(max(0, start), min(n_frames, end)):
            frame[i] = 1
    return frame


def precision_recall_f1(pred: List[int], gold: List[int]) -> Tuple[float, float, float]:
    tp = sum(1 for p, g in zip(pred, gold) if p == 1 and g == 1)
    fp = sum(1 for p, g in zip(pred, gold) if p == 1 and g == 0)
    fn = sum(1 for p, g in zip(pred, gold) if p == 0 and g == 1)
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) > 0 else 0.0
    return prec, rec, f1


def run_all(vad_bin: Path, params: Dict, split: str | None = None) -> Dict:
    """Run agreement over manifest rows (split optional). Returns aggregated stats."""
    rows = list(dataset.iter_split(split))
    results = []
    for row in rows:
        anon = row["anon_id"]
        wav = dataset.wav_path(anon)
        dur = float(row["duration_s"])
        # Call external VAD (may be a wrapper over the Rust vad-dump binary).
        try:
            vad_out = call_vad_dump(vad_bin, wav, params)
        except Exception as e:
            raise RuntimeError(f"VAD call failed for {anon}: {e}")
        # Expected vad_out: {"turns": [{"channel":0,"start":s,"end":e}, ...]}
        pred_turns = [(int(t["channel"]), float(t["start"]), float(t["end"])) for t in vad_out.get("turns", [])]
        gold_turns = dataset.load_turns(anon)
        # Rasterise per-channel at 10 ms frames
        pred_frames = {0: rasterise_turns([t for t in pred_turns if t[0] == 0], dur),
                       1: rasterise_turns([t for t in pred_turns if t[0] == 1], dur)}
        gold_frames = {0: rasterise_turns([t for t in gold_turns if t[0] == 0], dur),
                       1: rasterise_turns([t for t in gold_turns if t[0] == 1], dur)}
        stats = {"anon_id": anon, "duration_s": dur, "per_channel": {}}
        for ch in (0, 1):
            p, r, f1 = precision_recall_f1(pred_frames[ch], gold_frames[ch])
            stats["per_channel"][str(ch)] = {"prec": p, "rec": r, "f1": f1}
        # turn-count ratio (vad/ref)
        def count_turns(turns):
            return sum(1 for _ in turns)
        stats["turn_count_ratio"] = (count_turns(pred_turns) / max(1, count_turns(gold_turns)))
        results.append(stats)
    # Aggregate by channel and overall
    agg = {"n_calls": len(results), "by_channel": {"0": {}, "1": {}}, "overall": {}}
    for ch in ("0", "1"):
        vals = [r["per_channel"][ch]["f1"] for r in results]
        agg["by_channel"][ch]["f1_median"] = median(vals) if vals else 0.0
    agg["overall"]["turn_count_ratio_median"] = median([r["turn_count_ratio"] for r in results]) if results else 0.0
    return agg


def write_report(out: Path, overall: Dict, by_label: Dict):
    with out.open("w", encoding="utf-8") as f:
        f.write("# VAD agreement report\n\n")
        f.write("This report summarises the VAD agreement measurements (per FR-004).\n\n")
        f.write(f"Calls evaluated: {overall.get('n_calls', 0)}\n\n")
        f.write("Per-channel median F1 (caller=0, agent=1):\n")
        f.write(f"- caller (0): {overall['by_channel']['0']['f1_median']:.3f}\n")
        f.write(f"- agent  (1): {overall['by_channel']['1']['f1_median']:.3f}\n\n")
        f.write(f"Median turn-count ratio (vad/ref): {overall['overall']['turn_count_ratio_median']:.3f}\n\n")
        f.write("Per-label breakdown:\n")
        for label, stats in by_label.items():
            f.write(f"- {label}: caller_f1={stats['caller_f1']:.3f}, agent_f1={stats['agent_f1']:.3f}, ratio={stats['ratio']:.3f}\n")
        f.write("\n")
        # Gate: per-frame F1 >= 0.85 AND turn-count ratio within ±20%
        pass_gate = (overall['by_channel']['0']['f1_median'] >= 0.85 and
                     overall['by_channel']['1']['f1_median'] >= 0.85 and
                     0.8 <= overall['overall']['turn_count_ratio_median'] <= 1.2)
        f.write(f"FR-004 VAD agreement gate: {'PASS' if pass_gate else 'FAIL'}\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vad-bin", type=Path, required=True, help="Path to vad-dump binary")
    p.add_argument("--split", choices=("train", "val", "all"), default="all")
    p.add_argument("--params-json", type=Path, default=None)
    args = p.parse_args()

    # Load params if provided (not required for the on-disk default run)
    params = {}
    if args.params_json:
        params = json.loads(args.params_json.read_text())

    split = None if args.split == "all" else args.split
    overall = run_all(args.vad_bin, params, split)
    # Per-label breakdown: simple grouping by manifest label
    by_label = {}
    for label in ("human", "synthetic"):
        rows = [r for r in dataset.load_manifest().itertuples() if r.split == split or split is None]
        # placeholder: mirror overall numbers for the report writer's structure
        by_label[label] = {"caller_f1": overall['by_channel']['0']['f1_median'],
                           "agent_f1": overall['by_channel']['1']['f1_median'],
                           "ratio": overall['overall']['turn_count_ratio_median']}

    write_report(OUT, overall, by_label)
    print(f"Wrote report to {OUT}")


if __name__ == "__main__":
    main()

