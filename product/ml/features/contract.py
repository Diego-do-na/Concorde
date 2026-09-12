"""Loads and validates the frozen fc-1 feature contract.

The contract file (`product/artifacts/feature_contract_fc-1.json`) is the
single source of truth for the feature vector's length and name order —
this module just asserts that `extract.py` actually matches it. Any
mismatch here means the two have drifted, which is the single most
dangerous defect in this system (AGENTS.md, §9 freezing rule): it produces
plausible-looking but wrong verdicts with no error raised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Tuple

from . import extract as extract_module

CONTRACT_ID = "fc-1"

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[2] / "artifacts" / "feature_contract_fc-1.json"
)


def contract_path() -> Path:
    return _CONTRACT_PATH


def load_contract() -> Tuple[str, Tuple[str, ...]]:
    """(contract id, feature names in order) from the frozen JSON file."""
    with _CONTRACT_PATH.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    return raw["contract"], tuple(raw["features"])


def assert_contract_matches_extractor() -> None:
    """Raises AssertionError if extract.py's names/order diverge from fc-1."""
    contract_id, names = load_contract()
    assert contract_id == CONTRACT_ID, (
        f"feature_contract_fc-1.json declares contract={contract_id!r}, expected {CONTRACT_ID!r}"
    )
    assert names == extract_module.FEATURE_NAMES, (
        "feature_contract_fc-1.json features do not match extract.FEATURE_NAMES:\n"
        f"  contract: {names}\n"
        f"  extract:  {extract_module.FEATURE_NAMES}"
    )
