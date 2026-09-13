//! `/detect` end-to-end orchestration (T022): decode → VAD → features →
//! ONNX → verdict.
//!
//! [`analyze_bytes`] is the single place that wires T009 (WAV decode),
//! T011 (VAD, [`crate::audio::vad::detect_turns`]), T014 (behavioral
//! features, [`crate::features::extract`]) and T020 (ONNX inference,
//! [`crate::inference::model::Model`]) into one call. `routes::detect`
//! uses only [`Analysis::verdict`]; `routes::analyze` (not scored, ADR-013)
//! is meant to expose the rest — turns, events, named features, timings.
//!
//! Degenerate calls (no caller turns, a single active channel, near-silent
//! audio) are *not* a pipeline failure: the extractor defines a value for
//! every fc-1 feature on any input (see `features::mod`'s
//! `fully_empty_call_is_all_zero` / `degenerate_inputs_never_nan_or_inf`
//! tests), so they still get scored by the real model. Only bytes that
//! don't decode as WAV at all are a genuine failure here, which
//! `routes::detect` maps to the fallback verdict (ADR-006) — never this
//! function's own placeholder.

use std::io::Cursor;
use std::time::Instant;

use anyhow::{Context, Result};
use serde::Serialize;

use crate::analysis::waveform;
use crate::audio::vad::{detect_turns, Turn, VadParams};
use crate::features::{self, FC1_NAMES};
use crate::semantic::{PendingSemantic, SemanticOutcome};
use crate::state::SharedState;

/// Per-stage wall-clock cost of one `/detect` call, in milliseconds.
/// Budget (NFR-001, internal): decode 800ms, VAD 400ms, inference 50ms for
/// a worst-case ~180s call; features is comparatively free.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct TimingsMs {
    pub decode: f64,
    pub vad: f64,
    pub features: f64,
    pub inference: f64,
    pub total: f64,
}

/// §8.2 dialogue events, derived from the caller/agent turn lists — never
/// carried by `/detect` (ADR-013), only `/analyze`.
#[derive(Debug, Clone, Copy, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    /// Caller and agent speech overlap in time.
    Overlap,
    /// Caller starts speaking while inside an agent turn (a subset of
    /// `Overlap`, called out separately per §8.2).
    Interruption,
    /// A gap with neither channel active, longer than
    /// [`features::SILENCE_GAP_S`] (2s).
    Silence,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct Event {
    pub kind: EventKind,
    pub start: f32,
    pub end: f32,
}

/// How many turns each channel produced — cheap to log, useful to eyeball
/// degenerate calls without pulling the full turn list.
#[derive(Debug, Clone, Copy, Serialize)]
pub struct TurnCounts {
    pub caller: usize,
    pub agent: usize,
}

/// The model's verdict on one call, plus the raw probability behind it
/// (never exposed by `/detect`'s wire body, only used internally / by
/// `/analyze`).
#[derive(Debug, Clone, Copy, Serialize)]
pub struct Verdict {
    pub is_synthetic: bool,
    pub confidence: f64,
    pub p_synthetic: f64,
}

/// Everything one `/detect` (or `/analyze`) request computes. `/detect`'s
/// handler reads only `verdict`; every other field exists for `/analyze`
/// and for the structured log line `routes::detect` emits per request.
#[derive(Debug, Clone, Serialize)]
pub struct Analysis {
    pub duration_s: f32,
    pub turns: Vec<Turn>,
    pub turn_counts: TurnCounts,
    pub events: Vec<Event>,
    pub features: Vec<(&'static str, f64)>,
    pub verdict: Verdict,
    pub timings_ms: TimingsMs,
    pub model_version: Option<String>,
    pub waveform: waveform::Waveform,
    pub semantic: SemanticOutcome,
}

/// Run the full pipeline over a decoded-or-not WAV byte slice. Fails only
/// when `bytes` doesn't parse as WAV at all, or when scoring itself errors
/// (e.g. no model loaded) — both are genuine incidents, left for the
/// caller (`routes::detect`, via `run_failsafe`) to turn into the fallback
/// verdict and log.
pub async fn analyze_bytes(state: &SharedState, bytes: &[u8]) -> Result<Analysis> {
    analyze_bytes_with_timeout(state, bytes, state.config.semantic_timeout_ms).await
}

pub async fn analyze_bytes_with_timeout(
    state: &SharedState,
    bytes: &[u8],
    semantic_timeout_ms: u64,
) -> Result<Analysis> {
    let total_start = Instant::now();

    let decode_start = Instant::now();
    let (sample_rate, duration_s) = wav_duration(bytes).context("WAV decode failed")?;
    let (ch0_samples, ch1_samples) = wav_channels(bytes).context("WAV channel decode failed")?;
    let decode_ms = elapsed_ms(decode_start);

    let vad_start = Instant::now();
    let params = VadParams::default();
    let turns = detect_turns(bytes, &params).context("VAD failed")?;
    let vad_ms = elapsed_ms(vad_start);

    let (caller, agent) = turns.split();

    // Stage 1: SPAWN ASR in background if semantic engine is active
    let pending_semantic = if let Some(ref semantic) = state.semantic {
        semantic.spawn(&turns.turns, &ch0_samples, &ch1_samples, sample_rate)
    } else {
        PendingSemantic::Unavailable {
            reason: "disabled",
            probe_detected: false,
            probe_ms: 0.0,
        }
    };

    // Stage 2: Extract features & run ONNX inference concurrently
    let features_start = Instant::now();
    let feature_vec = features::extract(&caller, &agent, duration_s);
    let named_features: Vec<(&'static str, f64)> =
        FC1_NAMES.iter().copied().zip(feature_vec.iter().copied()).collect();
    let features_ms = elapsed_ms(features_start);

    let inference_start = Instant::now();
    let model = state.model.as_ref().context("no model loaded")?;
    let (scored, model_version, threshold) = {
        let mut guard = model.lock().expect("model mutex poisoned");
        let scored = guard.score(feature_vec).context("inference failed")?;
        let version = guard.meta.model_version.clone();
        let thr = std::env::var("CONCORDE_THRESHOLD")
            .ok()
            .and_then(|s| s.parse::<f64>().ok())
            .unwrap_or(guard.meta.threshold as f64);
        (scored, version, thr)
    };
    let inference_ms = elapsed_ms(inference_start);

    // Stage 3: Wait for ASR up to the remaining timeout budget
    let semantic_outcome = if let Some(ref semantic) = state.semantic {
        semantic.finish(pending_semantic, semantic_timeout_ms, scored.p_synthetic).await
    } else {
        SemanticOutcome::disabled(scored.p_synthetic)
    };

    // Stage 4: Produce final verdict from fused p_final
    let p_final = semantic_outcome.p_final;
    let is_synthetic = p_final >= threshold;
    let confidence = if is_synthetic { p_final } else { 1.0 - p_final };

    let events = compute_events(&caller, &agent, duration_s);
    let turn_counts = TurnCounts { caller: caller.len(), agent: agent.len() };

    let waveform_obj = waveform::compute(bytes, 50).context("waveform extraction failed")?;
    let total_ms = elapsed_ms(total_start);

    let verdict = Verdict {
        is_synthetic,
        confidence,
        p_synthetic: p_final,
    };

    // Fire-and-forget Tiger Data event (T048): a non-blocking `try_send`
    // via a bounded channel, never awaited here -- storage is
    // observability, not a dependency (§7.2). No-op when
    // `TIGERDATA_URL` is unset.
    crate::storage::record(
        state,
        crate::storage::DetectionEvent {
            request_id: None,
            call_id: None,
            is_synthetic: verdict.is_synthetic,
            confidence: verdict.confidence,
            p_synthetic: verdict.p_synthetic,
            duration_s,
            model_version: Some(model_version.clone()),
        },
    );

    Ok(Analysis {
        duration_s,
        turns: turns.turns,
        turn_counts,
        events,
        features: named_features,
        verdict,
        timings_ms: TimingsMs {
            decode: decode_ms,
            vad: vad_ms,
            features: features_ms,
            inference: inference_ms,
            total: total_ms,
        },
        model_version: Some(model_version),
        waveform: waveform_obj,
        semantic: semantic_outcome,
    })
}

fn elapsed_ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

/// Peek the WAV header to get `(sample_rate, duration_s)` without running
/// VAD. `detect_turns` re-parses the same bytes for the real decode; the
/// tiny duplicated header read is what lets `decode` and `vad` be reported
/// as separate timings (§8.1 of this task) instead of one fused number.
fn wav_duration(wav: &[u8]) -> Result<(u32, f32)> {
    let reader = hound::WavReader::new(Cursor::new(wav)).context("not a valid WAV file")?;
    let spec = reader.spec();
    if spec.sample_rate == 0 {
        anyhow::bail!("WAV header declares a 0 Hz sample rate");
    }
    let duration_s = reader.duration() as f32 / spec.sample_rate as f32;
    Ok((spec.sample_rate, duration_s))
}

fn wav_channels(wav: &[u8]) -> Result<(Vec<f32>, Vec<f32>)> {
    let mut reader = hound::WavReader::new(Cursor::new(wav)).context("not a valid WAV file")?;
    let spec = reader.spec();
    let num_channels = spec.channels as usize;
    if num_channels == 0 {
        anyhow::bail!("WAV has 0 channels");
    }

    let mut ch0 = Vec::new();
    let mut ch1 = Vec::new();

    match spec.sample_format {
        hound::SampleFormat::Int => {
            let max_val = (1i64 << (spec.bits_per_sample - 1)) as f32;
            let samples: Vec<i32> = reader.samples::<i32>().collect::<Result<_, _>>()?;
            for chunk in samples.chunks(num_channels) {
                if chunk.len() == num_channels {
                    let s0 = chunk[0] as f32 / max_val;
                    let s1 = if num_channels > 1 { chunk[1] as f32 / max_val } else { s0 };
                    ch0.push(s0);
                    ch1.push(s1);
                }
            }
        }
        hound::SampleFormat::Float => {
            let samples: Vec<f32> = reader.samples::<f32>().collect::<Result<_, _>>()?;
            for chunk in samples.chunks(num_channels) {
                if chunk.len() == num_channels {
                    let s0 = chunk[0];
                    let s1 = if num_channels > 1 { chunk[1] } else { s0 };
                    ch0.push(s0);
                    ch1.push(s1);
                }
            }
        }
    }

    Ok((ch0, ch1))
}

/// Derive §8.2 dialogue events from the caller/agent turn lists.
/// - `Overlap`: any caller/agent turn pair whose intervals intersect.
/// - `Interruption`: the caller starts inside an already-open agent turn
///   (a subset of the overlaps above).
/// - `Silence`: a gap longer than [`features::SILENCE_GAP_S`] where
///   neither channel is active, computed over the merged busy spans of
///   both channels across `[0, duration_s]`.
fn compute_events(caller: &[(f32, f32)], agent: &[(f32, f32)], duration_s: f32) -> Vec<Event> {
    let mut events = Vec::new();

    for &(caller_start, caller_end) in caller {
        for &(agent_start, agent_end) in agent {
            let overlap_start = caller_start.max(agent_start);
            let overlap_end = caller_end.min(agent_end);
            if overlap_start < overlap_end {
                events.push(Event { kind: EventKind::Overlap, start: overlap_start, end: overlap_end });
                if caller_start >= agent_start && caller_start < agent_end {
                    events.push(Event {
                        kind: EventKind::Interruption,
                        start: caller_start,
                        end: overlap_end,
                    });
                }
            }
        }
    }

    let busy: Vec<(f64, f64)> = caller
        .iter()
        .chain(agent.iter())
        .map(|&(s, e)| (s as f64, e as f64))
        .collect();
    let merged = features::merge_busy_intervals(&busy);
    let mut cursor = 0.0f64;
    for &(start, end) in &merged {
        let gap = start - cursor;
        if gap > features::SILENCE_GAP_S {
            events.push(Event { kind: EventKind::Silence, start: cursor as f32, end: start as f32 });
        }
        cursor = cursor.max(end);
    }
    let tail_gap = duration_s as f64 - cursor;
    if tail_gap > features::SILENCE_GAP_S {
        events.push(Event { kind: EventKind::Silence, start: cursor as f32, end: duration_s });
    }

    events.sort_by(|a, b| a.start.partial_cmp(&b.start).unwrap());
    events
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::inference::model::Model;
    use crate::state::AppState;
    use std::path::PathBuf;
    use std::process::Command;
    use std::sync::{Arc, Mutex};

    fn fixtures_dir() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
    }

    fn test_state_with_model() -> SharedState {
        let dir = fixtures_dir();
        let onnx = dir.join("dummy_fc1.onnx");
        if !onnx.exists() {
            let status = Command::new("python3")
                .arg(dir.join("make_dummy_model.py"))
                .status()
                .expect("python3 available");
            assert!(status.success());
        }
        let model = Model::load(&onnx).expect("load dummy model");
        let mut config = Config::from_env();
        config.strict = false;
        Arc::new(AppState::new(config, Some(Arc::new(Mutex::new(model)))))
    }

    fn stereo_wav(ch0: &[f32], ch1: &[f32], sr: u32) -> Vec<u8> {
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

    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n)
            .map(|i| {
                let t = i as f32 / sr as f32;
                amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect()
    }

    #[tokio::test]
    async fn happy_path_produces_a_real_scored_verdict() {
        let state = test_state_with_model();
        let sr = 8000u32;
        let ch0 = sine(sr, 440.0, 400, 0.5);
        let ch1 = sine(sr, 440.0, 400, 0.5);
        let wav = stereo_wav(&ch0, &ch1, sr);

        let analysis = analyze_bytes(&state, &wav).await.expect("pipeline succeeds");
        assert!((0.0..=1.0).contains(&analysis.verdict.confidence));
        assert_eq!(analysis.features.len(), 23);
        assert!(analysis.model_version.is_some());
        assert!(analysis.timings_ms.total >= 0.0);
    }

    #[tokio::test]
    async fn degenerate_silent_call_still_gets_a_real_verdict_not_the_placeholder() {
        let state = test_state_with_model();
        let sr = 8000u32;
        // 2s of pure silence on both channels: no turns at all.
        let silence = vec![0.0f32; sr as usize * 2];
        let wav = stereo_wav(&silence, &silence, sr);

        let analysis = analyze_bytes(&state, &wav).await.expect("degenerate calls still score");
        assert_eq!(analysis.turn_counts.caller, 0);
        assert_eq!(analysis.turn_counts.agent, 0);
        // The extractor defines an all-zero vector for a silent call, and
        // that vector goes through the real model rather than being
        // special-cased into the ADR-006 fallback.
        assert!(analysis.features.iter().all(|(_, v)| *v == 0.0));
    }

    #[tokio::test]
    async fn not_a_wav_file_is_a_pipeline_error() {
        let state = test_state_with_model();
        let err = analyze_bytes(&state, b"not a wav file at all").await.unwrap_err();
        assert!(err.to_string().contains("WAV decode failed"));
    }

    #[tokio::test]
    async fn missing_model_is_a_pipeline_error() {
        let config = Config::from_env();
        let state = std::sync::Arc::new(AppState::new(config, None));
        let sr = 8000u32;
        let ch0 = sine(sr, 440.0, 300, 0.5);
        let wav = stereo_wav(&ch0, &[], sr);
        let err = analyze_bytes(&state, &wav).await.unwrap_err();
        assert!(err.to_string().contains("no model loaded"));
    }

    #[test]
    fn overlap_and_interruption_events_detected() {
        // Agent talks 0..1s; caller barges in at 0.2s and keeps going to 1.5s.
        let caller = [(0.2f32, 1.5)];
        let agent = [(0.0f32, 1.0)];
        let events = compute_events(&caller, &agent, 5.0);
        assert!(events.iter().any(|e| matches!(e.kind, EventKind::Overlap)));
        assert!(events.iter().any(|e| matches!(e.kind, EventKind::Interruption)));
    }

    #[test]
    fn long_mutual_gap_is_a_silence_event() {
        let agent = [(0.0f32, 1.0)];
        let caller = [(4.0f32, 5.0)]; // 3s mutual gap > 2s threshold
        let events = compute_events(&caller, &agent, 5.0);
        assert!(events.iter().any(|e| matches!(e.kind, EventKind::Silence)));
    }

    #[test]
    fn short_gap_is_not_a_silence_event() {
        let agent = [(0.0f32, 1.0)];
        let caller = [(1.5f32, 2.0)]; // 0.5s gap, well under 2s
        let events = compute_events(&caller, &agent, 2.0);
        assert!(!events.iter().any(|e| matches!(e.kind, EventKind::Silence)));
    }
}
