//! Live local semantic vote for `/detect` (T045, ADR-008, NFR-001,
//! NFR-010): a probe-turn detector plus one local ASR call on the caller's
//! answer turn, fused into the behavioral verdict under a hard timeout.
//! Every component is local -- no audio or transcript text ever leaves
//! this process (the privacy contract this whole layer is built around).
//!
//! Module map:
//! - [`probe`] -- T043's log-mel + subsequence-DTW probe-turn detector.
//! - [`asr`] -- `whisper-rs` wrapper (+ resampling) that turns one turn's
//!   audio into text.
//! - [`rules`] -- `answer_type`/`invention_score`, ported from
//!   `product/ml/semantic/rules.py`.
//! - [`fusion`] -- the runtime `logit(p_final) = logit(p_behavioral) +
//!   w*(invention_score-0.5)` rule, from `product/artifacts/
//!   semantic_fusion.json`.
//!
//! [`SemanticEngine`] is the boot-time singleton (`AppState::semantic`):
//! one `WhisperContext`, the loaded probe template bank, the fusion
//! params, and a `tokio::sync::Semaphore` bounding concurrent ASR work.
//! [`SemanticEngine::spawn`] / [`SemanticEngine::finish`] split the "spawn
//! ASR, do other work concurrently, wait with a timeout" flow the pipeline
//! needs across the one point where `pipeline::analyze_bytes` does
//! CPU-bound feature extraction + inference in between.

pub mod asr;
pub mod fusion;
pub mod probe;
pub mod rules;

use std::path::Path;
use std::sync::Arc;
use std::time::{Duration, Instant};

use anyhow::{Context, Result};
use serde::Serialize;
use tokio::sync::Semaphore;

use crate::audio::vad::Turn;
use crate::config::Config;

/// `CONCORDE_PROBE_TEMPLATES_PATH` is read directly here (not via
/// `Config`) for the same reason `analysis::effective_threshold`
/// duplicates env parsing instead of extending `Config`: this task's scope
/// doesn't extend to `config.rs`.
fn probe_templates_path() -> String {
    std::env::var("CONCORDE_PROBE_TEMPLATES_PATH")
        .unwrap_or_else(|_| "artifacts/probe_templates.json".to_string())
}

fn elapsed_ms(start: Instant) -> f64 {
    start.elapsed().as_secs_f64() * 1000.0
}

/// Seam over [`probe::ProbeDetector`] so orchestration (`SemanticEngine`)
/// can be tested independently of the real DTW/log-mel matching (which is
/// unit-tested directly in `probe.rs` against the real template bank).
pub trait ProbeSource: Send + Sync {
    fn find_probe(&self, channel1: &[f32], turns: &[Turn]) -> Option<probe::ProbeMatch>;
}

impl ProbeSource for probe::ProbeDetector {
    fn find_probe(&self, channel1: &[f32], turns: &[Turn]) -> Option<probe::ProbeMatch> {
        self.detect(channel1, turns)
    }
}

/// Everything the pipeline/`, /analyze` need out of one semantic pass. When
/// `available` is false, `p_final == p_behavioral` exactly (NFR-010) and
/// `invention_score` is the neutral `0.5` default (never a silently
/// imputed real-looking number, AGENTS.md rule 4 / ADR-008).
#[derive(Debug, Clone, Serialize)]
pub struct SemanticOutcome {
    pub available: bool,
    pub reason: &'static str,
    pub probe_detected: bool,
    pub probe_ms: f64,
    pub asr_ms: Option<f64>,
    pub answer_type: Option<&'static str>,
    pub invention_score: f64,
    pub p_behavioral: f64,
    pub p_final: f64,
}

impl SemanticOutcome {
    /// The `p_final = p_behavioral`, `available = false` case, tagged with
    /// why (`reason`) -- every "semantic absent" path in this module goes
    /// through this constructor so the invariant can't be broken locally.
    fn unavailable(reason: &'static str, probe_detected: bool, probe_ms: f64, p_behavioral: f64) -> Self {
        Self {
            available: false,
            reason,
            probe_detected,
            probe_ms,
            asr_ms: None,
            answer_type: None,
            invention_score: 0.5,
            p_behavioral,
            p_final: p_behavioral,
        }
    }

    /// Disabled (`CONCORDE_SEMANTIC_ENABLED=false`) or no engine booted --
    /// the cheapest possible path, no probe/ASR work attempted at all.
    pub fn disabled(p_behavioral: f64) -> Self {
        Self::unavailable("disabled", false, 0.0, p_behavioral)
    }
}

/// Holds a spawned-but-not-yet-awaited ASR call: the permit that bounds
/// concurrency is captured INSIDE the spawned closure (not held here), so
/// it's released only when the real whisper call actually finishes --
/// even if this request abandons it on timeout (§ below).
pub struct PendingAsr {
    handle: tokio::task::JoinHandle<Result<String>>,
    started_at: Instant,
}

/// Result of [`SemanticEngine::spawn`]: either semantic is already known
/// to be unavailable (no probe / no answer turn / busy / no runtime), or
/// an ASR call is in flight and [`SemanticEngine::finish`] needs to wait
/// on it (up to the caller's timeout budget).
pub enum PendingSemantic {
    Unavailable { reason: &'static str, probe_detected: bool, probe_ms: f64 },
    Waiting { probe_ms: f64, pending: PendingAsr },
}

/// Boot-time singleton: one `WhisperContext` (via [`asr::LocalAsr`]), the
/// loaded probe template bank, the fusion params, and the ASR concurrency
/// gate. Constructed once in `main.rs` alongside the ONNX model (§
/// `AppState::semantic`) -- never per-request.
pub struct SemanticEngine {
    probe: Arc<dyn ProbeSource>,
    transcriber: Arc<dyn asr::Transcriber>,
    fusion: fusion::FusionParams,
    semaphore: Arc<Semaphore>,
}

impl SemanticEngine {
    /// Attempts to boot the semantic layer from `config`. Returns `None`
    /// (never aborts the process) both when the layer is disabled and
    /// when it's enabled but fails to initialize (missing/bad artifact,
    /// whisper load failure, ...) -- the semantic layer is a SHOULD-
    /// priority enhancement (§3 MoSCoW) and must never threaten the MUST-
    /// priority behavioral system's ability to boot and serve `/detect`
    /// (AGENTS.md rule 3). A boot-time failure is still logged loudly
    /// (never silently masked, AGENTS.md rule 4).
    pub fn boot(config: &Config) -> Option<Arc<Self>> {
        if !config.semantic_enabled {
            tracing::info!(event = "semantic_boot", enabled = false, "semantic layer disabled by config");
            return None;
        }
        match Self::try_boot(config) {
            Ok(engine) => {
                tracing::info!(event = "semantic_boot", enabled = true, ok = true, "semantic layer initialized");
                Some(Arc::new(engine))
            }
            Err(e) => {
                tracing::error!(
                    event = "incident",
                    component = "semantic_boot",
                    error = %e,
                    "semantic layer failed to initialize; continuing with semantic disabled"
                );
                None
            }
        }
    }

    fn try_boot(config: &Config) -> Result<Self> {
        let whisper_path = config
            .whisper_model_path
            .as_deref()
            .context("CONCORDE_WHISPER_MODEL_PATH not set but CONCORDE_SEMANTIC_ENABLED=true")?;
        let threads = config.whisper_threads.unwrap_or(4);
        let local_asr = asr::LocalAsr::load(whisper_path, threads)
            .with_context(|| format!("load whisper model from {whisper_path}"))?;

        let probe_path = probe_templates_path();
        let probe = probe::ProbeDetector::load(Path::new(&probe_path))
            .with_context(|| format!("load probe templates from {probe_path}"))?;

        let fusion_path =
            config.semantic_fusion_path.as_deref().unwrap_or("artifacts/semantic_fusion.json");
        let fusion = fusion::FusionParams::load(Path::new(fusion_path))
            .with_context(|| format!("load semantic fusion params from {fusion_path}"))?;

        let max_concurrent = config.asr_max_concurrent.unwrap_or(2).max(1);

        Ok(Self {
            probe: Arc::new(probe),
            transcriber: Arc::new(local_asr),
            fusion,
            semaphore: Arc::new(Semaphore::new(max_concurrent)),
        })
    }

    /// Test-only constructor: swap in a fake [`ProbeSource`] and/or
    /// [`asr::Transcriber`] (e.g. one that sleeps) so the
    /// timeout/semaphore/degradation paths can be exercised without the
    /// real template bank or ggml model file (§ T045(b)VERIFY).
    #[cfg(test)]
    pub fn new_for_test(
        probe: Arc<dyn ProbeSource>,
        transcriber: Arc<dyn asr::Transcriber>,
        fusion: fusion::FusionParams,
        max_concurrent: usize,
    ) -> Self {
        Self { probe, transcriber, fusion, semaphore: Arc::new(Semaphore::new(max_concurrent.max(1))) }
    }

    /// Stage 1: probe detection (sync, cheap) + answer-turn lookup, then
    /// SPAWN the ASR call (`spawn_blocking`) if a permit is free. Never
    /// blocks waiting for ASR itself -- the caller does other CPU-bound
    /// work (features + inference) before calling [`Self::finish`].
    /// Returns [`PendingSemantic::Unavailable`] immediately (probe_ms only,
    /// no ASR attempted) when: no probe found/confidently absent/
    /// ambiguous, no caller turn follows the probe, no ASR permit is free
    /// (`reason = "busy"`), or there's no Tokio runtime to spawn onto
    /// (`reason = "no_runtime"` -- defensive; never true when called from
    /// `/detect`'s or `/analyze`'s own async handlers).
    pub fn spawn(&self, turns: &[Turn], channel0: &[f32], channel1: &[f32], sample_rate: u32) -> PendingSemantic {
        let probe_start = Instant::now();
        let probe_match = self.probe.find_probe(channel1, turns);
        let probe_ms = elapsed_ms(probe_start);

        let Some(probe_match) = probe_match else {
            return PendingSemantic::Unavailable { reason: "no_probe", probe_detected: false, probe_ms };
        };

        let Some((_answer_gi, start, end)) = probe::find_answer_turn(turns, probe_match.turn_index) else {
            return PendingSemantic::Unavailable { reason: "no_answer_turn", probe_detected: true, probe_ms };
        };

        let permit = match Arc::clone(&self.semaphore).try_acquire_owned() {
            Ok(p) => p,
            Err(_) => return PendingSemantic::Unavailable { reason: "busy", probe_detected: true, probe_ms },
        };

        if tokio::runtime::Handle::try_current().is_err() {
            return PendingSemantic::Unavailable { reason: "no_runtime", probe_detected: true, probe_ms };
        }

        let s = (start as f64 * sample_rate as f64).round().max(0.0) as usize;
        let e = ((end as f64 * sample_rate as f64).round() as usize).min(channel0.len());
        let answer_samples: Vec<f32> = if e > s { channel0[s..e].to_vec() } else { Vec::new() };
        let prepared = asr::prepare_for_asr(&answer_samples, sample_rate);

        let transcriber = Arc::clone(&self.transcriber);
        let started_at = Instant::now();
        // The permit lives INSIDE this closure, not in `PendingAsr` -- it's
        // released only when the real transcribe() call returns, whether
        // or not the original request is still waiting on it (see
        // `PendingAsr`'s doc comment).
        let handle = tokio::task::spawn_blocking(move || {
            let _permit = permit;
            transcriber.transcribe(&prepared)
        });

        PendingSemantic::Waiting { probe_ms, pending: PendingAsr { handle, started_at } }
    }

    /// Stage 2: wait for the spawned ASR call up to `timeout_ms` (measured
    /// from when [`Self::spawn`] started it, per T045 -- the caller's own
    /// CPU-bound work in between already ate into that budget). On
    /// timeout the task is abandoned (not aborted -- it still holds its
    /// permit until it actually finishes, see `spawn`'s doc comment) and
    /// its result discarded; `p_final` degrades to `p_behavioral` exactly.
    pub async fn finish(&self, pending: PendingSemantic, timeout_ms: u64, p_behavioral: f64) -> SemanticOutcome {
        let (probe_ms, pending) = match pending {
            PendingSemantic::Unavailable { reason, probe_detected, probe_ms } => {
                return SemanticOutcome::unavailable(reason, probe_detected, probe_ms, p_behavioral);
            }
            PendingSemantic::Waiting { probe_ms, pending } => (probe_ms, pending),
        };

        let PendingAsr { handle, started_at } = pending;
        let elapsed_before_wait = elapsed_ms(started_at);
        let remaining_ms = (timeout_ms as f64 - elapsed_before_wait).max(0.0) as u64;

        let result = tokio::time::timeout(Duration::from_millis(remaining_ms), handle).await;
        let asr_ms = elapsed_ms(started_at);

        match result {
            Err(_elapsed) => {
                let mut out = SemanticOutcome::unavailable("timeout", true, probe_ms, p_behavioral);
                out.asr_ms = Some(asr_ms);
                out
            }
            Ok(Err(_join_err)) => {
                let mut out = SemanticOutcome::unavailable("error", true, probe_ms, p_behavioral);
                out.asr_ms = Some(asr_ms);
                out
            }
            Ok(Ok(Err(_transcribe_err))) => {
                let mut out = SemanticOutcome::unavailable("error", true, probe_ms, p_behavioral);
                out.asr_ms = Some(asr_ms);
                out
            }
            Ok(Ok(Ok(text))) => {
                let (answer_type, invention_score) = rules::analyze_answer(&text);
                let p_final = fusion::apply(p_behavioral, invention_score, &self.fusion);
                SemanticOutcome {
                    available: true,
                    reason: "ok",
                    probe_detected: true,
                    probe_ms,
                    asr_ms: Some(asr_ms),
                    answer_type: Some(answer_type.as_str()),
                    invention_score,
                    p_behavioral,
                    p_final,
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    /// Deterministic [`ProbeSource`] stub: always returns the same fixed
    /// result regardless of audio content, so orchestration
    /// (spawn/finish/semaphore/timeout/fusion) can be tested independently
    /// of the real DTW matching (covered directly in `probe.rs`'s own
    /// tests against the real template bank).
    struct FixedProbe(Option<probe::ProbeMatch>);

    impl ProbeSource for FixedProbe {
        fn find_probe(&self, _channel1: &[f32], _turns: &[Turn]) -> Option<probe::ProbeMatch> {
            self.0
        }
    }

    fn test_fusion() -> fusion::FusionParams {
        fusion::FusionParams { w: -0.8, max_delta_p: 0.15, whisper_model: None, version: None }
    }

    struct SleepyTranscriber {
        delay: Duration,
        text: String,
        calls: Arc<AtomicUsize>,
    }

    impl asr::Transcriber for SleepyTranscriber {
        fn transcribe(&self, _samples_16k: &[f32]) -> Result<String> {
            self.calls.fetch_add(1, Ordering::SeqCst);
            std::thread::sleep(self.delay);
            Ok(self.text.clone())
        }
    }

    /// One agent turn (index 0) followed by one caller turn (index 1) --
    /// enough for `probe::find_answer_turn` to succeed once `spawn` is
    /// told (via [`FixedProbe`]) that turn 0 is the probe. Sample buffers
    /// are silence; only their *length* matters (the turn-window slice
    /// must stay in bounds), since the mocked transcriber ignores its
    /// input entirely.
    fn call_with_probe_at_turn_zero() -> (Vec<Turn>, Vec<f32>, Vec<f32>) {
        let sr = probe::SAMPLE_RATE;
        let agent = vec![0.0f32; sr as usize * 2];
        let caller = vec![0.0f32; sr as usize * 3];
        let turns = vec![
            Turn { channel: 1, start: 0.0, end: 2.0 },
            Turn { channel: 0, start: 2.1, end: 3.0 },
        ];
        (turns, caller, agent)
    }

    fn fixed_probe_match() -> Option<probe::ProbeMatch> {
        Some(probe::ProbeMatch { turn_index: 0, start: 0.0, end: 2.0, score: 0.1 })
    }

    /// A call with no probe turn at all -> immediately unavailable, no ASR
    /// ever attempted (reason = "no_probe").
    #[tokio::test(flavor = "multi_thread")]
    async fn no_probe_turn_is_unavailable_without_spawning_asr() {
        let calls = Arc::new(AtomicUsize::new(0));
        let transcriber: Arc<dyn asr::Transcriber> =
            Arc::new(SleepyTranscriber { delay: Duration::from_millis(1), text: "hola".into(), calls: calls.clone() });
        let engine = SemanticEngine::new_for_test(Arc::new(FixedProbe(None)), transcriber, test_fusion(), 2);

        let (turns, caller, agent) = call_with_probe_at_turn_zero();
        let pending = engine.spawn(&turns, &caller, &agent, probe::SAMPLE_RATE);
        let outcome = engine.finish(pending, 1500, 0.5).await;
        assert!(!outcome.available);
        assert_eq!(outcome.reason, "no_probe");
        assert_eq!(outcome.p_final, 0.5);
        assert_eq!(calls.load(Ordering::SeqCst), 0, "ASR must never be attempted without a probe");
    }

    /// Slow ASR path: the transcriber sleeps well past the timeout ->
    /// `finish` returns within timeout + slack, `available=false`,
    /// `reason="timeout"`, and `p_final == p_behavioral` exactly.
    #[tokio::test(flavor = "multi_thread")]
    async fn slow_asr_times_out_and_degrades_to_behavioral_only() {
        let calls = Arc::new(AtomicUsize::new(0));
        let transcriber: Arc<dyn asr::Transcriber> = Arc::new(SleepyTranscriber {
            delay: Duration::from_millis(500),
            text: "me llamo Roberto".into(),
            calls: calls.clone(),
        });
        let engine =
            SemanticEngine::new_for_test(Arc::new(FixedProbe(fixed_probe_match())), transcriber, test_fusion(), 2);

        let (turns, caller, agent) = call_with_probe_at_turn_zero();

        let start = Instant::now();
        let pending = engine.spawn(&turns, &caller, &agent, probe::SAMPLE_RATE);
        let outcome = engine.finish(pending, 50, 0.7).await; // 50ms budget, ASR sleeps 500ms
        let elapsed = start.elapsed();

        assert!(!outcome.available);
        assert_eq!(outcome.reason, "timeout");
        assert_eq!(outcome.p_behavioral, 0.7);
        assert_eq!(outcome.p_final, 0.7);
        assert!(elapsed < Duration::from_millis(300), "finish() took {elapsed:?}, should return near the 50ms budget");
    }

    /// Semaphore exhausted: with 0 free permits, `spawn` returns `busy`
    /// immediately (no ASR attempted, no measurable delay).
    #[tokio::test(flavor = "multi_thread")]
    async fn semaphore_exhausted_skips_immediately() {
        let calls = Arc::new(AtomicUsize::new(0));
        let transcriber: Arc<dyn asr::Transcriber> =
            Arc::new(SleepyTranscriber { delay: Duration::from_millis(50), text: "hola".into(), calls: calls.clone() });
        let engine =
            SemanticEngine::new_for_test(Arc::new(FixedProbe(fixed_probe_match())), transcriber, test_fusion(), 1);

        let (turns, caller, agent) = call_with_probe_at_turn_zero();

        // Hold the only permit with an in-flight call.
        let first = engine.spawn(&turns, &caller, &agent, probe::SAMPLE_RATE);
        assert!(matches!(first, PendingSemantic::Waiting { .. }));

        let start = Instant::now();
        let second = engine.spawn(&turns, &caller, &agent, probe::SAMPLE_RATE);
        let spawn_elapsed = start.elapsed();
        assert!(matches!(second, PendingSemantic::Unavailable { reason: "busy", .. }));
        assert!(spawn_elapsed < Duration::from_millis(5), "busy path must be near-instant, took {spawn_elapsed:?}");

        let outcome = engine.finish(second, 1500, 0.42).await;
        assert_eq!(outcome.reason, "busy");
        assert_eq!(outcome.p_final, 0.42);

        // Drain the first call so the test doesn't leak a sleeping thread.
        let _ = engine.finish(first, 1500, 0.42).await;
    }

    /// A confidently detected probe + fast ASR -> available=true and a
    /// fused p_final that respects `max_delta_p`.
    #[tokio::test(flavor = "multi_thread")]
    async fn fast_asr_produces_a_fused_available_outcome() {
        let calls = Arc::new(AtomicUsize::new(0));
        let transcriber: Arc<dyn asr::Transcriber> = Arc::new(SleepyTranscriber {
            delay: Duration::from_millis(1),
            text: "me llamo Roberto".into(),
            calls: calls.clone(),
        });
        let engine =
            SemanticEngine::new_for_test(Arc::new(FixedProbe(fixed_probe_match())), transcriber, test_fusion(), 2);

        let (turns, caller, agent) = call_with_probe_at_turn_zero();
        let pending = engine.spawn(&turns, &caller, &agent, probe::SAMPLE_RATE);
        let outcome = engine.finish(pending, 1500, 0.5).await;

        assert!(outcome.available);
        assert_eq!(outcome.reason, "ok");
        assert_eq!(outcome.answer_type, Some("assertion_name"));
        assert_eq!(outcome.invention_score, 0.80);
        assert!((outcome.p_final - 0.5).abs() <= test_fusion().max_delta_p + 1e-9);
        assert_eq!(calls.load(Ordering::SeqCst), 1);
    }

    /// `CONCORDE_SEMANTIC_ENABLED=false` yields the exact same shape as a
    /// timed-out/unavailable call: `p_final == p_behavioral`,
    /// `available=false` -- `SemanticOutcome::disabled` and
    /// `SemanticOutcome::unavailable("timeout", ...)` must be
    /// indistinguishable to any consumer that only reads
    /// `available`/`p_final` (§ T045(b)VERIFY item 5).
    #[test]
    fn disabled_and_timed_out_outcomes_have_the_same_shape() {
        let disabled = SemanticOutcome::disabled(0.63);
        let timed_out = SemanticOutcome::unavailable("timeout", true, 1.2, 0.63);
        assert_eq!(disabled.available, timed_out.available);
        assert_eq!(disabled.p_final, timed_out.p_final);
        assert_eq!(disabled.invention_score, timed_out.invention_score);
        assert_eq!(disabled.p_behavioral, timed_out.p_behavioral);
    }
}
