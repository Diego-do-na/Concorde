//! Probe-turn detector: 32-band log-mel + open-begin/open-end (subsequence)
//! DTW template matching, ported line-by-line from
//! `product/ml/semantic/probe_detector.py` (T043) — THAT module is the
//! definition (method frozen 2026-09-12); this is its Rust production
//! port, run over the agent (channel 1) turns `/detect`'s own VAD already
//! found, with no ASR involved. Loads the frozen template bank exported to
//! `product/artifacts/probe_templates.json`.
//!
//! No external FFT/DSP crate: `N_FFT` (256) is a fixed power of two, so
//! [`fft_radix2`] is a small self-contained iterative Cooley-Tukey rather
//! than a new dependency for one 256-point transform.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

use crate::audio::vad::Turn;

// ---- log-mel parameters (frozen, matches probe_detector.py exactly) ----
pub const SAMPLE_RATE: u32 = 8000;
const N_FFT: usize = 256;
pub const HOP: usize = 80;
const N_MELS: usize = 32;
const FMIN: f64 = 50.0;
const FMAX: f64 = 4000.0;
const EPS: f64 = 1e-10;

// --------------------------------------------------------------------------
// Artifact shape (product/artifacts/probe_templates.json, T043)
// --------------------------------------------------------------------------

#[derive(Debug, Deserialize)]
struct MelParams {
    sample_rate: u32,
    n_fft: usize,
    hop: usize,
    n_mels: usize,
    fmin: f64,
    fmax: f64,
}

#[derive(Debug, Deserialize)]
struct AmbiguityBounds {
    present_le: f64,
    absent_ge: f64,
}

#[derive(Debug, Deserialize)]
struct ProbeTemplatesFile {
    #[allow(dead_code)]
    version: String,
    mel_params: MelParams,
    templates: Vec<Vec<Vec<f64>>>, // [template][frame][band]
    #[allow(dead_code)]
    threshold: f64,
    min_turn_frames: usize,
    ambiguity_bounds: AmbiguityBounds,
}

/// A single L2-normalised log-mel template, `(n_frames, N_MELS)` flattened
/// row-major for cache-friendly dot products in the DTW cost matrix.
struct Template {
    frames: usize,
    bands: usize,
    data: Vec<f64>,
}

impl Template {
    fn row(&self, i: usize) -> &[f64] {
        &self.data[i * self.bands..(i + 1) * self.bands]
    }
}

pub struct ProbeDetector {
    _mel_fb: Vec<Vec<f64>>, // (N_MELS, n_bins)
    templates: Vec<Template>,
    min_turn_frames: usize,
    present_le: f64,
    absent_ge: f64,
}

/// The probe turn found by [`ProbeDetector::detect`]: its position in the
/// caller-supplied global turn list, its timing, and the winning
/// (lowest-is-best) template-bank score.
#[derive(Debug, Clone, Copy)]
pub struct ProbeMatch {
    pub turn_index: usize,
    pub start: f32,
    pub end: f32,
    pub score: f64,
}

impl ProbeDetector {
    pub fn load(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("read probe templates {}", path.display()))?;
        let file: ProbeTemplatesFile =
            serde_json::from_str(&raw).with_context(|| format!("parse probe templates {}", path.display()))?;

        let mp = &file.mel_params;
        if mp.sample_rate != SAMPLE_RATE
            || mp.n_fft != N_FFT
            || mp.hop != HOP
            || mp.n_mels != N_MELS
            || (mp.fmin - FMIN).abs() > 1e-9
            || (mp.fmax - FMAX).abs() > 1e-9
        {
            anyhow::bail!(
                "probe_templates.json mel_params {:?} don't match the frozen Rust constants \
                 (sample_rate={SAMPLE_RATE}, n_fft={N_FFT}, hop={HOP}, n_mels={N_MELS}, \
                 fmin={FMIN}, fmax={FMAX}) -- refusing to load a mismatched bank",
                mp
            );
        }

        let mel_fb = mel_filterbank();

        let templates: Vec<Template> = file
            .templates
            .iter()
            .map(|t| {
                let frames = t.len();
                let bands = t.first().map(|r| r.len()).unwrap_or(0);
                let mut data = Vec::with_capacity(frames * bands);
                for row in t {
                    data.extend_from_slice(row);
                }
                Template { frames, bands, data }
            })
            .collect();

        Ok(Self {
            _mel_fb: mel_fb,
            templates,
            min_turn_frames: file.min_turn_frames,
            present_le: file.ambiguity_bounds.present_le,
            absent_ge: file.ambiguity_bounds.absent_ge,
        })
    }

    /// `classify_score()` from probe_detector.py: `Some(true)` = confidently
    /// present, `Some(false)` = confidently absent, `None` = ambiguous
    /// (degrade to "not detected", never guess -- AGENTS.md rule 4/ADR-008).
    fn classify(&self, score: f64) -> Option<bool> {
        if score <= self.present_le {
            Some(true)
        } else if score >= self.absent_ge {
            Some(false)
        } else {
            None
        }
    }

    fn detector_score(&self, query: &Template) -> f64 {
        if query.frames == 0 {
            return f64::INFINITY;
        }
        self.templates
            .iter()
            .map(|t| subsequence_dtw_score(t, query))
            .fold(f64::INFINITY, f64::min)
    }

    /// Scores every agent (channel 1) turn in `turns` against the template
    /// bank (best -- lowest -- match wins), classifies the winning score,
    /// and on a confident match returns the probe turn's position/timing.
    /// `channel1_samples` is the whole call's decoded agent-channel audio
    /// at [`SAMPLE_RATE`] Hz; turn windows index into it by `start`/`end`
    /// (seconds). Mirrors `score_dataset.py::detect_probe_and_answer_index`
    /// (its probe-half; the answer-turn lookup is a separate, tiny
    /// caller-side step -- see [`super::find_answer_turn`]).
    pub fn detect(&self, channel1_samples: &[f32], turns: &[Turn]) -> Option<ProbeMatch> {
        let mut best_score = f64::INFINITY;
        let mut best: Option<(usize, f32, f32)> = None;

        for (gi, turn) in turns.iter().enumerate() {
            if turn.channel != 1 {
                continue;
            }
            let s = (turn.start as f64 * SAMPLE_RATE as f64).round().max(0.0) as usize;
            let e = ((turn.end as f64 * SAMPLE_RATE as f64).round() as usize).min(channel1_samples.len());
            if e <= s {
                continue;
            }
            let samples = &channel1_samples[s..e];
            if samples.len() < self.min_turn_frames * HOP {
                continue;
            }
            let query = extract_features(samples);
            let score = self.detector_score(&query);
            if score < best_score {
                best_score = score;
                best = Some((gi, turn.start, turn.end));
            }
        }

        let (gi, start, end) = best?;
        match self.classify(best_score) {
            Some(true) => Some(ProbeMatch { turn_index: gi, start, end, score: best_score }),
            _ => None, // confidently absent OR ambiguous both degrade to "not detected"
        }
    }
}

/// First caller (channel 0) turn after the probe turn's global index --
/// `score_dataset.py::detect_probe_and_answer_index`'s answer-turn lookup /
/// `transcribe.py::find_answer_turn`.
pub fn find_answer_turn(turns: &[Turn], probe_turn_index: usize) -> Option<(usize, f32, f32)> {
    turns
        .iter()
        .enumerate()
        .skip(probe_turn_index + 1)
        .find(|(_, t)| t.channel == 0)
        .map(|(gi, t)| (gi, t.start, t.end))
}

// --------------------------------------------------------------------------
// Log-mel front end
// --------------------------------------------------------------------------

fn hz_to_mel(hz: f64) -> f64 {
    2595.0 * (1.0 + hz / 700.0).log10()
}

fn mel_to_hz(mel: f64) -> f64 {
    700.0 * (10f64.powf(mel / 2595.0) - 1.0)
}

/// Triangular mel filterbank, `(N_MELS, n_fft/2+1)` -- `_mel_filterbank()`.
fn mel_filterbank() -> Vec<Vec<f64>> {
    let n_bins = N_FFT / 2 + 1;
    let n_pts = N_MELS + 2;
    let mel_lo = hz_to_mel(FMIN);
    let mel_hi = hz_to_mel(FMAX);
    let mel_pts: Vec<f64> =
        (0..n_pts).map(|i| mel_lo + (mel_hi - mel_lo) * i as f64 / (n_pts - 1) as f64).collect();
    let hz_pts: Vec<f64> = mel_pts.iter().map(|&m| mel_to_hz(m)).collect();
    let bin_pts: Vec<usize> = hz_pts
        .iter()
        .map(|&hz| {
            let b = (((N_FFT + 1) as f64) * hz / SAMPLE_RATE as f64).floor();
            (b.max(0.0) as usize).min(n_bins - 1)
        })
        .collect();

    let mut fb = vec![vec![0.0f64; n_bins]; N_MELS];
    for m in 1..=N_MELS {
        let left = bin_pts[m - 1];
        let mut center = bin_pts[m];
        let mut right = bin_pts[m + 1];
        if center == left {
            center += 1;
        }
        if right == center {
            right += 1;
        }
        for k in left..center.min(n_bins) {
            fb[m - 1][k] = (k - left) as f64 / (center - left) as f64;
        }
        for k in center..right.min(n_bins) {
            fb[m - 1][k] = (right - k) as f64 / (right - center) as f64;
        }
    }
    fb
}

/// Hamming window, `np.hamming(n_fft)`: `0.54 - 0.46*cos(2*pi*i/(N-1))`.
fn hamming_window(n: usize) -> Vec<f64> {
    if n == 1 {
        return vec![1.0];
    }
    (0..n).map(|i| 0.54 - 0.46 * (2.0 * std::f64::consts::PI * i as f64 / (n - 1) as f64).cos()).collect()
}

/// In-place iterative radix-2 Cooley-Tukey FFT (forward, `exp(-2*pi*i*k*n/N)`
/// convention, matching `numpy.fft.rfft`). `re.len()` must be a power of two.
fn fft_radix2(re: &mut [f64], im: &mut [f64]) {
    let n = re.len();
    debug_assert!(n.is_power_of_two());

    let mut j = 0usize;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }

    let mut len = 2usize;
    while len <= n {
        let ang = -2.0 * std::f64::consts::PI / len as f64;
        let (wr, wi) = (ang.cos(), ang.sin());
        let mut i = 0;
        while i < n {
            let (mut cur_wr, mut cur_wi) = (1.0, 0.0);
            for k in 0..len / 2 {
                let ur = re[i + k];
                let ui = im[i + k];
                let vr = re[i + k + len / 2] * cur_wr - im[i + k + len / 2] * cur_wi;
                let vi = re[i + k + len / 2] * cur_wi + im[i + k + len / 2] * cur_wr;
                re[i + k] = ur + vr;
                im[i + k] = ui + vi;
                re[i + k + len / 2] = ur - vr;
                im[i + k + len / 2] = ui - vi;
                let new_wr = cur_wr * wr - cur_wi * wi;
                let new_wi = cur_wr * wi + cur_wi * wr;
                cur_wr = new_wr;
                cur_wi = new_wi;
            }
            i += len;
        }
        len <<= 1;
    }
}

/// `_frame_signal` + `rfft` + mel projection + `log`: int16-range f32
/// samples (already at [`SAMPLE_RATE`] Hz) -> `(n_frames, N_MELS)` raw
/// (not yet normalised) log-mel energies.
fn log_mel_spectrogram(samples: &[f32], mel_fb: &[Vec<f64>]) -> Vec<Vec<f64>> {
    let window = hamming_window(N_FFT);
    let mut samples64: Vec<f64> = samples.iter().map(|&s| s as f64).collect();
    if samples64.len() < N_FFT {
        samples64.resize(N_FFT, 0.0);
    }
    let n = samples64.len();
    let n_frames = (1 + (n - N_FFT) / HOP).max(1);
    let n_bins = N_FFT / 2 + 1;

    let mut out = Vec::with_capacity(n_frames);
    for i in 0..n_frames {
        let start = i * HOP;
        let mut re = vec![0.0f64; N_FFT];
        let mut im = vec![0.0f64; N_FFT];
        for k in 0..N_FFT {
            let s = samples64.get(start + k).copied().unwrap_or(0.0);
            re[k] = s * window[k];
        }
        fft_radix2(&mut re, &mut im);

        let mut power = vec![0.0f64; n_bins];
        for k in 0..n_bins {
            power[k] = (re[k] * re[k] + im[k] * im[k]) / N_FFT as f64;
        }

        let mut mel_row = vec![0.0f64; N_MELS];
        for (band, fb_row) in mel_fb.iter().enumerate() {
            let mut acc = 0.0;
            for k in 0..n_bins {
                acc += power[k] * fb_row[k];
            }
            mel_row[band] = (acc + EPS).ln();
        }
        out.push(mel_row);
    }
    out
}

/// `normalize_frames`: per-band z-normalisation (population std, ddof=0)
/// over the clip's own frames, then per-frame L2 normalisation.
fn normalize_frames(log_mel: Vec<Vec<f64>>) -> Template {
    let n_frames = log_mel.len();
    if n_frames == 0 {
        return Template { frames: 0, bands: N_MELS, data: Vec::new() };
    }
    let bands = N_MELS;

    let mut mean = vec![0.0f64; bands];
    for row in &log_mel {
        for b in 0..bands {
            mean[b] += row[b];
        }
    }
    for m in &mut mean {
        *m /= n_frames as f64;
    }

    let mut std = vec![0.0f64; bands];
    for row in &log_mel {
        for b in 0..bands {
            let d = row[b] - mean[b];
            std[b] += d * d;
        }
    }
    for s in &mut std {
        *s = (*s / n_frames as f64).sqrt();
        if *s < 1e-8 {
            *s = 1.0;
        }
    }

    let mut data = Vec::with_capacity(n_frames * bands);
    for row in &log_mel {
        let mut z: Vec<f64> = (0..bands).map(|b| (row[b] - mean[b]) / std[b]).collect();
        let norm = z.iter().map(|v| v * v).sum::<f64>().sqrt();
        let norm = if norm < 1e-8 { 1.0 } else { norm };
        for v in &mut z {
            *v /= norm;
        }
        data.extend_from_slice(&z);
    }

    Template { frames: n_frames, bands, data }
}

/// `extract_features`: full front end for a query clip (uses the module's
/// own frozen filterbank, not an artifact-supplied one -- queries and
/// templates must be extracted identically).
fn extract_features(samples: &[f32]) -> Template {
    let mel_fb = mel_filterbank();
    normalize_frames(log_mel_spectrogram(samples, &mel_fb))
}

// --------------------------------------------------------------------------
// Open-begin/open-end (subsequence) DTW, cosine cost
// --------------------------------------------------------------------------

/// `subsequence_dtw_score`: best-matching-subsequence DTW distance of
/// `template` against `query`, both L2-normalised row-wise so cosine
/// similarity is a plain dot product. Open begin AND open end on the
/// query axis. Cost normalised by template length. Never negative.
fn subsequence_dtw_score(template: &Template, query: &Template) -> f64 {
    let (n, m) = (template.frames, query.frames);
    if n == 0 || m == 0 {
        return f64::INFINITY;
    }

    let cost = |i: usize, j: usize| -> f64 {
        let t = template.row(i);
        let q = query.row(j);
        let dot: f64 = t.iter().zip(q.iter()).map(|(a, b)| a * b).sum();
        1.0 - dot
    };

    // D is (n, m); row 0 = cost(0, j) for every j (open begin).
    let mut prev: Vec<f64> = (0..m).map(|j| cost(0, j)).collect();
    if n == 1 {
        return prev.iter().cloned().fold(f64::INFINITY, f64::min) / n as f64;
    }

    let mut cur = vec![0.0f64; m];
    for i in 1..n {
        cur[0] = prev[0] + cost(i, 0);
        for j in 1..m {
            let c = cost(i, j);
            cur[j] = c + prev[j].min(cur[j - 1]).min(prev[j - 1]);
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    prev.iter().cloned().fold(f64::INFINITY, f64::min) / n as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    fn artifact_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../artifacts/probe_templates.json")
    }

    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n)
            .map(|i| {
                let t = i as f32 / sr as f32;
                amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect()
    }

    #[test]
    fn mel_filterbank_shape_and_normalisation() {
        let fb = mel_filterbank();
        assert_eq!(fb.len(), N_MELS);
        assert_eq!(fb[0].len(), N_FFT / 2 + 1);
        // Every row should have at least one non-zero weight.
        for row in &fb {
            assert!(row.iter().any(|&w| w > 0.0));
        }
    }

    #[test]
    fn extract_features_l2_normalises_every_frame() {
        let samples = sine(SAMPLE_RATE, 300.0, 500, 0.5);
        let t = extract_features(&samples);
        assert!(t.frames > 0);
        for i in 0..t.frames {
            let row = t.row(i);
            let norm: f64 = row.iter().map(|v| v * v).sum::<f64>().sqrt();
            assert!((norm - 1.0).abs() < 1e-6 || norm == 0.0, "frame {i} norm {norm}");
        }
    }

    #[test]
    fn dtw_perfect_self_match_scores_near_zero() {
        let samples = sine(SAMPLE_RATE, 250.0, 800, 0.6);
        let features = extract_features(&samples);
        let score = subsequence_dtw_score(&features, &features);
        assert!(score < 1e-6, "self-match should score ~0, got {score}");
    }

    #[test]
    fn dtw_open_end_finds_template_inside_longer_query() {
        // Query = silence, then the template's own waveform, then silence.
        let template_wave = sine(SAMPLE_RATE, 250.0, 800, 0.6);
        let silence = vec![0.0f32; (SAMPLE_RATE as usize * 300) / 1000];
        let mut query_wave = silence.clone();
        query_wave.extend_from_slice(&template_wave);
        query_wave.extend_from_slice(&silence);

        let template = extract_features(&template_wave);
        let query = extract_features(&query_wave);
        let score = subsequence_dtw_score(&template, &query);
        assert!(score < 0.65, "expected a good embedded match, got {score}");
    }

    #[test]
    fn loads_the_real_shipped_artifact_if_present() {
        let path = artifact_path();
        if !path.exists() {
            eprintln!("probe_templates.json not found at {path:?}, skipping");
            return;
        }
        let detector = ProbeDetector::load(&path).expect("load real probe templates");
        assert_eq!(detector.templates.len(), 3);
        assert!(detector.present_le < detector.absent_ge);
    }

    #[test]
    fn detect_finds_a_planted_template_turn_and_none_without_it() {
        let path = artifact_path();
        if !path.exists() {
            eprintln!("probe_templates.json not found at {path:?}, skipping");
            return;
        }
        let raw = fs::read_to_string(&path).unwrap();
        let file: ProbeTemplatesFile = serde_json::from_str(&raw).unwrap();
        let detector = ProbeDetector::load(&path).unwrap();

        // Reconstruct a waveform-free "clip" isn't possible from feature
        // vectors alone (DTW needs *L2-normalised feature rows*, which we
        // have directly from the artifact) -- build a synthetic Template
        // wrapping the first bank template's own vectors as if it were the
        // extracted query for one agent turn, bypassing audio synthesis.
        let bank_template = &file.templates[0];
        let frames = bank_template.len();
        let bands = bank_template[0].len();
        let mut data = Vec::with_capacity(frames * bands);
        for row in bank_template {
            data.extend_from_slice(row);
        }
        let query = Template { frames, bands, data };
        let score = detector.detector_score(&query);
        assert!(detector.classify(score) == Some(true), "exact template replay must classify as present, score={score}");

        // A pure-noise-shaped (but still L2-normalised) query of the same
        // length should NOT confidently match any of the 3 phrase templates.
        let mut noise_data = vec![0.0f64; frames * bands];
        for (i, v) in noise_data.iter_mut().enumerate() {
            // deterministic pseudo-noise, not actually random (no rng dep)
            *v = ((i * 2654435761) % 1000) as f64 / 1000.0 - 0.5;
        }
        // L2-normalise each row so it's a valid feature Template.
        for f in 0..frames {
            let row = &mut noise_data[f * bands..(f + 1) * bands];
            let norm: f64 = row.iter().map(|v| v * v).sum::<f64>().sqrt();
            if norm > 1e-8 {
                for v in row.iter_mut() {
                    *v /= norm;
                }
            }
        }
        let noise_query = Template { frames, bands, data: noise_data };
        let noise_score = detector.detector_score(&noise_query);
        assert_ne!(
            detector.classify(noise_score),
            Some(true),
            "unrelated noise must not confidently match, score={noise_score}"
        );
    }
}
