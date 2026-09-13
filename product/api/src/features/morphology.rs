//! F-14…F-18 — caller turn morphology and fragmentation (§9).
//!
//! Mirrors `product/ml/features/extract.py::extract` line by line.

use super::{cv, stats, Interval, FRAGMENTATION_GAP_S, SHORT_TURN_S};

/// Returns `[dur_mean, dur_std, dur_cv, short_turn_ratio,
/// fragmentation_rate]` (F-14…F-18).
pub(crate) fn compute(caller_sorted: &[Interval], duration_s: f64) -> [f64; 5] {
    let caller_durs: Vec<f64> = caller_sorted.iter().map(|&(s, e)| e - s).collect();
    let (mean, _median, std, _min) = stats(&caller_durs);
    let f16 = cv(std, mean);
    let f17 = if caller_durs.is_empty() {
        0.0
    } else {
        caller_durs.iter().filter(|d| **d < SHORT_TURN_S).count() as f64 / caller_durs.len() as f64
    };

    let mut fragmentation_count = 0.0;
    for w in caller_sorted.windows(2) {
        let (_s0, e0) = w[0];
        let (s1, _e1) = w[1];
        if (s1 - e0) < FRAGMENTATION_GAP_S {
            fragmentation_count += 1.0;
        }
    }
    let f18 = if duration_s > 0.0 {
        fragmentation_count / (duration_s / 60.0)
    } else {
        0.0
    };

    [mean, std, f16, f17, f18]
}
