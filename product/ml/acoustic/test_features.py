"""Tests for acoustic feature extractor (product/ml/acoustic/features.py)."""

import numpy as np
import pytest

from product.ml.acoustic.features import (
    FEATURE_NAMES,
    extract_from_wave,
    extract_mono_from_stereo,
    f0_stability_autocorr,
    ltas_tilt_from_spectrum,
    spectral_centroid,
    spectral_flatness,
)


def test_feature_names_count():
    assert len(FEATURE_NAMES) == 8
    assert "spectral_flatness_mean" in FEATURE_NAMES
    assert "clipping_ratio" in FEATURE_NAMES


def test_extract_empty_and_null():
    empty = np.array([], dtype=np.float32)
    feat = extract_from_wave(empty, 8000)
    assert len(feat) == 8
    assert not np.any(np.isnan(feat))
    assert not np.any(np.isinf(feat))

    feat_mono = extract_mono_from_stereo(None, 8000)
    assert len(feat_mono) == 8
    assert not np.any(np.isnan(feat_mono))


def test_extract_sine_wave():
    sr = 8000
    t = np.linspace(0, 1.0, sr, endpoint=False, dtype=np.float32)
    # 440 Hz sine wave
    wave = 0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32)

    feat = extract_from_wave(wave, sr)
    assert len(feat) == 8
    assert not np.any(np.isnan(feat))
    assert not np.any(np.isinf(feat))
    # Sine wave is highly tonal, so spectral flatness should be low
    assert feat[0] >= 0.0
    # Clipping ratio should be 0.0
    assert feat[7] == 0.0


def test_extract_clipped_wave():
    sr = 8000
    wave = np.ones(sr, dtype=np.float32) * 1.0  # Fully clipped signal
    feat = extract_from_wave(wave, sr)
    assert len(feat) == 8
    assert feat[7] == 1.0  # clipping ratio = 1.0


def test_extract_stereo_shape():
    sr = 8000
    t = np.linspace(0, 0.5, int(0.5 * sr), endpoint=False, dtype=np.float32)
    ch0 = 0.3 * np.sin(2 * np.pi * 300 * t).astype(np.float32)
    ch1 = 0.3 * np.sin(2 * np.pi * 600 * t).astype(np.float32)
    stereo = np.vstack([ch0, ch1])

    feat = extract_mono_from_stereo(stereo, sr)
    assert len(feat) == 8
    assert not np.any(np.isnan(feat))
