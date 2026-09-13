//! Top contributing factors and the deterministic rationale sentence for
//! `/analyze` (FR-009). Both are derived from the same per-call feature
//! vector and the model's meta sidecar (`train_feature_means/_stds`,
//! `feature_importance`, `direction_sign`) — no extra inference needed.

use super::TopFactor;
use crate::inference::model::Meta;
use crate::pipeline::TurnCounts;

/// §8.2's `top_factors`: at most 5 entries.
const TOP_N: usize = 5;
/// The rationale is built from only the top 3 (task description).
const RATIONALE_N: usize = 3;

/// Rank all fc-1 features by `|importance_i * z_i|`, where `z_i` is the
/// feature's z-score against the model's own training distribution, and
/// keep the top 5. `weight` is renormalised so the returned slice always
/// sums to 1 — including the degenerate all-zero-importance case (e.g.
/// the dummy test fixture), where every candidate ties at 0 and the top 5
/// simply split the weight evenly rather than dividing by zero.
pub(super) fn top_factors(features: &[(&'static str, f64)], meta: &Meta) -> Vec<TopFactor> {
    // (original index, name, raw value, importance-weighted z-score used
    // for ranking/weight, direction_sign-weighted z-score used only to
    // pick "synthetic" vs "human" below).
    let mut scored: Vec<(usize, &'static str, f64, f64, f64)> = features
        .iter()
        .enumerate()
        .map(|(i, &(name, x))| {
            let mean = meta.train_feature_means.get(i).copied().unwrap_or(0.0);
            let std = meta.train_feature_stds.get(i).copied().unwrap_or(1.0);
            let importance = meta.feature_importance.get(i).copied().unwrap_or(0.0);
            let direction_sign = meta.direction_sign.get(i).copied().unwrap_or(1) as f64;
            let z = if std.abs() > 1e-12 { (x - mean) / std } else { 0.0 };
            (i, name, x, importance * z, direction_sign * z)
        })
        .collect();

    // Descending |score|, ties broken by original fc-1 index for a
    // deterministic order (this ranking is a display artifact, not part
    // of the frozen feature contract).
    scored.sort_by(|a, b| b.3.abs().partial_cmp(&a.3.abs()).unwrap().then(a.0.cmp(&b.0)));
    scored.truncate(TOP_N.min(scored.len()));

    let total: f64 = scored.iter().map(|&(_, _, _, score, _)| score.abs()).sum();
    let uniform = 1.0 / scored.len().max(1) as f64;

    scored
        .into_iter()
        .map(|(_, name, x, score, effective)| TopFactor {
            feature: name,
            value: x,
            direction: if effective >= 0.0 { "synthetic" } else { "human" },
            weight: if total > 1e-12 { score.abs() / total } else { uniform },
        })
        .collect()
}

/// One sentence per top-3 factor, e.g. "Response latency stayed within CV
/// 0.09 across 12 agent turns (resp_latency_cv, synthetic signal, weight
/// 41%)." Every sentence names its own raw fc-1 feature identifier, so the
/// rationale always mentions `top_factors[0].feature` verbatim regardless
/// of which feature ranks first for a given call.
pub(super) fn rationale(top_factors: &[TopFactor], turn_counts: &TurnCounts) -> String {
    top_factors.iter().take(RATIONALE_N).map(|f| describe(f, turn_counts)).collect::<Vec<_>>().join(" ")
}

fn describe(factor: &TopFactor, turn_counts: &TurnCounts) -> String {
    format!(
        "{} ({}, {} signal, weight {:.0}%).",
        phrase(factor.feature, factor.value, turn_counts),
        factor.feature,
        factor.direction,
        factor.weight * 100.0
    )
}

/// Human-readable phrasing per fc-1 feature (§9). Covers all 23 names
/// explicitly; the wildcard arm is defensive only (unreachable while the
/// contract stays fc-1) and still names the feature verbatim.
fn phrase(feature: &str, v: f64, turn_counts: &TurnCounts) -> String {
    match feature {
        "resp_latency_mean" => format!("Mean response latency was {v:.2}s"),
        "resp_latency_median" => format!("Median response latency was {v:.2}s"),
        "resp_latency_std" => format!("Response latency varied by {v:.2}s (std)"),
        "resp_latency_cv" => {
            format!("Response latency stayed within CV {v:.2} across {} agent turns", turn_counts.agent)
        }
        "latency_monotony_index" => {
            format!("{:.0}% of response latencies landed within ±0.15s of the median", v * 100.0)
        }
        "resp_latency_min" => format!("The fastest response latency was {v:.2}s"),
        "overlap_count" => format!("{v:.0} caller/agent overlaps were detected"),
        "overlap_rate_per_min" => format!("Overlaps occurred at {v:.2} per minute"),
        "overlap_total_dur" => format!("Overlapping speech totalled {v:.2}s"),
        "caller_bargein_count" => format!("The caller barged in on the agent {v:.0} time(s)"),
        "recovery_delay_mean" => format!("After an overlap, the caller resumed after {v:.2}s on average"),
        "recovery_delay_cv" => format!("Recovery delay after interruption had CV {v:.2}"),
        "recovery_abort_ratio" => {
            format!("The caller yielded the floor within 0.4s in {:.0}% of overlaps", v * 100.0)
        }
        "caller_turn_dur_mean" => format!("Caller turns averaged {v:.2}s"),
        "caller_turn_dur_std" => format!("Caller turn duration varied by {v:.2}s (std)"),
        "caller_turn_dur_cv" => format!("Caller turn duration had CV {v:.2}"),
        "short_turn_ratio" => format!("{:.0}% of caller turns were short backchannels (<1.0s)", v * 100.0),
        "fragmentation_rate" => format!("Caller turns fragmented at {v:.2} per minute"),
        "caller_speech_ratio" => format!("The caller held the floor for {:.0}% of the call", v * 100.0),
        "speech_balance" => format!("Caller/agent speech balance was {v:.2}"),
        "silence_break_delay_mean" => {
            format!("After a mutual silence, the caller spoke after {v:.2}s on average")
        }
        "silence_break_delay_cv" => format!("Silence-break delay had CV {v:.2}"),
        "turn_count_caller" => format!("The caller produced {v:.0} turns over the call"),
        other => format!("{other} measured {v:.3}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::features::FC1_NAMES;
    use crate::inference::model::Calibration;

    fn dummy_meta(importance: Vec<f64>, means: Vec<f64>, stds: Vec<f64>, signs: Vec<i32>) -> Meta {
        Meta {
            model_version: "test".into(),
            feature_contract: "fc-1".into(),
            calibration: Calibration { kind: "platt".into(), a: 1.0, b: 0.0 },
            threshold: 0.5,
            git_sha: None,
            seed: None,
            manifest_sha256: None,
            feature_names: FC1_NAMES.iter().map(|s| s.to_string()).collect(),
            train_feature_means: means,
            train_feature_stds: stds,
            feature_importance: importance,
            direction_sign: signs,
        }
    }

    #[test]
    fn top5_and_weights_sum_to_one_even_with_all_zero_importance() {
        let features: Vec<(&'static str, f64)> = FC1_NAMES.iter().map(|&n| (n, 0.0)).collect();
        let meta = dummy_meta(vec![0.0; 23], vec![0.0; 23], vec![1.0; 23], vec![1; 23]);
        let top = top_factors(&features, &meta);
        assert_eq!(top.len(), 5);
        let total: f64 = top.iter().map(|f| f.weight).sum();
        assert!((total - 1.0).abs() < 1e-9, "total={total}");
    }

    #[test]
    fn dominant_feature_ranks_first_and_weights_still_sum_to_one() {
        let mut features: Vec<(&'static str, f64)> = FC1_NAMES.iter().map(|&n| (n, 0.0)).collect();
        features[3].1 = 5.0; // resp_latency_cv
        let mut importance = vec![0.0; 23];
        importance[3] = 1.0;
        let meta = dummy_meta(importance, vec![0.0; 23], vec![1.0; 23], vec![1; 23]);

        let top = top_factors(&features, &meta);
        assert_eq!(top[0].feature, "resp_latency_cv");
        assert_eq!(top[0].direction, "synthetic");
        let total: f64 = top.iter().map(|f| f.weight).sum();
        assert!((total - 1.0).abs() < 1e-9, "total={total}");
    }

    #[test]
    fn rationale_is_nonempty_and_mentions_top_feature_name() {
        let mut features: Vec<(&'static str, f64)> = FC1_NAMES.iter().map(|&n| (n, 0.0)).collect();
        features[3].1 = 5.0;
        let mut importance = vec![0.0; 23];
        importance[3] = 1.0;
        let meta = dummy_meta(importance, vec![0.0; 23], vec![1.0; 23], vec![1; 23]);

        let top = top_factors(&features, &meta);
        let turn_counts = TurnCounts { caller: 5, agent: 5 };
        let text = rationale(&top, &turn_counts);
        assert!(!text.is_empty());
        assert!(text.contains(top[0].feature));
    }

    #[test]
    fn every_fc1_feature_has_a_distinct_phrase_mentioning_its_own_name() {
        let turn_counts = TurnCounts { caller: 1, agent: 1 };
        for &name in FC1_NAMES.iter() {
            let text = phrase(name, 1.2345, &turn_counts);
            assert!(!text.is_empty(), "{name}: empty phrase");
            // The wildcard arm names the feature itself; every named arm's
            // wording is feature-specific but `describe()` (not `phrase`)
            // is what appends the parenthetical `(name, ...)` — assert
            // that composition here instead.
            let described = describe(
                &TopFactor { feature: name, value: 1.2345, direction: "synthetic", weight: 1.0 },
                &turn_counts,
            );
            assert!(described.contains(name), "{name}: rationale sentence doesn't mention its own name");
        }
    }
}
