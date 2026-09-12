#!/usr/bin/env python3
"""Reproduces spec §10.1's four mandatory EDA gates (phase F0, owner: Diego).

Gates:
    1. Class balance per split.
    2. Overlap-event density (R-04 decision gate).
    3. Duration-vs-label correlation (duration must never leak the label).
    4. Manifest hash + WAV format census (NFR-009 traceability).

Usage:
    python ml/eda/run_eda.py

Writes ml/eda/REPORT.md (deterministic — re-running produces byte-identical
output given the same dataset) and ml/eda/*.png (gitignored, per §13.4).
Requires CONCORDE_DATASET_DIR to point at the practice dataset (never
committed — see product/.env.example and AGENTS.md).
"""

from __future__ import annotations

import hashlib
import sys
import wave
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless: this script never opens a display
import matplotlib.pyplot as plt
import numpy as np
from sklearn.metrics import roc_auc_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.dataset import (  # noqa: E402
    dataset_dir,
    load_manifest,
    load_turns,
    manifest_path,
    wav_path,
)

EDA_DIR = Path(__file__).resolve().parent
REPORT_PATH = EDA_DIR / "REPORT.md"

# Decision thresholds from §10.1.
CLASS_IMBALANCE_GATE = 0.60  # literal gate: majority-class fraction strictly beyond 60/40
# Practical margin below the literal gate: train's measured split (59.9/40.1,
# see REPORT.md) sits *on* the 60/40 boundary without literally crossing it.
# Treating that as a non-event would defeat the gate's purpose, so
# class_weight is applied whenever train is at-or-near it, not only when it's
# strictly exceeded. This mirrors the task's own recorded decision.
CLASS_WEIGHT_MARGIN = 0.55
OVERLAP_DENSITY_GATE = 0.40  # below this fraction, R-04 triggers, reweight toward F-01..F-06/F-21
DURATION_AUC_GATE = 0.60  # at/above this, duration leaks the label and is banned


# ---------------------------------------------------------------------------
# Gate 1: class balance
# ---------------------------------------------------------------------------


def gate_class_balance(manifest):
    rows = []
    imbalance_flagged = False
    train_majority_frac = None
    for split in ("train", "val"):
        sub = manifest[manifest["split"] == split]
        n = len(sub)
        n_human = int((sub["label"] == "human").sum())
        n_synth = int((sub["label"] == "synthetic").sum())
        pct_human = 100.0 * n_human / n if n else float("nan")
        pct_synth = 100.0 * n_synth / n if n else float("nan")
        majority_frac = max(n_human, n_synth) / n if n else float("nan")
        if majority_frac > CLASS_IMBALANCE_GATE:
            imbalance_flagged = True
        if split == "train":
            train_majority_frac = majority_frac
        rows.append(
            {
                "split": split,
                "n": n,
                "n_human": n_human,
                "n_synthetic": n_synth,
                "pct_human": pct_human,
                "pct_synthetic": pct_synth,
            }
        )
    # The literal gate ("exceeds 60/40") only fires strictly past the line;
    # the training decision instead uses a small margin below it (see
    # CLASS_WEIGHT_MARGIN) so a split sitting *on* the boundary — like this
    # dataset's train at 59.9/40.1 — still gets class_weight applied.
    apply_class_weight = (train_majority_frac or 0.0) >= CLASS_WEIGHT_MARGIN
    return {
        "rows": rows,
        "imbalance_beyond_60_40": imbalance_flagged,
        "apply_class_weight": apply_class_weight,
        "decision": (
            "apply class_weight in training (train sits on/over the 60/40 boundary)"
            if apply_class_weight
            else "no class_weight required"
        ),
    }


# ---------------------------------------------------------------------------
# Gate 2: overlap density (R-04)
# ---------------------------------------------------------------------------


def _count_overlap_events(turns):
    """Count caller/agent turn pairs whose intervals intersect.

    An "overlap event" is one (caller turn, agent turn) pair with a
    non-empty intersection — the raw signal F-07..F-13 are built from
    (§9). O(n*m) per call; call turn counts are small (tens), so this is
    fine for ~350 calls.
    """
    caller = sorted((s, e) for ch, s, e in turns if ch == 0)
    agent = sorted((s, e) for ch, s, e in turns if ch == 1)
    events = 0
    for cs, ce in caller:
        for as_, ae in agent:
            if cs < ae and as_ < ce:
                events += 1
    return events


def gate_overlap_density(manifest):
    per_call = []
    for _, row in manifest.iterrows():
        turns = load_turns(row["anon_id"])
        events = _count_overlap_events(turns)
        per_call.append({"anon_id": row["anon_id"], "label": row["label"], "events": events})

    def frac_and_median(rows):
        n = len(rows)
        if n == 0:
            return float("nan"), float("nan")
        has_overlap = sum(1 for r in rows if r["events"] >= 1)
        median_events = float(np.median([r["events"] for r in rows]))
        return has_overlap / n, median_events

    overall_frac, overall_median = frac_and_median(per_call)
    human_rows = [r for r in per_call if r["label"] == "human"]
    synth_rows = [r for r in per_call if r["label"] == "synthetic"]
    human_frac, human_median = frac_and_median(human_rows)
    synth_frac, synth_median = frac_and_median(synth_rows)

    triggered = overall_frac < OVERLAP_DENSITY_GATE
    return {
        "overall_frac": overall_frac,
        "human_frac": human_frac,
        "synthetic_frac": synth_frac,
        "overall_median_events": overall_median,
        "human_median_events": human_median,
        "synthetic_median_events": synth_median,
        "r04_triggered": triggered,
        "decision": (
            "R-04 triggered: reweight toward F-01..F-06 and F-21, escalate semantic layer to MUST"
            if triggered
            else "R-04 NOT triggered: F-07..F-13 keep full weight"
        ),
    }


# ---------------------------------------------------------------------------
# Gate 3: duration vs label
# ---------------------------------------------------------------------------


def gate_duration_vs_label(manifest):
    y = (manifest["label"] == "synthetic").astype(int).to_numpy()
    duration = manifest["duration_s"].to_numpy(dtype=float)

    mean_human = float(manifest.loc[manifest["label"] == "human", "duration_s"].mean())
    mean_synth = float(manifest.loc[manifest["label"] == "synthetic", "duration_s"].mean())

    # Point-biserial correlation of a continuous variable against a binary
    # label is just the Pearson correlation between them.
    point_biserial_r = float(np.corrcoef(duration, y)[0, 1])

    auc = float(roc_auc_score(y, duration))
    discriminative_power = max(auc, 1.0 - auc)
    banned = discriminative_power >= DURATION_AUC_GATE

    return {
        "mean_duration_human_s": mean_human,
        "mean_duration_synthetic_s": mean_synth,
        "point_biserial_r": point_biserial_r,
        "auc": auc,
        "discriminative_power": discriminative_power,
        "duration_banned": banned,
        "decision": (
            "duration excluded as a feature (leaks the label)"
            if banned
            else "duration does not leak the label; excluded anyway — not in fc-1"
        ),
    }


# ---------------------------------------------------------------------------
# Gate 4: manifest hash + WAV format census
# ---------------------------------------------------------------------------


def gate_manifest_and_wav(manifest):
    path = manifest_path()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()

    formats = {}
    bad = []
    for anon_id in manifest["anon_id"]:
        p = wav_path(anon_id)
        with wave.open(str(p), "rb") as w:
            key = (w.getnchannels(), w.getframerate(), w.getsampwidth())
        formats[key] = formats.get(key, 0) + 1
        if key != (2, 8000, 2):
            bad.append((anon_id, key))

    has_speaker_id_column = "speaker_id" in manifest.columns or "speaker" in manifest.columns

    return {
        "manifest_sha256": digest,
        "n_wavs_checked": len(manifest),
        "formats": formats,
        "all_stereo_8k_16bit": not bad,
        "nonconforming_wavs": bad,
        "has_speaker_id_column": has_speaker_id_column,
    }


# ---------------------------------------------------------------------------
# Plots
# ---------------------------------------------------------------------------


def make_plots(manifest, overlap_result):
    fig, ax = plt.subplots(figsize=(5, 4))
    for label, color in (("human", "#4c9a5a"), ("synthetic", "#c0433d")):
        ax.hist(
            manifest.loc[manifest["label"] == label, "duration_s"],
            bins=20,
            alpha=0.6,
            label=label,
            color=color,
        )
    ax.set_xlabel("duration (s)")
    ax.set_ylabel("count")
    ax.set_title("Call duration by label")
    ax.legend()
    fig.tight_layout()
    fig.savefig(EDA_DIR / "duration_by_label.png", dpi=120)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(4, 4))
    fracs = [overlap_result["human_frac"], overlap_result["synthetic_frac"]]
    ax.bar(["human", "synthetic"], fracs, color=["#4c9a5a", "#c0433d"])
    ax.axhline(OVERLAP_DENSITY_GATE, linestyle="--", color="gray", linewidth=1)
    ax.set_ylim(0, 1)
    ax.set_ylabel("fraction of calls with >=1 overlap event")
    ax.set_title("Overlap density (R-04 gate)")
    fig.tight_layout()
    fig.savefig(EDA_DIR / "overlap_density.png", dpi=120)
    plt.close(fig)


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_report(class_balance, overlap, duration, manifest_gate) -> str:
    lines = []
    lines.append("# CONCORDE — EDA Gates Report (spec §10.1)")
    lines.append("")
    lines.append(f"Dataset dir: `{dataset_dir()}`")
    lines.append("")

    lines.append("## Gate 1 — class balance per split")
    lines.append("")
    lines.append("| split | n | human | synthetic | % human | % synthetic |")
    lines.append("|---|---|---|---|---|---|")
    for r in class_balance["rows"]:
        lines.append(
            f"| {r['split']} | {r['n']} | {r['n_human']} | {r['n_synthetic']} "
            f"| {r['pct_human']:.1f} | {r['pct_synthetic']:.1f} |"
        )
    lines.append("")
    lines.append(f"Beyond 60/40 in any split (literal gate): **{class_balance['imbalance_beyond_60_40']}**.")
    lines.append(f"Apply class_weight: **{class_balance['apply_class_weight']}**.")
    lines.append(f"Decision: {class_balance['decision']}.")
    lines.append("")

    lines.append("## Gate 2 — overlap-event density (R-04)")
    lines.append("")
    lines.append("| population | fraction with >=1 overlap event | median overlap events |")
    lines.append("|---|---|---|")
    lines.append(f"| overall | {overlap['overall_frac']*100:.1f}% | {overlap['overall_median_events']:.1f} |")
    lines.append(f"| human | {overlap['human_frac']*100:.1f}% | {overlap['human_median_events']:.1f} |")
    lines.append(f"| synthetic | {overlap['synthetic_frac']*100:.1f}% | {overlap['synthetic_median_events']:.1f} |")
    lines.append("")
    lines.append(
        f"R-04 gate (< {OVERLAP_DENSITY_GATE*100:.0f}% of calls with overlap): "
        f"**{'TRIGGERED' if overlap['r04_triggered'] else 'not triggered'}**."
    )
    lines.append(f"Decision: {overlap['decision']}.")
    lines.append("")

    lines.append("## Gate 3 — duration vs label")
    lines.append("")
    lines.append(f"Mean duration — human: {duration['mean_duration_human_s']:.1f} s, "
                  f"synthetic: {duration['mean_duration_synthetic_s']:.1f} s.")
    lines.append(f"Point-biserial correlation (duration, is_synthetic): {duration['point_biserial_r']:.3f}.")
    lines.append(f"AUC of duration alone: {duration['auc']:.3f} "
                 f"(discriminative power {duration['discriminative_power']:.3f}).")
    lines.append(
        f"Duration-as-shortcut gate (discriminative power >= {DURATION_AUC_GATE}): "
        f"**{'BANNED' if duration['duration_banned'] else 'not triggered'}**."
    )
    lines.append(f"Decision: {duration['decision']}.")
    lines.append("")

    lines.append("## Gate 4 — manifest hash + WAV format census")
    lines.append("")
    lines.append(f"`manifest.csv` sha256: `{manifest_gate['manifest_sha256']}`")
    lines.append("")
    lines.append(f"WAV files checked: {manifest_gate['n_wavs_checked']}")
    lines.append("")
    lines.append("| (channels, sample_rate_hz, sample_width_bytes) | count |")
    lines.append("|---|---|")
    for key, count in sorted(manifest_gate["formats"].items()):
        lines.append(f"| {key} | {count} |")
    lines.append("")
    lines.append(
        f"All stereo/8 kHz/16-bit: **{manifest_gate['all_stereo_8k_16bit']}**."
    )
    if manifest_gate["nonconforming_wavs"]:
        lines.append("")
        lines.append("Non-conforming files:")
        for anon_id, key in manifest_gate["nonconforming_wavs"]:
            lines.append(f"- `{anon_id}`: {key}")
    lines.append("")
    lines.append(
        f"speaker-id column present in manifest.csv: **{manifest_gate['has_speaker_id_column']}**. "
        "No speaker identifier exists in this dataset, so ADR-012's "
        "\"speaker-grouped CV inside train\" is not literally implementable. "
        "Fallback for T016/T017: stratified K-fold by anon_id inside train; "
        "val remains the only speaker-disjoint measurement — never approximate "
        "grouping by clustering voices (that would itself be a form of "
        "speaker identification, forbidden by §13.4/NFR-011)."
    )
    lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    manifest = load_manifest()

    class_balance = gate_class_balance(manifest)
    overlap = gate_overlap_density(manifest)
    duration = gate_duration_vs_label(manifest)
    manifest_gate = gate_manifest_and_wav(manifest)

    make_plots(manifest, overlap)

    report = render_report(class_balance, overlap, duration, manifest_gate)
    REPORT_PATH.write_text(report, encoding="utf-8")

    print(report)
    print(f"Wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
