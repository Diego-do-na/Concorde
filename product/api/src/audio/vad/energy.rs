//! VAD stage A: per-frame RMS energy and an adaptive activity threshold.
//!
//! The threshold is derived **per channel**, from that channel's own
//! energy distribution, rather than a single fixed dB value shared across
//! both channels. The agent channel (often synthesized speech played back
//! at a hotter, more consistent level) and the caller channel (variable
//! mic gain, room noise, phone codec) have different noise floors; a
//! shared threshold would either miss quiet caller speech or false-trigger
//! on agent-channel bleed. It is **percentile-based** rather than a mean
//! or a fixed silence-detector floor because a percentile is robust to the
//! actual speech frames themselves — a low percentile (default 10th) of
//! the whole channel's per-frame energy approximates "the quiet parts"
//! even when we don't yet know which frames are speech (ADR-003).
//!
//! All functions here are pure and allocate only their output `Vec` — no
//! hidden state, no I/O — so they stay cheap enough to run on every
//! request in the inference path.

use super::params::VadParams;

/// Per-frame RMS energy of `samples`, in dBFS, using `params.frame_ms` /
/// `params.hop_ms` (both in milliseconds) at sample rate `sr` (Hz).
///
/// Frame `i` covers samples `[i * hop_len, i * hop_len + frame_len)`,
/// where `frame_len`/`hop_len` are `frame_ms`/`hop_ms` converted to
/// samples at `sr`. Only full frames are emitted — a trailing partial
/// frame shorter than `frame_len` is dropped. A frame whose RMS is
/// numerically zero (pure digital silence) is floored to `-100.0` dB
/// rather than producing `-inf`.
///
/// Returns an empty `Vec` if `samples` is empty or shorter than one frame.
pub fn frame_rms_db(samples: &[f32], sr: u32, params: &VadParams) -> Vec<f32> {
    const SILENCE_FLOOR_DB: f32 = -100.0;

    if samples.is_empty() || sr == 0 {
        return Vec::new();
    }

    let frame_len = ((params.frame_ms as u64 * sr as u64) / 1000) as usize;
    let hop_len = ((params.hop_ms as u64 * sr as u64) / 1000) as usize;

    if frame_len == 0 || hop_len == 0 || samples.len() < frame_len {
        return Vec::new();
    }

    let num_frames = (samples.len() - frame_len) / hop_len + 1;
    let mut out = Vec::with_capacity(num_frames);

    for i in 0..num_frames {
        let start = i * hop_len;
        let frame = &samples[start..start + frame_len];
        let sum_sq: f64 = frame.iter().map(|&s| (s as f64) * (s as f64)).sum();
        let rms = (sum_sq / frame_len as f64).sqrt();
        let db = if rms > 0.0 {
            (20.0 * rms.log10()) as f32
        } else {
            SILENCE_FLOOR_DB
        };
        out.push(db.max(SILENCE_FLOOR_DB));
    }

    out
}

/// Adaptive activity threshold (in dB) for one channel: the
/// `params.noise_floor_percentile` percentile of `frames_db`, plus
/// `params.threshold_db_above_floor`.
///
/// Uses nearest-rank percentile over a sorted copy of `frames_db` (no
/// interpolation), which is stable and cheap for the frame counts a
/// single call produces (tens of thousands at most).
///
/// Returns `-100.0 + threshold_db_above_floor` if `frames_db` is empty,
/// so callers that skip the empty check still get a threshold no frame
/// (there are none) can exceed.
pub fn adaptive_threshold(frames_db: &[f32], params: &VadParams) -> f32 {
    const SILENCE_FLOOR_DB: f32 = -100.0;

    if frames_db.is_empty() {
        return SILENCE_FLOOR_DB + params.threshold_db_above_floor;
    }

    let mut sorted = frames_db.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).expect("frame dB values are never NaN"));

    let p = params.noise_floor_percentile.clamp(0.0, 1.0);
    let idx = ((p * sorted.len() as f32) as usize).min(sorted.len() - 1);
    let floor = sorted[idx];

    floor + params.threshold_db_above_floor
}

/// Raw (unsmoothed) per-frame activity: `true` where `frames_db[i] > thr`.
///
/// This is stage A's output only — no hysteresis or minimum-duration
/// logic is applied here; that is stage B (T011).
pub fn raw_activity(frames_db: &[f32], thr: f32) -> Vec<bool> {
    frames_db.iter().map(|&db| db > thr).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Instant;

    const SR: u32 = 8000;

    /// Deterministic pseudo-noise in `[-amplitude, amplitude]`, no external
    /// RNG dependency needed for a unit test.
    fn noise(len: usize, amplitude: f32, seed: u64) -> Vec<f32> {
        let mut state = seed.wrapping_add(0x9E3779B97F4A7C15);
        (0..len)
            .map(|_| {
                // xorshift64*
                state ^= state << 13;
                state ^= state >> 7;
                state ^= state << 17;
                let unit = (state >> 11) as f64 / (1u64 << 53) as f64; // [0,1)
                (unit as f32 * 2.0 - 1.0) * amplitude
            })
            .collect()
    }

    fn amplitude_for_dbfs(dbfs: f32) -> f32 {
        10f32.powf(dbfs / 20.0)
    }

    #[test]
    fn empty_input_returns_empty() {
        let params = VadParams::default();
        assert!(frame_rms_db(&[], SR, &params).is_empty());
        assert!(raw_activity(&frame_rms_db(&[], SR, &params), 0.0).is_empty());
    }

    #[test]
    fn silence_plus_tone_burst_activates_only_burst_frames() {
        let params = VadParams::default();

        // -50 dBFS noise floor, 3 s total, with a 1 s -20 dBFS tone burst
        // in the middle second.
        let noise_amp = amplitude_for_dbfs(-50.0);
        let tone_amp = amplitude_for_dbfs(-20.0);

        let mut samples = noise(SR as usize, noise_amp, 1);
        let freq = 440.0_f32;
        let tone: Vec<f32> = (0..SR as usize)
            .map(|n| {
                let t = n as f32 / SR as f32;
                tone_amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect();
        samples.extend(tone);
        samples.extend(noise(SR as usize, noise_amp, 2));

        let frames_db = frame_rms_db(&samples, SR, &params);
        let thr = adaptive_threshold(&frames_db, &params);
        let activity = raw_activity(&frames_db, thr);

        let hop_len = (params.hop_ms as usize * SR as usize) / 1000;
        let burst_start_frame = (SR as usize) / hop_len; // first frame index inside the burst
        let burst_end_frame = (2 * SR as usize) / hop_len; // first frame index after the burst

        for (i, &active) in activity.iter().enumerate() {
            let clearly_inside = i > burst_start_frame + 1 && i + 1 < burst_end_frame;
            let clearly_outside =
                i + 1 < burst_start_frame.saturating_sub(1) || i > burst_end_frame + 1;
            if clearly_inside {
                assert!(active, "frame {i} inside burst should be active");
            } else if clearly_outside {
                assert!(!active, "frame {i} outside burst should be inactive");
            }
            // frames within one frame of either edge are allowed to go
            // either way (±1 frame tolerance).
        }
    }

    #[test]
    fn different_channel_noise_floors_get_different_thresholds() {
        let params = VadParams::default();

        let quiet = noise(SR as usize, amplitude_for_dbfs(-55.0), 10);
        let loud = noise(SR as usize, amplitude_for_dbfs(-25.0), 20);

        let thr_quiet = adaptive_threshold(&frame_rms_db(&quiet, SR, &params), &params);
        let thr_loud = adaptive_threshold(&frame_rms_db(&loud, SR, &params), &params);

        assert!(
            thr_loud > thr_quiet + 10.0,
            "loud channel threshold ({thr_loud}) should sit well above quiet channel's ({thr_quiet})"
        );
    }

    #[test]
    fn processes_300s_of_audio_in_under_100ms() {
        let params = VadParams::default();
        let samples = noise((300 * SR as u64) as usize, amplitude_for_dbfs(-30.0), 42);

        let start = Instant::now();
        let frames_db = frame_rms_db(&samples, SR, &params);
        let thr = adaptive_threshold(&frames_db, &params);
        let _activity = raw_activity(&frames_db, thr);
        let elapsed = start.elapsed();

        assert!(
            elapsed.as_millis() < 100,
            "300s of audio took {elapsed:?}, budget is 100ms (run with --release for the real number)"
        );
    }
}
