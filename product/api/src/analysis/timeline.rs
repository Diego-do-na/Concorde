//! `/analyze`'s `timeline` field (§8.2): confidence-over-time,
//! reconstructed by re-extracting fc-1 features on the same turns
//! truncated at 16 evenly spaced points plus the untruncated end, then
//! re-running the already-loaded model on each truncated vector. This is
//! cheap — tabular inference on a tiny vector, no audio re-decode and no
//! VAD re-run — so doing it 16 extra times per `/analyze` call is
//! negligible next to the ~50ms/call ONNX budget (NFR-001).

use anyhow::{Context, Result};

use crate::audio::vad::Turn;
use crate::features;
use crate::state::SharedModel;

use super::TimelinePoint;

/// Number of interior (truncated) points; the 17th and final point is the
/// untruncated call, appended separately below.
const INTERIOR_POINTS: usize = 16;

/// `INTERIOR_POINTS` evenly spaced truncation times in `(0, duration_s)`,
/// plus `duration_s` itself as the last entry — 17 points total. The last
/// point reuses `final_confidence` (the caller's already-computed verdict)
/// rather than paying for an identical redundant re-score, which also
/// guarantees it matches `verdict.confidence` bit-for-bit.
pub(super) fn compute(
    model: &SharedModel,
    turns: &[Turn],
    duration_s: f32,
    final_confidence: f64,
) -> Result<Vec<TimelinePoint>> {
    let (caller, agent) = split(turns);

    let mut points = Vec::with_capacity(INTERIOR_POINTS + 1);
    for i in 1..=INTERIOR_POINTS {
        let t = duration_s * i as f32 / (INTERIOR_POINTS + 1) as f32;
        let caller_t = truncate(&caller, t);
        let agent_t = truncate(&agent, t);
        let feature_vec = features::extract(&caller_t, &agent_t, t);
        let confidence = {
            let mut guard = model.lock().expect("model mutex poisoned");
            guard.score(feature_vec).context("timeline re-score failed")?.confidence
        };
        points.push(TimelinePoint { t, confidence });
    }
    points.push(TimelinePoint { t: duration_s, confidence: final_confidence });
    Ok(points)
}

fn split(turns: &[Turn]) -> (Vec<(f32, f32)>, Vec<(f32, f32)>) {
    let mut caller = Vec::new();
    let mut agent = Vec::new();
    for turn in turns {
        if turn.channel == 0 {
            caller.push((turn.start, turn.end));
        } else {
            agent.push((turn.start, turn.end));
        }
    }
    (caller, agent)
}

/// Keep only turns that had already started by `t`, clipping any still in
/// progress to end at `t` — i.e. what the VAD would have seen had the call
/// ended there.
fn truncate(turns: &[(f32, f32)], t: f32) -> Vec<(f32, f32)> {
    turns.iter().filter(|&&(start, _)| start < t).map(|&(start, end)| (start, end.min(t))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn truncate_clips_in_progress_turns_and_drops_future_ones() {
        let turns = vec![(0.0, 1.0), (2.0, 5.0), (6.0, 7.0)];
        let out = truncate(&turns, 3.0);
        assert_eq!(out, vec![(0.0, 1.0), (2.0, 3.0)]);
    }

    #[test]
    fn truncate_at_zero_drops_everything() {
        let turns = vec![(0.0, 1.0), (0.5, 2.0)];
        assert!(truncate(&turns, 0.0).is_empty());
    }
}
