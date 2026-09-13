"""Acoustic sub-signal feature extractor (compact, low-dimensional).

Eight caller-channel descriptors:
- spectral_flatness_mean
- spectral_flatness_std
- spectral_centroid_mean
- spectral_centroid_std
- f0_stability (autocorrelation-derived)
- ltas_tilt
- silence_floor_db
- clipping_ratio

Design: deliberately small to avoid memorising TTS engine fingerprints.
This is a reference extractor for the offline pipeline only; the Rust
port must mirror these names/order if and when used in production.
"""
from __future__ import annotations

import numpy as np
from scipy import signal
from scipy.fft import rfft, rfftfreq

FEATURE_NAMES = (
    "spectral_flatness_mean",
    "spectral_flatness_std",
    "spectral_centroid_mean",
    "spectral_centroid_std",
    "f0_stability",
    "ltas_tilt",
    "silence_floor_db",
    "clipping_ratio",
)


def _frame_signal(x: np.ndarray, frame_len: int, hop: int) -> np.ndarray:
    if x.size == 0:
        return np.zeros((0, frame_len), dtype=np.float32)
    n_frames = 1 + max(0, (len(x) - frame_len) // hop)
    frames = np.lib.stride_tricks.sliding_window_view(x, frame_len)[::hop]
    return frames.astype(np.float32)


def _rms(frames: np.ndarray) -> np.ndarray:
    if frames.size == 0:
        return np.array([], dtype=np.float32)
    return np.sqrt(np.mean(frames ** 2, axis=1))


def spectral_flatness(power_spec: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    # geometric mean / arithmetic mean per frame
    geo = np.exp(np.mean(np.log(power_spec + eps), axis=1))
    arith = np.mean(power_spec, axis=1) + eps
    return geo / arith


def spectral_centroid(mag: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    num = np.sum(mag * freqs[None, :], axis=1)
    den = np.sum(mag, axis=1) + 1e-12
    return num / den


def f0_stability_autocorr(x: np.ndarray, sr: int) -> float:
    # Compute normalized autocorrelation and return a simple stability metric:
    # ratio of the highest non-zero-lag peak to the zero-lag peak (energy).
    if x.size < 256:
        return 0.0
    x = x - np.mean(x)
    ac = np.correlate(x, x, mode="full")
    ac = ac[ac.size // 2 :]
    if ac.size < 2 or ac[0] == 0:
        return 0.0
    ac = ac / (ac[0] + 1e-12)
    # ignore very short lags (<50 Hz) and very long lags (>500 Hz)
    min_lag = max(1, int(sr / 500))
    max_lag = min(len(ac) - 1, int(sr / 50))
    if max_lag <= min_lag:
        return 0.0
    peak = float(np.max(ac[min_lag : max_lag + 1]))
    return float(peak)


def ltas_tilt_from_spectrum(power_spec: np.ndarray, freqs: np.ndarray) -> float:
    # Compute a simple tilt: slope of linear fit to log-power vs log-frequency.
    if power_spec.size == 0:
        return 0.0
    avg = np.mean(power_spec, axis=0)
    mask = freqs > 0
    if mask.sum() < 2:
        return 0.0
    x = np.log(freqs[mask])
    y = np.log(avg[mask] + 1e-12)
    A = np.vstack([x, np.ones_like(x)]).T
    m, _ = np.linalg.lstsq(A, y, rcond=None)[0]
    return float(m)


def extract_from_wave(wave: np.ndarray, sr: int) -> np.ndarray:
    """Extract the 8 acoustic features from a mono waveform (numpy float32, [-1,1])."""
    # Defensive fallbacks
    if wave is None or wave.size == 0:
        return np.zeros(len(FEATURE_NAMES), dtype=np.float32)

    # Frame params: 25 ms frames @ 8kHz -> 200 samples, hop 10 ms -> 80 samples
    frame_len = max(64, int(0.025 * sr))
    hop = max(32, int(0.010 * sr))

    frames = _frame_signal(wave, frame_len, hop)
    rms = _rms(frames)

    # FFT per-frame
    if frames.size == 0:
        power_spec = np.zeros((0, frame_len // 2 + 1), dtype=np.float32)
    else:
        win = signal.windows.hann(frame_len, sym=False).astype(np.float32)
        fft_frames = np.fft.rfft(frames * win[None, :], axis=1)
        power_spec = (np.abs(fft_frames) ** 2).astype(np.float32)

    freqs = rfftfreq(frame_len, d=1.0 / sr)

    sf = spectral_flatness(power_spec) if power_spec.size else np.array([], dtype=np.float32)
    sc = spectral_centroid(np.sqrt(power_spec + 1e-12), freqs) if power_spec.size else np.array([], dtype=np.float32)

    spectral_flatness_mean = float(np.mean(sf)) if sf.size else 0.0
    spectral_flatness_std = float(np.std(sf, ddof=0)) if sf.size else 0.0
    spectral_centroid_mean = float(np.mean(sc)) if sc.size else 0.0
    spectral_centroid_std = float(np.std(sc, ddof=0)) if sc.size else 0.0

    f0_stability = f0_stability_autocorr(wave, sr)
    ltas = ltas_tilt_from_spectrum(power_spec, freqs) if power_spec.size else 0.0

    # Silence floor: 10th percentile RMS in dBFS
    if rms.size:
        silence_floor_db = float(20.0 * np.log10(max(1e-12, np.percentile(rms, 10))))
    else:
        silence_floor_db = 0.0

    # Clipping ratio
    clipping_ratio = float((np.abs(wave) >= 0.999).sum() / float(max(1, wave.size)))

    out = np.array(
        [
            spectral_flatness_mean,
            spectral_flatness_std,
            spectral_centroid_mean,
            spectral_centroid_std,
            f0_stability,
            ltas,
            silence_floor_db,
            clipping_ratio,
        ],
        dtype=np.float32,
    )
    return out


def extract_mono_from_stereo(stereo_wave: np.ndarray, sr: int) -> np.ndarray:
    """Convenience: accept stereo (shape (2, N) or (N,2)) and return caller-channel features.

    The convention in this repo: ch0 = caller, ch1 = agent. We extract caller only.
    """
    if stereo_wave is None or stereo_wave.size == 0:
        return extract_from_wave(np.array([], dtype=np.float32), sr)
    if stereo_wave.ndim == 2 and stereo_wave.shape[0] == 2:
        caller = stereo_wave[0]
    elif stereo_wave.ndim == 2 and stereo_wave.shape[1] == 2:
        caller = stereo_wave[:, 0]
    else:
        # already mono
        caller = stereo_wave.flatten()
    return extract_from_wave(caller.astype(np.float32), sr)

