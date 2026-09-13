#!/usr/bin/env python3
"""Builds the fc-1 feature tables from both turn sources (T016).

For every one of the 353 calls in `manifest.csv`, computes the fc-1
vector *twice*: once from the practice dataset's ground-truth
`turns/<id>.json` ("ref") and once from this system's own Rust VAD via
the release `vad-dump` binary ("vad") — the same turns shape `/detect`
produces at serving time (ADR-003, FR-004). Writes:

    ml/data/features_ref.parquet   ground-truth-turns vectors
    ml/data/features_vad.parquet   this system's own VAD vectors (the
                                    training table — train on what
                                    serving sees, FR-004/ADR-003)
    ml/data/skew_report.md         per-feature mean-abs-diff + Spearman
                                    rho between the two sources (R-01
                                    evidence)

`ml/data/` is gitignored (product/.gitignore) — never committed
(AGENTS.md, NFR-011). Requires CONCORDE_DATASET_DIR (see
product/.env.example) and a Rust toolchain to build `vad-dump` (or a
pre-built binary at CONCORDE_VAD_DUMP_BIN / target/release/vad-dump).

Usage:
    cd product/ml
    python -m train.build_dataset
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Sequence, Tuple

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from common.dataset import load_manifest, load_turns, wav_path  # noqa: E402
from features import contract  # noqa: E402
from features.extract import FEATURE_NAMES, extract  # noqa: E402

Turn = Tuple[int, float, float]
Interval = Tuple[float, float]

# product/ — two levels up from product/ml/train/build_dataset.py.
PRODUCT_DIR = Path(__file__).resolve().parents[2]

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
REF_PARQUET = DATA_DIR / "features_ref.parquet"
VAD_PARQUET = DATA_DIR / "features_vad.parquet"
SKEW_REPORT = DATA_DIR / "skew_report.md"

# Below this Spearman rho, a feature is flagged "VAD-sensitive" (R-01 evidence).
SKEW_CORRELATION_FLOOR = 0.7


def vad_dump_binary_path() -> Path:
    """Path to the release `vad-dump` binary.

    Overridable via CONCORDE_VAD_DUMP_BIN (e.g. a pre-built binary on a
    machine without a Rust toolchain); defaults to the workspace's own
    `target/release/vad-dump`.
    """
    override = os.environ.get("CONCORDE_VAD_DUMP_BIN")
    if override:
        return Path(override)
    return PRODUCT_DIR / "target" / "release" / "vad-dump"


def ensure_vad_dump_built() -> Path:
    """Returns the `vad-dump` binary, building it in release mode if missing."""
    binary = vad_dump_binary_path()
    if binary.is_file():
        return binary
    result = subprocess.run(
        ["cargo", "build", "--release", "--bin", "vad-dump"],
        cwd=PRODUCT_DIR,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not binary.is_file():
        raise RuntimeError(
            "failed to build vad-dump (`cargo build --release --bin vad-dump` "
            f"in {PRODUCT_DIR}); set CONCORDE_VAD_DUMP_BIN to a pre-built "
            f"binary if no Rust toolchain is available here.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return binary


def run_vad_dump(binary: Path, wav: Path) -> List[Turn]:
    """Runs `vad-dump` on one WAV file, returns its (channel, start, end) turns."""
    result = subprocess.run(
        [str(binary), str(wav)], capture_output=True, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(f"vad-dump failed on {wav}: {result.stderr.strip()}")
    raw = json.loads(result.stdout)
    turns = [(int(t["channel"]), float(t["start"]), float(t["end"])) for t in raw["turns"]]
    turns.sort(key=lambda t: t[1])
    return turns


def split_channels(turns: Sequence[Turn]) -> Tuple[List[Interval], List[Interval]]:
    """Splits (channel, start, end) turns into (caller, agent) interval lists."""
    caller = [(s, e) for ch, s, e in turns if ch == 0]
    agent = [(s, e) for ch, s, e in turns if ch == 1]
    return caller, agent


def build_table(
    manifest: pd.DataFrame,
    turns_for: Callable[[str], List[Turn]],
    source: str,
) -> pd.DataFrame:
    """One row per manifest call: metadata + fc-1 vector + `source` tag.

    `turns_for(anon_id)` supplies that call's (channel, start, end) turns
    — `load_turns` for the "ref" table, `run_vad_dump` for "vad" — so the
    same extraction/assembly logic runs over either source.
    """
    columns = ["anon_id", "label", "split", "duration_s", *FEATURE_NAMES, "source"]
    records = []
    for _, row in manifest.iterrows():
        anon_id = row["anon_id"]
        caller, agent = split_channels(turns_for(anon_id))
        vec = extract(caller, agent, float(row["duration_s"]))
        record = {
            "anon_id": anon_id,
            "label": row["label"],
            "split": row["split"],
            "duration_s": float(row["duration_s"]),
        }
        record.update(zip(FEATURE_NAMES, (float(x) for x in vec)))
        record["source"] = source
        records.append(record)
    return pd.DataFrame.from_records(records, columns=columns)


def compute_skew_report(df_ref: pd.DataFrame, df_vad: pd.DataFrame) -> str:
    """Markdown report: per fc-1 feature, mean abs diff and Spearman rho
    between the "ref" and "vad" sources, joined on `anon_id`."""
    merged = df_ref.merge(df_vad, on="anon_id", suffixes=("_ref", "_vad"))

    lines = [
        "# VAD vs. reference-turns skew report (fc-1)",
        "",
        "One row per fc-1 feature: mean absolute difference between the value",
        "computed from the provided `turns/<id>.json` (\"ref\") and from this",
        "system's own VAD via `vad-dump` (\"vad\"), and their Spearman rank",
        "correlation across all matched calls. Features with correlation",
        f"below {SKEW_CORRELATION_FLOOR} (or undefined, because one source is",
        "constant across every call) are flagged **VAD-sensitive** — R-01",
        "evidence that training/serving skew on that feature needs extra",
        "attention (training still happens on `features_vad`, never",
        "`features_ref` — FR-004/ADR-003).",
        "",
        f"Calls compared: {len(merged)}",
        "",
        "| feature | mean abs diff | spearman rho | flag |",
        "|---|---|---|---|",
    ]

    flagged: List[str] = []
    for name in FEATURE_NAMES:
        ref_col = merged[f"{name}_ref"].to_numpy(dtype=np.float64)
        vad_col = merged[f"{name}_vad"].to_numpy(dtype=np.float64)
        mean_abs_diff = float(np.mean(np.abs(ref_col - vad_col)))

        if len(ref_col) < 2 or np.std(ref_col) == 0.0 or np.std(vad_col) == 0.0:
            rho = float("nan")
        else:
            rho, _p = spearmanr(ref_col, vad_col)
            rho = float(rho)

        is_undefined = rho != rho  # NaN-safe
        is_sensitive = is_undefined or rho < SKEW_CORRELATION_FLOOR
        if is_sensitive:
            flagged.append(name)

        rho_str = "n/a (constant)" if is_undefined else f"{rho:.3f}"
        flag_str = "VAD-sensitive" if is_sensitive else ""
        lines.append(f"| {name} | {mean_abs_diff:.4f} | {rho_str} | {flag_str} |")

    lines += [
        "",
        f"**{len(flagged)}/{len(FEATURE_NAMES)} features flagged VAD-sensitive** "
        f"(rho < {SKEW_CORRELATION_FLOOR} or undefined): "
        + (", ".join(flagged) if flagged else "none") + ".",
        "",
    ]
    return "\n".join(lines) + "\n"


def main() -> None:
    contract.assert_contract_matches_extractor()

    manifest = load_manifest()
    binary = ensure_vad_dump_built()

    df_ref = build_table(manifest, load_turns, source="ref")
    df_vad = build_table(
        manifest,
        lambda anon_id: run_vad_dump(binary, wav_path(anon_id)),
        source="vad",
    )

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    df_ref.to_parquet(REF_PARQUET, engine="pyarrow", index=False)
    df_vad.to_parquet(VAD_PARQUET, engine="pyarrow", index=False)
    SKEW_REPORT.write_text(compute_skew_report(df_ref, df_vad), encoding="utf-8")

    print(f"wrote {REF_PARQUET} ({len(df_ref)} rows)")
    print(f"wrote {VAD_PARQUET} ({len(df_vad)} rows)")
    print(f"wrote {SKEW_REPORT}")


if __name__ == "__main__":
    main()
