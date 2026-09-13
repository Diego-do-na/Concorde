//! Behavioral feature extraction — contract fc-1 (§9).
//!
//! Mirrors `product/ml/features/extract.py` line by line; that file (not
//! this one) is the single source of truth for the fc-1 vector (AGENTS.md).
//! Any behavioral difference between the two extractors is a bug in
//! whichever one deviates from §9, not acceptable "reference vs.
//! production" drift. Silent reordering of `FC1_NAMES` is the single most
//! dangerous possible defect in this system — it produces plausible-
//! looking but wrong verdicts with no error raised. Any addition to the
//! contract creates `fc-2`, requires a new ONNX export, and requires
//! re-running the golden-vector parity test (T015).
//!
//! Notation (§9): caller = channel 0 turns, agent = channel 1 turns, each
//! a half-open interval `[start, end)` in seconds; `duration_s` = call
//! duration `D`. CV = coefficient of variation (population std / mean).

mod balance;
mod latency;
mod morphology;
mod overlap;

/// The feature-contract version this extractor implements. A model whose
/// `CONCORDE_FEATURE_CONTRACT` doesn't match this must abort startup
/// (FR-006) rather than run against a mismatched vector.
pub const FC1_VERSION: &str = "fc-1";

/// F-01…F-22 (F-21 split into two scalars) in the exact order frozen by
/// `product/artifacts/feature_contract_fc-1.json` — the single source of
/// truth for both extractors. Never reorder this array.
pub const FC1_NAMES: [&str; 23] = [
    "resp_latency_mean",
    "resp_latency_median",
    "resp_latency_std",
    "resp_latency_cv",
    "latency_monotony_index",
    "resp_latency_min",
    "overlap_count",
    "overlap_rate_per_min",
    "overlap_total_dur",
    "caller_bargein_count",
    "recovery_delay_mean",
    "recovery_delay_cv",
    "recovery_abort_ratio",
    "caller_turn_dur_mean",
    "caller_turn_dur_std",
    "caller_turn_dur_cv",
    "short_turn_ratio",
    "fragmentation_rate",
    "caller_speech_ratio",
    "speech_balance",
    "silence_break_delay_mean",
    "silence_break_delay_cv",
    "turn_count_caller",
];

pub(crate) type Interval = (f64, f64);

// §9 thresholds, shared verbatim with the Python reference extractor.
pub(crate) const MONOTONY_WINDOW_S: f64 = 0.15;
pub(crate) const ABORT_WINDOW_S: f64 = 0.4;
pub(crate) const SHORT_TURN_S: f64 = 1.0;
pub(crate) const FRAGMENTATION_GAP_S: f64 = 0.5;
pub(crate) const SILENCE_GAP_S: f64 = 2.0;

/// `(mean, median, population-std, min)` of `values`; all `0.0` if empty.
/// Population std uses `ddof=0`, matching numpy's default — the same
/// convention `product/ml/features/extract.py::_stats` uses.
pub(crate) fn stats(values: &[f64]) -> (f64, f64, f64, f64) {
    if values.is_empty() {
        return (0.0, 0.0, 0.0, 0.0);
    }
    let n = values.len() as f64;
    let mean = values.iter().sum::<f64>() / n;
    let mut sorted = values.to_vec();
    sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let median = median_of_sorted(&sorted);
    let variance = values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n;
    let std = variance.sqrt();
    let min = sorted[0];
    (mean, median, std, min)
}

fn median_of_sorted(sorted: &[f64]) -> f64 {
    let n = sorted.len();
    if n % 2 == 1 {
        sorted[n / 2]
    } else {
        (sorted[n / 2 - 1] + sorted[n / 2]) / 2.0
    }
}

/// `std / mean`, defined as `0.0` when mean is `0.0` (never NaN from 0/0).
pub(crate) fn cv(std: f64, mean: f64) -> f64 {
    if mean != 0.0 {
        std / mean
    } else {
        0.0
    }
}

/// First turn in `sorted_turns` (sorted by start) with `start >= threshold`.
pub(crate) fn first_at_or_after(sorted_turns: &[Interval], threshold: f64) -> Option<Interval> {
    sorted_turns.iter().copied().find(|t| t.0 >= threshold)
}

/// Merge overlapping/touching intervals (both channels) into busy spans.
pub(crate) fn merge_busy_intervals(intervals: &[Interval]) -> Vec<Interval> {
    if intervals.is_empty() {
        return Vec::new();
    }
    let mut ordered = intervals.to_vec();
    ordered.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let mut merged: Vec<(f64, f64)> = vec![ordered[0]];
    for &(start, end) in &ordered[1..] {
        let last = merged.last_mut().unwrap();
        if start <= last.1 {
            last.1 = last.1.max(end);
        } else {
            merged.push((start, end));
        }
    }
    merged
}

/// The fc-1 feature vector for one call, per §9, as 23 `f64`s.
///
/// `caller` / `agent` are half-open `(start, end)` intervals in seconds
/// for channel 0 / channel 1 respectively; unsorted input is accepted.
/// `duration_s` is the call duration `D`.
pub fn extract(caller: &[(f32, f32)], agent: &[(f32, f32)], duration_s: f32) -> [f64; 23] {
    let mut caller_sorted: Vec<Interval> = caller.iter().map(|&(s, e)| (s as f64, e as f64)).collect();
    let mut agent_sorted: Vec<Interval> = agent.iter().map(|&(s, e)| (s as f64, e as f64)).collect();
    caller_sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    agent_sorted.sort_by(|a, b| a.partial_cmp(b).unwrap());
    let duration_s = duration_s as f64;

    let lat = latency::compute(&caller_sorted, &agent_sorted);
    let ovl = overlap::compute(&caller_sorted, &agent_sorted, duration_s);
    let morph = morphology::compute(&caller_sorted, duration_s);
    let bal = balance::compute(&caller_sorted, &agent_sorted, duration_s);

    [
        lat[0], lat[1], lat[2], lat[3], lat[4], lat[5], ovl[0], ovl[1], ovl[2], ovl[3], ovl[4],
        ovl[5], ovl[6], morph[0], morph[1], morph[2], morph[3], morph[4], bal[0], bal[1], bal[2],
        bal[3], bal[4],
    ]
}

/// `extract()`'s output paired with its `FC1_NAMES` label, for `/analyze`
/// (ADR-013 — `/detect` never carries this).
pub fn extract_named(
    caller: &[(f32, f32)],
    agent: &[(f32, f32)],
    duration_s: f32,
) -> Vec<(&'static str, f64)> {
    let vec = extract(caller, agent, duration_s);
    FC1_NAMES.iter().copied().zip(vec).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use std::path::Path;

    // Tolerance is 1e-6, not the Python reference test's 1e-9: this
    // extractor takes f32 turn boundaries (§9's own signature, matching
    // VAD output), so f32->f64 promotion alone introduces ~1e-8 error
    // before any arithmetic runs. 1e-6 matches the golden-vector parity
    // budget (T015) and comfortably survives that rounding.
    const EPS: f64 = 1e-6;

    fn approx(actual: f64, expected: f64) {
        assert!(
            (actual - expected).abs() < EPS,
            "expected {expected}, got {actual}"
        );
    }

    fn idx(name: &str) -> usize {
        FC1_NAMES.iter().position(|&n| n == name).unwrap()
    }

    #[derive(serde::Deserialize)]
    struct Contract {
        contract: String,
        features: Vec<String>,
    }

    fn load_contract() -> Contract {
        let path =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../artifacts/feature_contract_fc-1.json");
        let data = fs::read_to_string(&path)
            .unwrap_or_else(|e| panic!("read feature contract at {}: {e}", path.display()));
        serde_json::from_str(&data).expect("parse feature contract fc-1")
    }

    #[test]
    fn fc1_names_match_contract_file() {
        let contract = load_contract();
        assert_eq!(contract.contract, FC1_VERSION);
        assert_eq!(contract.features.len(), FC1_NAMES.len());
        for (i, name) in contract.features.iter().enumerate() {
            assert_eq!(name, FC1_NAMES[i], "feature order mismatch at index {i}");
        }
    }

    // The 3 synthetic dialogues below are the same hand-computed vectors
    // as product/ml/features/tests/test_extract.py's
    // test_dialogue_{1,2,3}_* — kept in sync deliberately; if you change
    // one, change the other.

    #[test]
    fn dialogue_1_no_overlap_sequential_turns() {
        // A1(0,2) C1(2.5,4) A2(4,6) C2(6.3,7) A3(7,9) C3(9.4,11); D = 11.0
        let agent = [(0.0f32, 2.0), (4.0, 6.0), (7.0, 9.0)];
        let caller = [(2.5f32, 4.0), (6.3, 7.0), (9.4, 11.0)];
        let vec = extract(&caller, &agent, 11.0);

        approx(vec[idx("resp_latency_mean")], 0.4);
        approx(vec[idx("resp_latency_median")], 0.4);
        approx(vec[idx("resp_latency_std")], 0.0816496581);
        approx(vec[idx("resp_latency_cv")], 0.2041241452);
        approx(vec[idx("latency_monotony_index")], 1.0);
        approx(vec[idx("resp_latency_min")], 0.3);

        approx(vec[idx("overlap_count")], 0.0);
        approx(vec[idx("overlap_rate_per_min")], 0.0);
        approx(vec[idx("overlap_total_dur")], 0.0);
        approx(vec[idx("caller_bargein_count")], 0.0);
        approx(vec[idx("recovery_delay_mean")], 0.0);
        approx(vec[idx("recovery_delay_cv")], 0.0);
        approx(vec[idx("recovery_abort_ratio")], 0.0);

        approx(vec[idx("caller_turn_dur_mean")], 1.2666666667);
        approx(vec[idx("caller_turn_dur_std")], 0.4027681991);
        approx(vec[idx("caller_turn_dur_cv")], 0.3179748940);
        approx(vec[idx("short_turn_ratio")], 1.0 / 3.0);
        approx(vec[idx("fragmentation_rate")], 0.0);

        approx(vec[idx("caller_speech_ratio")], 0.3454545455);
        approx(vec[idx("speech_balance")], 0.6333333333);

        approx(vec[idx("silence_break_delay_mean")], 0.0);
        approx(vec[idx("silence_break_delay_cv")], 0.0);

        approx(vec[idx("turn_count_caller")], 3.0);
        assert!(vec.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn dialogue_2_bargein_abort_fragmentation_and_silence() {
        // A1(0,5) with a caller barge-in+abort C1(2.0,2.3) inside it, then
        // C2(5.2,5.4) short backchannel, a 3.1s mutual silence, A2(8.5,10.0),
        // C3(10.6,12.0), then fragmented C4(12.3,13.0). D=13.0
        let agent = [(0.0f32, 5.0), (8.5, 10.0)];
        let caller = [(2.0f32, 2.3), (5.2, 5.4), (10.6, 12.0), (12.3, 13.0)];
        let vec = extract(&caller, &agent, 13.0);

        approx(vec[idx("resp_latency_mean")], 0.4);
        approx(vec[idx("resp_latency_std")], 0.2);
        approx(vec[idx("resp_latency_cv")], 0.5);
        approx(vec[idx("latency_monotony_index")], 0.0);
        approx(vec[idx("resp_latency_min")], 0.2);

        approx(vec[idx("overlap_count")], 1.0);
        approx(vec[idx("overlap_rate_per_min")], 60.0 / 13.0);
        approx(vec[idx("overlap_total_dur")], 0.3);
        approx(vec[idx("caller_bargein_count")], 1.0);

        approx(vec[idx("recovery_delay_mean")], 2.9);
        approx(vec[idx("recovery_delay_cv")], 0.0);
        approx(vec[idx("recovery_abort_ratio")], 1.0);

        approx(vec[idx("caller_turn_dur_mean")], 0.65);
        approx(vec[idx("caller_turn_dur_std")], 0.4716990566);
        approx(vec[idx("caller_turn_dur_cv")], 0.7256908563);
        approx(vec[idx("short_turn_ratio")], 0.75);
        approx(vec[idx("fragmentation_rate")], 60.0 / 13.0);

        approx(vec[idx("caller_speech_ratio")], 0.2);
        approx(vec[idx("speech_balance")], 2.6 / 6.5);

        approx(vec[idx("silence_break_delay_mean")], 5.2);
        approx(vec[idx("silence_break_delay_cv")], 0.0);

        approx(vec[idx("turn_count_caller")], 4.0);
        assert!(vec.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn dialogue_3_overlap_wins_the_floor_no_next_turn() {
        // A1(0.0,1.0); caller barges in at 0.2 and keeps talking well past
        // the agent's turn end: C1(0.2,3.0), also the last/only caller turn.
        let agent = [(0.0f32, 1.0)];
        let caller = [(0.2f32, 3.0)];
        let vec = extract(&caller, &agent, 3.3);

        approx(vec[idx("resp_latency_mean")], 0.0);
        approx(vec[idx("resp_latency_std")], 0.0);
        approx(vec[idx("resp_latency_cv")], 0.0);
        approx(vec[idx("latency_monotony_index")], 0.0);
        approx(vec[idx("resp_latency_min")], 0.0);

        approx(vec[idx("overlap_count")], 1.0);
        approx(vec[idx("overlap_rate_per_min")], 60.0 / 3.3);
        approx(vec[idx("overlap_total_dur")], 0.8);
        approx(vec[idx("caller_bargein_count")], 1.0);

        approx(vec[idx("recovery_delay_mean")], 0.0);
        approx(vec[idx("recovery_delay_cv")], 0.0);
        approx(vec[idx("recovery_abort_ratio")], 0.0);

        approx(vec[idx("caller_turn_dur_mean")], 2.8);
        approx(vec[idx("caller_turn_dur_std")], 0.0);
        approx(vec[idx("caller_turn_dur_cv")], 0.0);
        approx(vec[idx("short_turn_ratio")], 0.0);
        approx(vec[idx("fragmentation_rate")], 0.0);

        approx(vec[idx("caller_speech_ratio")], 2.8 / 3.3);
        approx(vec[idx("speech_balance")], 2.8);

        approx(vec[idx("silence_break_delay_mean")], 0.0);
        approx(vec[idx("silence_break_delay_cv")], 0.0);

        approx(vec[idx("turn_count_caller")], 1.0);
        assert!(vec.iter().all(|v| v.is_finite()));
    }

    #[test]
    fn degenerate_inputs_never_nan_or_inf() {
        let cases: [(&[(f32, f32)], &[(f32, f32)], f32); 4] = [
            (&[], &[], 10.0),
            (&[], &[(0.0, 2.0), (3.0, 5.0)], 6.0),
            (&[(1.0, 2.0)], &[], 3.0),
            (&[(0.0, 0.5)], &[(0.5, 1.0)], 0.0),
        ];
        for (caller, agent, dur) in cases {
            let vec = extract(caller, agent, dur);
            assert!(
                vec.iter().all(|v| v.is_finite()),
                "non-finite feature for caller={caller:?} agent={agent:?} dur={dur}"
            );
        }
    }

    #[test]
    fn fully_empty_call_is_all_zero() {
        let vec = extract(&[], &[], 10.0);
        assert_eq!(vec, [0.0; 23]);
    }

    #[test]
    fn extract_named_pairs_values_with_fc1_names_in_order() {
        let agent = [(0.0f32, 2.0)];
        let caller = [(2.5f32, 4.0)];
        let vec = extract(&caller, &agent, 5.0);
        let named = extract_named(&caller, &agent, 5.0);
        assert_eq!(named.len(), 23);
        for (i, (name, value)) in named.iter().enumerate() {
            assert_eq!(*name, FC1_NAMES[i]);
            assert_eq!(*value, vec[i]);
        }
    }
}
