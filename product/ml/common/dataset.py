"""Shared dataset access for the CONCORDE offline ML pipeline (ml/).

Every script under `ml/` reads the altur-challenge practice dataset through
this module instead of hand-rolling paths — it is the single place that
knows the on-disk layout, so a change to that layout (or to where the
dataset lives on a given machine) only has to happen here.

Dataset layout (never committed — see AGENTS.md §"No dataset in the repo"
and NFR-011):

    <CONCORDE_DATASET_DIR>/
        manifest.csv        anon_id,label,split,duration_s
        audio/<anon_id>.wav stereo 8 kHz 16-bit PCM; channel 0 = caller, channel 1 = agent
        turns/<anon_id>.json {"turns": [{"channel": 0|1, "start": <s>, "end": <s>}, ...]}

`turns/<id>.json` exists only in this practice dataset (§9, ADR-003) — the
served system computes its own turns via VAD. Everything here is read-only
and offline; nothing in this module is ever imported by `api/`.
"""

from __future__ import annotations

import functools
import json
import os
from pathlib import Path
from typing import Iterator, List, Tuple

import pandas as pd

# Diego's original laptop path (see product/.env.example) — kept as the
# fallback so scripts work out of the box on his machine; every other
# machine sets CONCORDE_DATASET_DIR (product/.env) to override it.
_DEFAULT_DATASET_DIR = "/Users/diego/Desktop/Proyects/HackMty/hackmty26"

_VALID_SPLITS = ("train", "val")


def dataset_dir() -> Path:
    """Root of the practice dataset, from $CONCORDE_DATASET_DIR or the default."""
    return Path(os.environ.get("CONCORDE_DATASET_DIR", _DEFAULT_DATASET_DIR))


def _require_dataset_dir() -> Path:
    root = dataset_dir()
    if not root.is_dir():
        raise FileNotFoundError(
            f"CONCORDE dataset dir not found: {root}\n"
            "Set CONCORDE_DATASET_DIR (see product/.env.example) to the "
            "directory containing manifest.csv, audio/ and turns/. The "
            "dataset is never committed to this repo (AGENTS.md, NFR-011)."
        )
    return root


def manifest_path() -> Path:
    return _require_dataset_dir() / "manifest.csv"


@functools.lru_cache(maxsize=1)
def load_manifest() -> pd.DataFrame:
    """The full manifest: columns anon_id, label, split, duration_s.

    label is one of {"human", "synthetic"}; split is one of {"train", "val"}.
    Cached in-process — the manifest is small (353 rows) and read-only for
    the lifetime of any one script invocation.
    """
    path = manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"manifest.csv not found at {path}")
    df = pd.read_csv(path, dtype={"anon_id": str, "label": str, "split": str})
    df["duration_s"] = df["duration_s"].astype(float)
    return df


def turns_path(anon_id: str) -> Path:
    return _require_dataset_dir() / "turns" / f"{anon_id}.json"


def load_turns(anon_id: str) -> List[Tuple[int, float, float]]:
    """Speech turns for one call, flattened and time-sorted.

    Returns a list of (channel, start, end) tuples — channel 0 is the
    caller, channel 1 is the bank agent (FR-001), start/end are seconds as
    a half-open interval [start, end), matching §9's notation. This is the
    practice-only ground truth (turns/<anon_id>.json, ADR-003) — the served
    system never has this file and must reproduce it via its own VAD.
    """
    path = turns_path(anon_id)
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    turns = [
        (int(t["channel"]), float(t["start"]), float(t["end"]))
        for t in raw["turns"]
    ]
    turns.sort(key=lambda t: t[1])
    return turns


def wav_path(anon_id: str) -> Path:
    """Path to the raw audio for one call. Never opened by anything in api/."""
    return _require_dataset_dir() / "audio" / f"{anon_id}.wav"


def iter_split(split: str | None = None) -> Iterator[pd.Series]:
    """Yield manifest rows (as pandas Series) for one split, or all rows.

    `split` is "train", "val", or None for every row. Per ADR-012, `val` is
    intocable outside final measurement/calibration — callers doing model
    selection must pass split="train" explicitly, never rely on the default.
    """
    if split is not None and split not in _VALID_SPLITS:
        raise ValueError(f"split must be one of {_VALID_SPLITS} or None, got {split!r}")
    df = load_manifest()
    if split is not None:
        df = df[df["split"] == split]
    for _, row in df.iterrows():
        yield row
