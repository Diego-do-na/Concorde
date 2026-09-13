"""Tests for T042: answer_type rules, invention_score, cache resumability,
probe ground truth coverage, and the "no transcript text outside the
gitignored cache" invariant.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

import semantic.rules as rules
import semantic.transcribe as transcribe
from common.dataset import dataset_dir

# --------------------------------------------------------------------------
# (b) rules classify 12 hand-written answers correctly
# --------------------------------------------------------------------------

FIXTURES = [
    ("no tengo esa cuenta", "denial"),
    ("no cuento con eso", "denial"),
    ("no sé, no me acuerdo", "denial"),
    ("no me aparece ninguna", "denial"),
    ("creo que sí tengo algo", "hedge"),
    ("no estoy seguro, tal vez", "hedge"),
    ("sí, tengo la nómina plus", "assertion_product"),
    ("mi crédito verde está activo", "assertion_product"),
    ("el número es 4 5 6 7 8 9", "assertion_numeric"),
    ("me llamo Roberto Sánchez", "assertion_name"),
    ("¿cuál es? no entiendo", "question_back"),
    ("buenos días como esta", "other"),
]


@pytest.mark.parametrize("text,expected", FIXTURES)
def test_classify_answer_type_fixtures(text, expected):
    assert rules.classify_answer_type(text) == expected


def test_all_seven_answer_types_covered_by_fixtures():
    assert {label for _, label in FIXTURES} == set(rules.ANSWER_TYPES)


def test_invention_score_table_shape_and_ordering():
    # assertion_* high, denial/question_back low, hedge in the middle --
    # the documented shape from the module docstring / DIGEST.md rule table.
    assert rules.invention_score("assertion_numeric") > rules.invention_score("hedge")
    assert rules.invention_score("assertion_product") > rules.invention_score("hedge")
    assert rules.invention_score("assertion_name") > rules.invention_score("hedge")
    assert rules.invention_score("hedge") > rules.invention_score("denial")
    assert rules.invention_score("hedge") > rules.invention_score("question_back")
    for t in rules.ANSWER_TYPES:
        assert 0.0 <= rules.invention_score(t) <= 1.0
    with pytest.raises(ValueError):
        rules.invention_score("not_a_real_type")


def test_analyze_answer_combines_fields():
    result = rules.analyze_answer("no tengo esa cuenta", response_latency=0.8)
    assert result["answer_type"] == "denial"
    assert result["word_count"] == 4
    assert result["response_latency"] == 0.8
    assert result["invention_score"] == rules.invention_score("denial")


# --------------------------------------------------------------------------
# (b) transcripts cache is resumable (second run does zero whisper work)
# --------------------------------------------------------------------------


def test_transcribe_and_cache_is_resumable(tmp_path, monkeypatch):
    monkeypatch.setattr(transcribe, "TRANSCRIPTS_DIR", tmp_path / "transcripts")

    fake_turns = [(1, 0.0, 1.0), (0, 2.0, 3.0)]  # one agent turn, one caller turn
    monkeypatch.setattr(transcribe, "load_turns", lambda anon_id: fake_turns)
    monkeypatch.setattr(transcribe, "wav_path", lambda anon_id: Path("unused.wav"))

    fake_stereo = np.zeros((8000 * 4, 2), dtype=np.int16)
    monkeypatch.setattr(
        transcribe, "read_stereo_wav", lambda path: (fake_stereo, 8000)
    )

    call_count = {"n": 0}

    def fake_run_whisper_cli(whisper_bin, model_path, wav_file, language="es", threads=4):
        call_count["n"] += 1
        return {"transcription": [{"offsets": {"from": 0, "to": 500}, "text": "hola"}]}

    monkeypatch.setattr(transcribe, "run_whisper_cli", fake_run_whisper_cli)

    kwargs = dict(
        whisper_bin=Path("whisper-cli"),
        model_path=Path("model.bin"),
        threads=1,
        language="es",
    )

    worked_first = transcribe.transcribe_and_cache("call_test", "base", **kwargs)
    assert worked_first is True
    calls_after_first = call_count["n"]
    assert calls_after_first == 2  # one whisper-cli invocation per channel present

    out_path = transcribe.cache_path("call_test", "base")
    assert out_path.is_file()

    worked_second = transcribe.transcribe_and_cache("call_test", "base", **kwargs)
    assert worked_second is False
    assert call_count["n"] == calls_after_first  # zero additional whisper invocations


def test_cache_paths_resolve_under_gitignored_cache_dir():
    # Structural guard for "no transcript text is written anywhere except
    # the gitignored cache" (product/.gitignore: ml/semantic/cache/).
    assert transcribe.CACHE_DIR == transcribe.SEMANTIC_DIR / "cache"
    assert transcribe.TRANSCRIPTS_DIR.is_relative_to(transcribe.CACHE_DIR)
    assert transcribe.cache_path("anything", "base").is_relative_to(transcribe.CACHE_DIR)
    assert transcribe.GROUND_TRUTH_PATH.is_relative_to(transcribe.CACHE_DIR)


# --------------------------------------------------------------------------
# (b) probe ground truth found in >= 55% of calls for the base model
# --------------------------------------------------------------------------

_GT_PATH = transcribe.GROUND_TRUTH_PATH

pytestmark_probe = pytest.mark.skipif(
    not _GT_PATH.is_file(),
    reason="cache/probe_ground_truth.json not built yet -- run transcribe.py first",
)


@pytestmark_probe
def test_probe_ground_truth_coverage_at_least_55_percent():
    with _GT_PATH.open("r", encoding="utf-8") as f:
        gt = json.load(f)
    assert gt["model"] == "base"
    assert gt["considered"] > 0
    assert gt["coverage"] >= 0.55


# --------------------------------------------------------------------------
# (b) no transcript text is written anywhere except the gitignored cache
# --------------------------------------------------------------------------


def test_build_digest_never_leaks_raw_transcript_text(tmp_path, monkeypatch):
    marker = "UNIQUE_MARKER_SENTENCE_XYZ_TOTALLY_MADE_UP"

    cache_dir = tmp_path / "cache"
    (cache_dir / "transcripts" / "base").mkdir(parents=True)

    ground_truth = {
        "model": "base",
        "considered": 2,
        "found": 2,
        "coverage": 1.0,
        "calls": {
            "call_a": {
                "probe_found": True,
                "probe_turn_index": 0,
                "answer_turn_index": 1,
                "response_latency": 0.9,
            },
            "call_b": {
                "probe_found": True,
                "probe_turn_index": 0,
                "answer_turn_index": 1,
                "response_latency": 1.4,
            },
        },
    }
    (cache_dir / "probe_ground_truth.json").write_text(
        json.dumps(ground_truth), encoding="utf-8"
    )

    def turns_json(text):
        return {
            "anon_id": "x",
            "model": "base",
            "language": "es",
            "turns": [
                {"index": 0, "channel": 1, "start": 0.0, "end": 1.0, "text": "probe"},
                {"index": 1, "channel": 0, "start": 2.0, "end": 3.0, "text": text},
            ],
        }

    (cache_dir / "transcripts" / "base" / "call_a.json").write_text(
        json.dumps(turns_json(f"{marker} no tengo esa cuenta")), encoding="utf-8"
    )
    (cache_dir / "transcripts" / "base" / "call_b.json").write_text(
        json.dumps(turns_json("mi número es 123456")), encoding="utf-8"
    )

    class _FakeManifest:
        def __getitem__(self, key):
            return {"anon_id": ["call_a", "call_b"], "label": ["human", "synthetic"]}[key]

    import common.dataset as dataset_module

    monkeypatch.setattr(dataset_module, "load_manifest", lambda: _FakeManifest())

    digest = rules.build_digest(semantic_dir=tmp_path, model="base")

    assert marker not in digest
    assert "no tengo esa cuenta" not in digest
    assert "123456" not in digest

    # still a real digest: structural sections + correct aggregate counts.
    assert "## Rule table" in digest
    assert "denial" in digest
    assert "assertion_numeric" in digest
