"""Tests for score_dataset.py and ab_report.py (T044).

Synthetic fixtures throughout — no real dataset/whisper cache required
(same tier as train/tests/test_calibrate.py):

- score_dataset.py's tests monkeypatch `load_turns`, the probe detector's
  scoring primitives, and the cached-transcript lookup to deterministic
  stand-ins so the *pipeline logic* (probe detection -> answer-turn
  selection -> transcript lookup -> rules.py) is exercised end-to-end
  without needing audio, whisper.cpp, or a template bank.
- ab_report.py's tests build a synthetic features_vad.parquet-shaped
  table, train a real (small) fc-1-only model + calibration.json on it
  (T017/T018's own path), synthesize a semantic_scores.csv, then run the
  full A/B + fusion pipeline against that sandbox.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

import semantic.rules as rules
import semantic.score_dataset as score_dataset
from semantic import ab_report, probe_detector
from train import calibrate as calibrate_module
from train import train as train_module
from features.extract import FEATURE_NAMES

# A 5-turn call: agent(0), caller(1), agent(2, the "probe"), caller(3, the
# answer), agent(4). Global index == position, matching turns.json/cached
# transcripts' "index" field.
FAKE_TURNS = [
    (1, 0.0, 1.0),
    (0, 1.0, 2.0),
    (1, 2.0, 4.5),
    (0, 4.5, 6.0),
    (1, 6.0, 7.0),
]

FAKE_PAYLOAD = {
    "min_turn_frames": 1,
    "ambiguity_bounds": {"present_le": 0.3, "absent_ge": 0.7},
}


@pytest.fixture(autouse=True)
def _patch_probe_scoring(monkeypatch):
    """Every agent turn "scores" via a fixed lookup by its global index --
    turn 2 (the probe) scores confidently present, turns 0 and 4 score
    confidently absent. detector_score/extract_features/classify are the
    real functions; only the audio-dependent bits are stubbed."""
    scores_by_index = {0: 0.9, 2: 0.1, 4: 0.85}

    def fake_channel0_turn_samples(anon_id, start, end):
        # Encode which turn this is via its start time (first element) so
        # the fake detector_score below can look it up; long enough to
        # clear score_dataset's min_turn_samples filter.
        return np.full(200, start, dtype=np.float64)

    def fake_extract_features(samples, sr=probe_detector.SAMPLE_RATE):
        return samples  # passthrough; detector_score is also faked below

    def fake_detector_score(templates, query):
        start = float(query[0])
        for gi, (ch, s, _e) in enumerate(FAKE_TURNS):
            if ch == 1 and s == start:
                return scores_by_index[gi]
        raise AssertionError(f"unexpected query for start={start}")

    monkeypatch.setattr(probe_detector, "_channel0_turn_samples", fake_channel0_turn_samples)
    monkeypatch.setattr(probe_detector, "extract_features", fake_extract_features)
    monkeypatch.setattr(probe_detector, "detector_score", fake_detector_score)
    monkeypatch.setattr(score_dataset, "load_turns", lambda anon_id: FAKE_TURNS)


def test_detect_probe_and_answer_index_finds_probe_and_answer():
    detected, answer_idx = score_dataset.detect_probe_and_answer_index(
        "call_x", templates=[], payload=FAKE_PAYLOAD
    )
    assert detected is True
    assert answer_idx == 3  # first channel-0 turn after global index 2


def test_detect_probe_and_answer_index_ambiguous_degrades_to_not_detected(monkeypatch):
    # Widen the ambiguity zone so the best score (0.1) is no longer
    # confidently present -- must degrade to "not detected", never guess.
    payload = {"min_turn_frames": 1, "ambiguity_bounds": {"present_le": 0.05, "absent_ge": 0.95}}
    detected, answer_idx = score_dataset.detect_probe_and_answer_index(
        "call_x", templates=[], payload=payload
    )
    assert detected is False
    assert answer_idx is None


def test_answer_text_for_model_reads_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / "cache" / "transcripts" / "base"
    cache_dir.mkdir(parents=True)
    record = {
        "anon_id": "call_x",
        "model": "base",
        "language": "es",
        "turns": [{"index": 3, "channel": 0, "start": 4.5, "end": 6.0, "text": "no tengo esa cuenta"}],
    }
    (cache_dir / "call_x.json").write_text(json.dumps(record), encoding="utf-8")
    monkeypatch.setattr(score_dataset, "cache_path", lambda anon_id, model: cache_dir / f"{anon_id}.json")

    text = score_dataset.answer_text_for_model("call_x", "base", 3)
    assert text == "no tengo esa cuenta"


def test_answer_text_for_model_missing_cache_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(score_dataset, "cache_path", lambda anon_id, model: tmp_path / "missing.json")
    assert score_dataset.answer_text_for_model("call_x", "base", 3) is None


def test_score_call_unavailable_when_probe_not_detected(monkeypatch):
    payload = {"min_turn_frames": 1, "ambiguity_bounds": {"present_le": 0.05, "absent_ge": 0.95}}
    rows = score_dataset.score_call("call_x", templates=[], payload=payload)
    assert len(rows) == len(score_dataset.WHISPER_MODELS)
    for row in rows:
        assert row["probe_detected"] is False
        assert row["semantic_available"] is False
        assert row["invention_score"] == score_dataset.NEUTRAL_INVENTION_SCORE
        assert row["answer_type"] == ""


def test_score_call_available_when_transcript_present(monkeypatch):
    def fake_answer_text_for_model(anon_id, model, answer_index):
        assert answer_index == 3
        return "no tengo esa cuenta"

    monkeypatch.setattr(score_dataset, "answer_text_for_model", fake_answer_text_for_model)
    rows = score_dataset.score_call("call_x", templates=[], payload=FAKE_PAYLOAD)
    assert len(rows) == len(score_dataset.WHISPER_MODELS)
    for row in rows:
        assert row["probe_detected"] is True
        assert row["semantic_available"] is True
        assert row["answer_type"] == "denial"
        assert row["invention_score"] == rules.invention_score("denial")


def test_score_dataset_is_idempotent(tmp_path, monkeypatch):
    def fake_answer_text_for_model(anon_id, model, answer_index):
        return "me llamo Roberto Sánchez"

    monkeypatch.setattr(score_dataset, "answer_text_for_model", fake_answer_text_for_model)
    monkeypatch.setattr(score_dataset, "load_manifest", lambda: _fake_manifest())

    out_path = tmp_path / "semantic_scores.csv"
    score_dataset.main(["--out-path", str(out_path)])
    first_bytes = out_path.read_bytes()

    score_dataset.main(["--out-path", str(out_path)])
    second_bytes = out_path.read_bytes()

    assert first_bytes == second_bytes


def _fake_manifest():
    return pd.DataFrame(
        {
            "anon_id": ["call_b", "call_a"],
            "label": ["human", "synthetic"],
            "split": ["train", "val"],
            "duration_s": [60.0, 90.0],
        }
    )


# ---------------------------------------------------------------------------
# ab_report.py: FR-014 A/B + runtime fusion.
# ---------------------------------------------------------------------------

PASSING_VAD_REPORT = """# VAD agreement report (FR-004)

Gate
----
FR-004 VAD agreement gate: PASS
"""


def _make_features_df(n_human: int, n_synthetic: int, split: str, rng: np.random.RandomState) -> pd.DataFrame:
    labels = ["human"] * n_human + ["synthetic"] * n_synthetic
    rows = []
    for i, label in enumerate(labels):
        target = 1.0 if label == "synthetic" else 0.0
        feats = {name: float(rng.normal(0.0, 1.0)) for name in FEATURE_NAMES}
        feats["resp_latency_mean"] = target * 1.5 + float(rng.normal(0.0, 1.0))
        rows.append(
            {
                "anon_id": f"{split}-{i}",
                "label": label,
                "split": split,
                "duration_s": 60.0,
                **feats,
                "source": "vad",
            }
        )
    return pd.DataFrame.from_records(
        rows, columns=["anon_id", "label", "split", "duration_s", *FEATURE_NAMES, "source"]
    )


@pytest.fixture()
def features_df():
    rng = np.random.RandomState(11)
    train_df = _make_features_df(60, 90, "train", rng)
    val_df = _make_features_df(25, 25, "val", rng)
    return pd.concat([train_df, val_df], ignore_index=True)


@pytest.fixture()
def wired_paths(tmp_path, features_df, monkeypatch):
    """Wires ab_report's module-level paths to a tmp_path sandbox and
    builds the fc-1-only model + calibration.json it reads as
    p_behavioral's source (T017/T018 artifacts, never retrained by
    ab_report.py itself)."""
    features_path = tmp_path / "features_vad.parquet"
    features_df.to_parquet(features_path, engine="pyarrow", index=False)

    report_path = tmp_path / "REPORT.md"
    report_path.write_text(PASSING_VAD_REPORT, encoding="utf-8")

    model_path = tmp_path / "model_lgbm.txt"
    meta_path = tmp_path / "train_meta.json"
    rc = train_module.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--meta-path", str(meta_path),
            "--vad-report-path", str(report_path),
        ]
    )
    assert rc == 0

    calibration_path = tmp_path / "calibration.json"
    reliability_path = tmp_path / "reliability_curve.png"
    rc = calibrate_module.main(
        [
            "--features-path", str(features_path),
            "--model-path", str(model_path),
            "--calibration-path", str(calibration_path),
            "--reliability-path", str(reliability_path),
        ]
    )
    assert rc == 0

    semantic_scores_path = tmp_path / "semantic_scores.csv"
    ab_report_path = tmp_path / "AB_REPORT.md"
    fusion_path = tmp_path / "semantic_fusion.json"

    monkeypatch.setattr(ab_report, "VAD_PARQUET", features_path)
    monkeypatch.setattr(ab_report, "MODEL_PATH", model_path)
    monkeypatch.setattr(ab_report, "CALIBRATION_PATH", calibration_path)
    monkeypatch.setattr(ab_report, "SEMANTIC_SCORES_PATH", semantic_scores_path)
    monkeypatch.setattr(ab_report, "AB_REPORT_PATH", ab_report_path)
    monkeypatch.setattr(ab_report, "FUSION_PATH", fusion_path)

    return {
        "features_df": features_df,
        "semantic_scores_path": semantic_scores_path,
        "ab_report_path": ab_report_path,
        "fusion_path": fusion_path,
    }


def _write_semantic_scores(path, features_df: pd.DataFrame, rng: np.random.RandomState, informative: bool) -> None:
    """One row per (anon_id, whisper model). `informative=True` makes
    invention_score track the true label (assertion-like -> higher score
    for synthetic calls); `informative=False` makes it pure label-blind
    noise, so the two models exercise both sides of the honest-outcome
    rule in the A/B."""
    rows = []
    for _, row in features_df.iterrows():
        target = 1.0 if row["label"] == "synthetic" else 0.0
        for model in ab_report.WHISPER_MODELS:
            if informative:
                score = float(np.clip(0.3 + 0.5 * target + rng.normal(0.0, 0.12), 0.0, 1.0))
            else:
                score = float(np.clip(rng.uniform(0.0, 1.0), 0.0, 1.0))
            rows.append(
                {
                    "anon_id": row["anon_id"],
                    "model": model,
                    "probe_detected": True,
                    "answer_type": "assertion_numeric" if informative else "other",
                    "invention_score": score,
                    "semantic_available": True,
                }
            )
    pd.DataFrame.from_records(rows).to_csv(path, index=False)


def test_ab_report_and_fusion_artifact_end_to_end(wired_paths):
    rng = np.random.RandomState(3)
    _write_semantic_scores(wired_paths["semantic_scores_path"], wired_paths["features_df"], rng, informative=True)

    rc = ab_report.main([])
    assert rc == 0

    report_text = wired_paths["ab_report_path"].read_text(encoding="utf-8")

    # (b) VERIFY: AB_REPORT.md contains both AUCs, both BAs, the delta,
    # and the fusion parameters -- checked structurally per whisper model.
    for model in ab_report.WHISPER_MODELS:
        assert model in report_text
    assert "val AUC" in report_text
    assert "val BA" in report_text
    assert "delta (plus - fc1)" in report_text
    assert "max_delta_p" in report_text
    assert "w (train-OOF-optimal)" in report_text

    fusion = json.loads(wired_paths["fusion_path"].read_text(encoding="utf-8"))
    ab_report.validate_semantic_fusion_schema(fusion)
    assert fusion["whisper_model"] in ab_report.WHISPER_MODELS
    assert 0.0 <= fusion["ba_val_behavioral"] <= 1.0
    assert 0.0 <= fusion["ba_val_fused"] <= 1.0


def test_honest_outcome_rule_forces_w_zero_when_fusion_does_not_help(wired_paths):
    rng = np.random.RandomState(5)
    _write_semantic_scores(wired_paths["semantic_scores_path"], wired_paths["features_df"], rng, informative=False)

    calibration = ab_report.load_behavioral_calibration()
    df_all = ab_report.load_features()
    result = ab_report.evaluate_fusion_for_whisper_model(df_all, "tiny", calibration)

    if result["ba_val_fused"] <= result["ba_val_behavioral"]:
        assert result["w"] == 0.0
        assert "did not exceed" in result["honest_note"]
    else:
        # Noise got lucky on this seed and still improved val BA -- the
        # rule only fires when it doesn't, so just confirm w wasn't
        # forced to 0 in that case.
        assert result["w"] == result["w_selected_on_train_oof"]


def test_fused_probability_never_exceeds_max_delta_p():
    rng = np.random.RandomState(9)
    p_behav = rng.uniform(0.05, 0.95, 200)
    invention = rng.uniform(0.0, 1.0, 200)
    fused = ab_report.fused_probability(p_behav, invention, w=10.0, max_delta_p=0.15)
    assert np.all(np.abs(fused - p_behav) <= 0.15 + 1e-9)


def test_validate_semantic_fusion_schema_rejects_missing_key():
    bad = {"version": ab_report.FUSION_ARTIFACT_VERSION, "w": 0.0}
    with pytest.raises(ValueError, match="missing required key"):
        ab_report.validate_semantic_fusion_schema(bad)


def test_validate_semantic_fusion_schema_rejects_bad_whisper_model():
    bad = {
        "version": ab_report.FUSION_ARTIFACT_VERSION,
        "w": 0.0,
        "max_delta_p": 0.15,
        "whisper_model": "large",
        "fitted_on": "train_oof",
        "ba_val_behavioral": 0.8,
        "ba_val_fused": 0.8,
    }
    with pytest.raises(ValueError, match="whisper_model"):
        ab_report.validate_semantic_fusion_schema(bad)
