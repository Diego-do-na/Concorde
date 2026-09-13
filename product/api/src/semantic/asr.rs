//! Local ASR via `whisper-rs` 0.16 (T045): one `WhisperContext` created at
//! boot (`state::AppState`), a fresh `WhisperState` per request. Runs
//! fully locally -- no audio or text ever leaves the process (ADR-008,
//! the semantic layer's privacy contract).
//!
//! [`Transcriber`] is the seam tests use to simulate a slow/erroring ASR
//! backend (§ T045(b)VERIFY "ASR slow path... simulated via... a sleep
//! hook") without needing the real ggml model file in CI.

use anyhow::{Context, Result};
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

/// Caps how long a single answer-turn clip is fed to whisper (§ T045: "8->16
/// kHz resampled, capped at 15 s").
pub const MAX_ASR_SECONDS: f32 = 15.0;
const ASR_SAMPLE_RATE: u32 = 16_000;

/// Anything that can turn 16 kHz mono `f32` PCM into text. The real
/// production impl is [`LocalAsr`]; tests substitute a fake to simulate
/// latency/errors without the real model file.
pub trait Transcriber: Send + Sync {
    fn transcribe(&self, samples_16k: &[f32]) -> Result<String>;
}

pub struct LocalAsr {
    ctx: WhisperContext,
    threads: i32,
}

impl LocalAsr {
    /// Loads the ggml model once (§ T045: "ONE `WhisperContext` created at
    /// boot"). `threads` is `CONCORDE_WHISPER_THREADS`.
    pub fn load(model_path: &str, threads: usize) -> Result<Self> {
        let ctx = WhisperContext::new_with_params(model_path, WhisperContextParameters::default())
            .with_context(|| format!("load whisper model {model_path}"))?;
        Ok(Self { ctx, threads: threads.max(1) as i32 })
    }

    /// `audio_ctx` sized from the segment length (§ T045): <= 10 s -> 512,
    /// <= 15 s -> 768, else 1500 (whisper.cpp's own full-context default).
    fn audio_ctx_for(duration_s: f32) -> i32 {
        if duration_s <= 10.0 {
            512
        } else if duration_s <= 15.0 {
            768
        } else {
            1500
        }
    }
}

impl Transcriber for LocalAsr {
    fn transcribe(&self, samples_16k: &[f32]) -> Result<String> {
        let mut state = self.ctx.create_state().context("create whisper state")?;

        let duration_s = samples_16k.len() as f32 / ASR_SAMPLE_RATE as f32;
        let audio_ctx = Self::audio_ctx_for(duration_s);

        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_language(Some("es"));
        params.set_n_threads(self.threads);
        params.set_translate(false);
        params.set_no_timestamps(true);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        params.set_print_special(false);
        params.set_single_segment(false);
        params.set_audio_ctx(audio_ctx);
        params.set_suppress_blank(true);

        state.full(params, samples_16k).context("whisper full() failed")?;

        let n = state.full_n_segments();
        let mut text = String::new();
        for i in 0..n {
            if let Some(seg) = state.get_segment(i) {
                if let Ok(s) = seg.to_str() {
                    if !text.is_empty() {
                        text.push(' ');
                    }
                    text.push_str(s.trim());
                }
            }
        }
        Ok(text.trim().to_string())
    }
}

/// Linear-interpolation resampler. Not bit-parity with `scipy.signal.
/// resample_poly` (the Python reference, T042), but whisper.cpp's own
/// mel/attention front end is robust to that difference -- what matters
/// for T045's parity gate is the downstream categorical output
/// (`answer_type`/`invention_score`), not sample-level audio fidelity, and
/// avoiding a full polyphase-FIR implementation (or a new heavyweight DSP
/// dependency) keeps this self-contained.
pub fn resample_linear(samples: &[f32], sr_in: u32, sr_out: u32) -> Vec<f32> {
    if samples.is_empty() || sr_in == sr_out {
        return samples.to_vec();
    }
    let ratio = sr_out as f64 / sr_in as f64;
    let out_len = ((samples.len() as f64) * ratio).round().max(1.0) as usize;
    let mut out = Vec::with_capacity(out_len);
    let last_idx = samples.len() - 1;
    for i in 0..out_len {
        let src_pos = i as f64 / ratio;
        let idx = src_pos.floor() as usize;
        let frac = src_pos - idx as f64;
        if idx >= last_idx {
            out.push(samples[last_idx]);
        } else {
            let a = samples[idx] as f64;
            let b = samples[idx + 1] as f64;
            out.push((a + (b - a) * frac) as f32);
        }
    }
    out
}

/// Resample to 16 kHz and cap at [`MAX_ASR_SECONDS`], in that order (§
/// T045: "8->16 kHz resampled, capped at 15 s").
pub fn prepare_for_asr(samples: &[f32], sr_in: u32) -> Vec<f32> {
    let mut resampled = resample_linear(samples, sr_in, ASR_SAMPLE_RATE);
    let max_samples = (MAX_ASR_SECONDS * ASR_SAMPLE_RATE as f32) as usize;
    if resampled.len() > max_samples {
        resampled.truncate(max_samples);
    }
    resampled
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resample_linear_doubles_length_for_8k_to_16k() {
        let samples: Vec<f32> = (0..800).map(|i| (i as f32 / 800.0).sin()).collect();
        let out = resample_linear(&samples, 8_000, 16_000);
        assert_eq!(out.len(), 1600);
    }

    #[test]
    fn resample_linear_is_identity_for_equal_rates() {
        let samples = vec![0.1f32, 0.2, -0.3, 0.4];
        let out = resample_linear(&samples, 16_000, 16_000);
        assert_eq!(out, samples);
    }

    #[test]
    fn prepare_for_asr_caps_at_max_seconds() {
        let sr = 8_000u32;
        let samples = vec![0.1f32; sr as usize * 20]; // 20s of audio at 8kHz
        let out = prepare_for_asr(&samples, sr);
        let max_samples = (MAX_ASR_SECONDS * ASR_SAMPLE_RATE as f32) as usize;
        assert_eq!(out.len(), max_samples);
    }

    #[test]
    fn audio_ctx_sizing_matches_spec_thresholds() {
        assert_eq!(LocalAsr::audio_ctx_for(5.0), 512);
        assert_eq!(LocalAsr::audio_ctx_for(10.0), 512);
        assert_eq!(LocalAsr::audio_ctx_for(12.0), 768);
        assert_eq!(LocalAsr::audio_ctx_for(15.0), 768);
        assert_eq!(LocalAsr::audio_ctx_for(20.0), 1500);
    }

    /// Real-model smoke test: skipped (not failed) if no ggml model file is
    /// configured/available in this environment (§ T045(b): "the real model
    /// file (skipped if absent)").
    #[test]
    fn real_model_transcribes_synthetic_silence_without_error() {
        let path = match std::env::var("CONCORDE_WHISPER_MODEL_PATH") {
            Ok(p) if std::path::Path::new(&p).exists() => p,
            _ => {
                eprintln!("CONCORDE_WHISPER_MODEL_PATH not set/found, skipping real-model test");
                return;
            }
        };
        let asr = LocalAsr::load(&path, 1).expect("load real whisper model");
        let silence = vec![0.0f32; ASR_SAMPLE_RATE as usize * 2];
        let text = asr.transcribe(&silence).expect("transcribe silence");
        // No assertion on content (silence -> often empty or filler text
        // depending on the model); the real assertion is "did not error".
        let _ = text;
    }
}
