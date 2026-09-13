//! F-01…F-06 — response latency (§9).
//!
//! For every agent turn `a`, the response latency is the gap to the first
//! caller turn `c` with `c.start >= a.end` — mirrors
//! `product/ml/features/extract.py::extract` line by line.

use super::{cv, first_at_or_after, stats, Interval, MONOTONY_WINDOW_S};

/// Returns `[mean, median, std, cv, monotony_index, min]` (F-01…F-06).
pub(crate) fn compute(caller_sorted: &[Interval], agent_sorted: &[Interval]) -> [f64; 6] {
    let mut latencies: Vec<f64> = Vec::new();
    for &(_a_start, a_end) in agent_sorted {
        if let Some(c) = first_at_or_after(caller_sorted, a_end) {
            latencies.push(c.0 - a_end);
        }
    }
    let (mean, median, std, min) = stats(&latencies);
    let f04 = cv(std, mean);
    let f05 = if latencies.is_empty() {
        0.0
    } else {
        latencies
            .iter()
            .filter(|x| (**x - median).abs() <= MONOTONY_WINDOW_S)
            .count() as f64
            / latencies.len() as f64
    };
    [mean, median, std, f04, f05, min]
}
