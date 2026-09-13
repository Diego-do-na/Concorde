//! Waveform envelope extraction for console visualization (ADR-013).
//!
//! Computes the peak amplitude in fixed-size time buckets (50 ms each)
//! for each channel independently. The result is a low-dimensional
//! representation suitable for the dashboard's dual-channel plot.
//!
//! Input: WAV bytes (already decoded by the pipeline).
//! Output: `Waveform { caller: Vec<f32>, agent: Vec<f32>, bucket_ms: u32 }`
//! where each value is the peak absolute sample value in [0, 1], rounded to 3 decimals.

use std::io::Cursor;

use anyhow::Result;
use serde::Serialize;

/// Waveform envelope for both channels, bucketed at 50 ms intervals.
#[derive(Debug, Clone, Serialize)]
pub struct Waveform {
    pub caller: Vec<f32>,
    pub agent: Vec<f32>,
    pub bucket_ms: u32,
}

/// Compute waveform envelope from WAV bytes.
///
/// # Arguments
/// - `wav` — raw WAV bytes (any mono/stereo, 8000 Hz, 16/32-bit or float)
/// - `bucket_ms` — size of each bucket in milliseconds (typically 50)
///
/// # Returns
/// A `Waveform` with peak absolute values per bucket for each channel.
/// Channels beyond the first two are ignored (only ch0 and ch1 are used).
/// If only one channel exists, agent is returned as all zeros.
pub fn compute(wav: &[u8], bucket_ms: u32) -> Result<Waveform> {
    if bucket_ms == 0 {
        anyhow::bail!("bucket_ms must be positive");
    }

    let reader = hound::WavReader::new(Cursor::new(wav))?;
    let spec = reader.spec();
    let sr = spec.sample_rate;
    let channels = spec.channels as usize;

    if channels == 0 || channels > 2 {
        anyhow::bail!("unsupported channel count: {}", channels);
    }

    // Read samples into per-channel buffers
    let mut ch_buffers: Vec<Vec<f32>> = vec![Vec::new(); channels];

    match spec.sample_format {
        hound::SampleFormat::Int => {
            if spec.bits_per_sample == 16 {
                let mut samples = reader.into_samples::<i16>();
                let mut idx = 0usize;
                while let Some(s) = samples.next() {
                    let v = s? as f32 / i16::MAX as f32;
                    ch_buffers[idx % channels].push(v);
                    idx += 1;
                }
            } else if spec.bits_per_sample == 32 {
                let mut samples = reader.into_samples::<i32>();
                let mut idx = 0usize;
                while let Some(s) = samples.next() {
                    let v = s? as f32 / i32::MAX as f32;
                    ch_buffers[idx % channels].push(v);
                    idx += 1;
                }
            } else {
                anyhow::bail!("unsupported PCM bits_per_sample: {}", spec.bits_per_sample);
            }
        }
        hound::SampleFormat::Float => {
            let mut samples = reader.into_samples::<f32>();
            let mut idx = 0usize;
            while let Some(s) = samples.next() {
                let v = s?;
                ch_buffers[idx % channels].push(v);
                idx += 1;
            }
        }
    }

    // Compute peak envelopes for each channel
    let caller = compute_envelope(&ch_buffers[0], sr, bucket_ms);
    let agent = if channels > 1 { compute_envelope(&ch_buffers[1], sr, bucket_ms) } else { Vec::new() };

    Ok(Waveform { caller, agent, bucket_ms })
}

/// Compute the peak absolute value per bucket for a single channel.
///
/// # Arguments
/// - `samples` — normalized audio samples in [-1, 1]
/// - `sample_rate` — sample rate in Hz
/// - `bucket_ms` — bucket size in milliseconds
///
/// # Returns
/// Vector of peak absolute values, rounded to 3 decimals, one per bucket.
fn compute_envelope(samples: &[f32], sample_rate: u32, bucket_ms: u32) -> Vec<f32> {
    if samples.is_empty() || sample_rate == 0 {
        return Vec::new();
    }

    let bucket_samples = ((sample_rate as u64 * bucket_ms as u64) / 1000) as usize;
    if bucket_samples == 0 {
        return Vec::new();
    }

    let num_buckets = (samples.len() + bucket_samples - 1) / bucket_samples;
    let mut envelope = Vec::with_capacity(num_buckets);

    for bucket_idx in 0..num_buckets {
        let start = bucket_idx * bucket_samples;
        let end = (start + bucket_samples).min(samples.len());
        let bucket = &samples[start..end];

        if bucket.is_empty() {
            envelope.push(0.0);
        } else {
            let peak = bucket.iter().map(|s| s.abs()).max_by(|a, b| a.partial_cmp(b).unwrap()).unwrap_or(0.0);
            let rounded = (peak * 1000.0).round() / 1000.0; // 3 decimals
            envelope.push(rounded);
        }
    }

    envelope
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n)
            .map(|i| {
                let t = i as f32 / sr as f32;
                amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect()
    }

    fn stereo_wav(ch0: &[f32], ch1: &[f32], sr: u32) -> Vec<u8> {
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: sr,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut cursor = Cursor::new(Vec::new());
        let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();
        let len = ch0.len().max(ch1.len());
        for i in 0..len {
            let a = *ch0.get(i).unwrap_or(&0.0);
            let b = *ch1.get(i).unwrap_or(&0.0);
            writer.write_sample((a.clamp(-1.0, 1.0) * i16::MAX as f32) as i16).unwrap();
            writer.write_sample((b.clamp(-1.0, 1.0) * i16::MAX as f32) as i16).unwrap();
        }
        writer.finalize().unwrap();
        cursor.into_inner()
    }

    #[test]
    fn two_second_synthetic_file_gives_40_buckets() {
        let sr = 8000u32;
        let ch0 = sine(sr, 440.0, 2000, 0.5); // 2 seconds
        let ch1 = sine(sr, 440.0, 2000, 0.5);
        let wav = stereo_wav(&ch0, &ch1, sr);

        let waveform = compute(&wav, 50).expect("compute waveform");
        assert_eq!(waveform.caller.len(), 40, "2000ms / 50ms = 40 buckets");
        assert_eq!(waveform.agent.len(), 40);
        assert_eq!(waveform.bucket_ms, 50);
    }

    #[test]
    fn silence_gives_all_zeros() {
        let sr = 8000u32;
        let silence = vec![0.0; sr as usize]; // 1 second of silence
        let wav = stereo_wav(&silence, &silence, sr);

        let waveform = compute(&wav, 50).expect("compute waveform");
        for &val in &waveform.caller {
            assert_eq!(val, 0.0);
        }
        for &val in &waveform.agent {
            assert_eq!(val, 0.0);
        }
    }

    #[test]
    fn minus_6_dbfs_tone_gives_peak_approximately_0_5() {
        let sr = 8000u32;
        // -6 dB amplitude = 10^(-6/20) ≈ 0.501
        let amp = 10_f32.powf(-6.0 / 20.0);
        let ch0 = sine(sr, 440.0, 1000, amp);
        let ch1 = vec![0.0; ch0.len()];
        let wav = stereo_wav(&ch0, &ch1, sr);

        let waveform = compute(&wav, 50).expect("compute waveform");
        // All buckets should be close to 0.5
        for &val in &waveform.caller {
            assert!((val - 0.5).abs() < 0.01, "expected ~0.5, got {}", val);
        }
    }

    #[test]
    fn mono_wav_returns_empty_agent_channel() {
        let sr = 8000u32;
        let ch0 = sine(sr, 440.0, 1000, 0.5);

        // Create mono WAV
        let spec = hound::WavSpec {
            channels: 1,
            sample_rate: sr,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut cursor = Cursor::new(Vec::new());
        let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();
        for &sample in &ch0 {
            writer.write_sample((sample.clamp(-1.0, 1.0) * i16::MAX as f32) as i16).unwrap();
        }
        writer.finalize().unwrap();
        let wav = cursor.into_inner();

        let waveform = compute(&wav, 50).expect("compute waveform");
        assert!(!waveform.caller.is_empty());
        assert!(waveform.agent.is_empty());
    }

    #[test]
    fn peak_amplitude_is_rounded_to_three_decimals() {
        let sr = 8000u32;
        // Create a channel with a specific amplitude that will have many decimal places
        let amp = 0.123456789_f32;
        let ch0 = sine(sr, 440.0, 100, amp); // 100ms = ~1 bucket
        let ch1 = vec![0.0; ch0.len()];
        let wav = stereo_wav(&ch0, &ch1, sr);

        let waveform = compute(&wav, 50).expect("compute waveform");
        for &val in &waveform.caller {
            if val > 0.0 {
                // Check that it's rounded to at most 3 decimals
                let rounded = (val * 1000.0).round() / 1000.0;
                assert_eq!(val, rounded, "value {} not rounded to 3 decimals", val);
            }
        }
    }
}
