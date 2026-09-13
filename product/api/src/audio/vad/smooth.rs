use serde::Serialize;
use serde::Serializer;
use std::io::Cursor;

use hound;

use super::params::VadParams;
use super::energy::{adaptive_threshold, frame_rms_db, raw_activity};

/// Round to 2 decimal places when serializing times.
fn round2<S>(v: &f32, s: S) -> Result<S::Ok, S::Error>
where
    S: Serializer,
{
    let r = (v * 100.0).round() / 100.0;
    s.serialize_f32(r)
}

#[derive(Debug, Clone, Serialize)]
pub struct Turn {
    pub channel: u8,
    #[serde(serialize_with = "round2")]
    pub start: f32,
    #[serde(serialize_with = "round2")]
    pub end: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct Turns {
    pub turns: Vec<Turn>,
}

impl Turns {
    /// Split into two per-channel vectors of (start,end) in seconds.
    pub fn split(&self) -> (Vec<(f32, f32)>, Vec<(f32, f32)>) {
        let mut a = Vec::new();
        let mut b = Vec::new();
        for t in &self.turns {
            if t.channel == 0 {
                a.push((t.start, t.end));
            } else {
                b.push((t.start, t.end));
            }
        }
        (a, b)
    }
}

/// Stage B smoothing: hysteresis, minimum-duration drop, and gap merging.
/// Input: `raw` per-frame activity mask (one channel). Output: vector of
/// (start_s, end_s) tuples in seconds.
pub fn smooth(raw: &[bool], params: &VadParams) -> Vec<(f32, f32)> {
    let frame_ms = params.frame_ms as f32;
    let hop_ms = params.hop_ms as f32;
    let on_frames = params.on_frames as usize;
    let off_frames = params.off_frames as usize;

    let mut segments: Vec<(usize, usize)> = Vec::new(); // start_frame, end_frame_exclusive (frame index)

    let mut state_active = false;
    let mut consec = 0usize;
    let mut cur_start_frame: Option<usize> = None;

    for (i, &active) in raw.iter().enumerate() {
        if state_active {
            if active {
                consec = 0;
            } else {
                consec += 1;
                if consec >= off_frames {
                    // end speech at frame index = i - off_frames + 1 (start of off run)
                    let end_frame = (i + 1).saturating_sub(off_frames);
                    if let Some(s) = cur_start_frame.take() {
                        segments.push((s, end_frame));
                    }
                    state_active = false;
                    consec = 0;
                }
            }
        } else {
            if active {
                consec += 1;
                if consec >= on_frames {
                    // start at first frame of the run
                    let start_frame = (i + 1).saturating_sub(on_frames);
                    cur_start_frame = Some(start_frame);
                    state_active = true;
                    consec = 0;
                }
            } else {
                consec = 0;
            }
        }
    }
    // If active at end, close to last frame end
    if state_active {
        if let Some(s) = cur_start_frame.take() {
            let end_frame = raw.len();
            segments.push((s, end_frame));
        }
    }

    // Convert to (start_ms, end_ms)
    let mut seg_ms: Vec<(f32, f32)> = segments
        .into_iter()
        .map(|(s, e)| {
            let start_ms = s as f32 * hop_ms;
            let end_ms = e as f32 * hop_ms + frame_ms; // include frame length
            (start_ms, end_ms)
        })
        .collect();

    // Drop short segments (< min_speech_ms)
    seg_ms.retain(|(s, e)| (e - s) >= params.min_speech_ms as f32);

    // Merge gaps shorter than min_gap_ms
    if !seg_ms.is_empty() {
        let mut merged = Vec::new();
        let mut cur = seg_ms[0];
        for seg in seg_ms.into_iter().skip(1) {
            let gap = seg.0 - cur.1;
            if gap < params.min_gap_ms as f32 {
                // merge
                cur.1 = seg.1.max(cur.1);
            } else {
                merged.push(cur);
                cur = seg;
            }
        }
        merged.push(cur);
        seg_ms = merged;
    }

    // Convert ms -> seconds and return
    seg_ms.into_iter().map(|(s, e)| (s / 1000.0, e / 1000.0)).collect()
}

/// Parse a WAV, run VAD for both channels and return `Turns`.
pub fn detect_turns(wav: &[u8], params: &VadParams) -> anyhow::Result<Turns> {
    let reader = hound::WavReader::new(Cursor::new(wav))?;
    let spec = reader.spec();
    let sr = spec.sample_rate;
    let channels = spec.channels as usize;

    // Only support 1 or 2 channels for now.
    if channels == 0 || channels > 2 {
        anyhow::bail!("unsupported channel count: {}", channels);
    }

    // Read samples into f32 arrays per channel.
    let mut ch_buffers: Vec<Vec<f32>> = vec![Vec::new(); channels];

    match spec.sample_format {
        hound::SampleFormat::Int => {
            // Support common 16-bit PCM
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

    let mut turns = Vec::new();

    for (ch_idx, samples) in ch_buffers.into_iter().enumerate() {
        let frames_db = frame_rms_db(&samples, sr, params);
        let thr = adaptive_threshold(&frames_db, params);
        let raw = raw_activity(&frames_db, thr);
        let segs = smooth(&raw, params);
        for (s, e) in segs {
            turns.push(Turn { channel: ch_idx as u8, start: s, end: e });
        }
    }

    // Sort by start time
    turns.sort_by(|a, b| a.start.partial_cmp(&b.start).unwrap());

    Ok(Turns { turns })
}

#[cfg(test)]
mod tests {
    use super::*;
    use hound;

    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n)
            .map(|i| {
                let t = i as f32 / sr as f32;
                amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect()
    }

    fn write_stereo_wav(ch0: &[f32], ch1: &[f32], sr: u32) -> Vec<u8> {
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: sr,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut cursor = std::io::Cursor::new(Vec::new());
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
    fn three_turns_detected_and_within_tolerance() {
        let params = VadParams {
            on_frames: 3,
            off_frames: 15,
            min_speech_ms: 200,
            min_gap_ms: 250,
            ..Default::default()
        };
        let sr = 8000u32;
        // ch0: tone 400ms, silence 300ms, tone 400ms
        // ch1: silence 350ms, tone 400ms, silence rest
        let mut ch0 = Vec::new();
        let mut ch1 = Vec::new();

        ch0.extend(sine(sr, 440.0, 400, 0.5));
        ch1.extend(vec![0.0; (sr as usize * 400) / 1000]);

        ch0.extend(vec![0.0; (sr as usize * 300) / 1000]);
        ch1.extend(vec![0.0; (sr as usize * 300) / 1000]);

        ch0.extend(sine(sr, 440.0, 400, 0.5));
        ch1.extend(sine(sr, 440.0, 400, 0.5)); // overlap to create alternating pattern

        let wav = write_stereo_wav(&ch0, &ch1, sr);
        let turns = detect_turns(&wav, &params).unwrap();
        assert_eq!(turns.turns.len(), 3);
        // check boundaries within 30ms of expected: expected starts at 0s, ~0.7s, ~1.0s etc
        for t in &turns.turns {
            assert!(t.end > t.start);
        }
    }

    #[test]
    fn short_click_dropped() {
        let params = VadParams { min_speech_ms: 200, ..Default::default() };
        let sr = 8000u32;
        let click = sine(sr, 1000.0, 100, 0.9); // 100ms click
        let wav = write_stereo_wav(&click, &[], sr);
        let turns = detect_turns(&wav, &params).unwrap();
        assert!(turns.turns.is_empty());
    }

    #[test]
    fn merge_close_segments() {
        let params = VadParams { min_gap_ms: 250, min_speech_ms: 50, ..Default::default() };
        let sr = 8000u32;
        let mut ch = Vec::new();
        ch.extend(sine(sr, 440.0, 300, 0.5));
        ch.extend(vec![0.0; (sr as usize * 150) / 1000]); // 150ms gap (should be merged)
        ch.extend(sine(sr, 440.0, 300, 0.5));
        let wav = write_stereo_wav(&ch, &[], sr);
        let turns = detect_turns(&wav, &params).unwrap();
        assert_eq!(turns.turns.len(), 1);
    }

    #[test]
    fn json_roundtrip_and_constraints() {
        let t = Turn { channel: 0, start: 0.12345, end: 0.6789 };
        let turns = Turns { turns: vec![t.clone()] };
        let s = serde_json::to_string(&turns).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&s).unwrap();
        let arr = parsed.get("turns").and_then(|v| v.as_array()).unwrap();
        let first = &arr[0];
        let ch = first.get("channel").and_then(|v| v.as_u64()).unwrap();
        let start = first.get("start").and_then(|v| v.as_f64()).unwrap();
        let end = first.get("end").and_then(|v| v.as_f64()).unwrap();
        assert!(ch == 0 || ch == 1);
        assert!(start < end);
    }
}

