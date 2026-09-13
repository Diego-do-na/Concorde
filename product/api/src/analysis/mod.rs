//! `/analyze` response construction — the §8.2 superset payload (FR-009,
//! ADR-013). `pipeline::analyze_bytes` already computed everything
//! `/detect` needs (an [`Analysis`]); this module turns that same value
//! into the richer payload the dashboard consumes, plus the pieces
//! `/detect` never touches: a re-scored confidence timeline, ranked
//! feature contributions, and a deterministic natural-language rationale.
//!
//! `/detect` and `/analyze` share one pipeline run and diverge only in
//! serializer — exactly the ADR-013 trade-off (a frozen two-key scored
//! contract vs. total freedom to enrich the internal one). Nothing in
//! this module is ever read by `/detect`'s handler.
//!
//! `analyze.schema.json` (next to this file) is the JSON Schema for
//! [`AnalyzeResponse`]'s wire shape; `tests/analyze_e2e.rs` validates
//! against it.

mod factors;
mod timeline;
pub mod waveform;

pub use waveform::Waveform;

use std::collections::BTreeMap;

use anyhow::{Context, Result};
use serde::Serialize;

use crate::audio::vad::Turn;
use crate::inference::model::Meta;
use crate::pipeline::{Analysis, Event, EventKind};
use crate::state::SharedState;

/// The full `POST /analyze` response body (§8.2). Field order here matches
/// the spec's own example for readability; JSON objects are unordered on
/// the wire regardless.
#[derive(Debug, Serialize)]
pub struct AnalyzeResponse {
    pub verdict: Verdict,
    pub signals: Signals,
    pub degraded: Degraded,
    pub timeline: Vec<TimelinePoint>,
    pub turns: Turns,
    pub events: Vec<AnalyzeEvent>,
    pub features: BTreeMap<&'static str, f64>,
    pub top_factors: Vec<TopFactor>,
    pub rationale: String,
    pub waveform: Waveform,
    pub timings_ms: Timings,
    pub meta: AnalyzeMeta,
}

#[derive(Debug, Serialize)]
pub struct Verdict {
    pub is_synthetic: bool,
    pub confidence: f64,
    pub threshold: f64,
}

/// Per-signal sub-scores. `semantic`/`acoustic` are `null` rather than
/// `0.0` while T038/T045 haven't landed, so the dashboard can render
/// "unavailable" instead of a misleading zero (§7.2, "never emit an
/// unmarked degradation").
#[derive(Debug, Serialize)]
pub struct Signals {
    pub behavioral: f64,
    pub semantic: Option<f64>,
    pub acoustic: Option<f64>,
}

#[derive(Debug, Serialize)]
pub struct Degraded {
    pub semantic_available: bool,
    pub acoustic_available: bool,
}

#[derive(Debug, Clone, Copy, Serialize)]
pub struct TimelinePoint {
    pub t: f32,
    pub confidence: f64,
}

#[derive(Debug, Serialize)]
pub struct Turns {
    pub caller: Vec<[f32; 2]>,
    pub agent: Vec<[f32; 2]>,
}

#[derive(Debug, Serialize)]
pub struct AnalyzeEvent {
    #[serde(rename = "type")]
    pub kind: &'static str,
    pub t: f32,
    pub duration: f32,
}

#[derive(Debug, Clone, Serialize)]
pub struct TopFactor {
    pub feature: &'static str,
    pub value: f64,
    pub direction: &'static str,
    pub weight: f64,
}

#[derive(Debug, Serialize)]
pub struct Timings {
    pub decode: f64,
    pub vad: f64,
    pub features: f64,
    pub semantic: Option<f64>,
    pub inference: f64,
    pub total: f64,
}

#[derive(Debug, Serialize)]
pub struct AnalyzeMeta {
    pub model_version: String,
    pub git_sha: String,
    pub feature_contract: String,
    pub duration_s: f32,
}

/// Build the `/analyze` payload from an already-computed [`Analysis`].
/// Fails only if the model that produced `analysis` isn't reachable
/// anymore (never true in practice — `analysis` couldn't exist without a
/// loaded model), if a timeline re-score itself errors, or if waveform
/// extraction fails; `routes::analyze` turns any `Err` here into the
/// `{"error": "..."}` envelope, since this route is not scored (ADR-013).
pub fn build(state: &SharedState, analysis: &Analysis) -> Result<AnalyzeResponse> {
    let shared_model = state.model.as_ref().context("no model loaded")?;
    let meta: Meta = {
        let guard = shared_model.lock().expect("model mutex poisoned");
        guard.meta.clone()
    };

    let verdict = Verdict {
        is_synthetic: analysis.verdict.is_synthetic,
        confidence: analysis.verdict.confidence,
        threshold: effective_threshold(&meta),
    };

    let signals = Signals { behavioral: analysis.verdict.p_synthetic, semantic: None, acoustic: None };

    // Hardcoded until T038 (semantic) / T045 (acoustic) land — never
    // imputed as if real (§7.2).
    let degraded = Degraded { semantic_available: false, acoustic_available: false };

    let timeline =
        timeline::compute(shared_model, &analysis.turns, analysis.duration_s, analysis.verdict.confidence)?;

    let turns = split_turns_for_wire(&analysis.turns);
    let events = analysis.events.iter().map(analyze_event).collect();
    let features: BTreeMap<&'static str, f64> = analysis.features.iter().copied().collect();

    let top_factors = factors::top_factors(&analysis.features, &meta);
    let rationale = factors::rationale(&top_factors, &analysis.turn_counts);

    let waveform = analysis.waveform.clone();

    let timings_ms = Timings {
        decode: analysis.timings_ms.decode,
        vad: analysis.timings_ms.vad,
        features: analysis.timings_ms.features,
        semantic: None,
        inference: analysis.timings_ms.inference,
        total: analysis.timings_ms.total,
    };

    let meta_out = AnalyzeMeta {
        model_version: analysis.model_version.clone().unwrap_or_else(|| meta.model_version.clone()),
        git_sha: state.git_sha.clone(),
        feature_contract: meta.feature_contract.clone(),
        duration_s: analysis.duration_s,
    };

    Ok(AnalyzeResponse {
        verdict,
        signals,
        degraded,
        timeline,
        turns,
        events,
        features,
        top_factors,
        rationale,
        waveform,
        timings_ms,
        meta: meta_out,
    })
}

/// Same threshold-resolution rule as `inference::model::Model::score` (env
/// override, else the sidecar's own value) — duplicated rather than
/// exposed from `Model` because `score` never surfaces which threshold it
/// applied, and this module's task scope doesn't extend to `inference/`.
fn effective_threshold(meta: &Meta) -> f64 {
    std::env::var("CONCORDE_THRESHOLD").ok().and_then(|s| s.parse::<f64>().ok()).unwrap_or(meta.threshold as f64)
}

fn split_turns_for_wire(turns: &[Turn]) -> Turns {
    let mut caller = Vec::new();
    let mut agent = Vec::new();
    for turn in turns {
        let pair = [round2(turn.start), round2(turn.end)];
        if turn.channel == 0 {
            caller.push(pair);
        } else {
            agent.push(pair);
        }
    }
    Turns { caller, agent }
}

fn analyze_event(event: &Event) -> AnalyzeEvent {
    let kind = match event.kind {
        EventKind::Overlap => "overlap",
        EventKind::Interruption => "interruption",
        EventKind::Silence => "silence",
    };
    AnalyzeEvent { kind, t: round2(event.start), duration: round2(event.end - event.start) }
}

fn round2(v: f32) -> f32 {
    (v * 100.0).round() / 100.0
}
