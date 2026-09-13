//! F-07…F-13 — overlap, barge-in, and recovery after overlap (§9).
//!
//! Overlap between an agent turn `a` and caller turn `c` is the standard
//! half-open interval test `a.start < c.end && c.start < a.end`; the
//! overlap span is `[max(a.start, c.start), min(a.end, c.end))`. Mirrors
//! `product/ml/features/extract.py::extract` line by line.

use super::{cv, first_at_or_after, stats, Interval, ABORT_WINDOW_S};

/// Returns `[count, rate_per_min, total_dur, bargein_count,
/// recovery_delay_mean, recovery_delay_cv, recovery_abort_ratio]`
/// (F-07…F-13).
pub(crate) fn compute(
    caller_sorted: &[Interval],
    agent_sorted: &[Interval],
    duration_s: f64,
) -> [f64; 7] {
    // (a_start, a_end, c_start, c_end, ov_start, ov_end)
    let mut overlap_events: Vec<(f64, f64, f64, f64, f64, f64)> = Vec::new();
    for &(a_start, a_end) in agent_sorted {
        for &(c_start, c_end) in caller_sorted {
            if a_start < c_end && c_start < a_end {
                let ov_start = a_start.max(c_start);
                let ov_end = a_end.min(c_end);
                overlap_events.push((a_start, a_end, c_start, c_end, ov_start, ov_end));
            }
        }
    }

    let f07 = overlap_events.len() as f64;
    let f08 = if duration_s > 0.0 {
        f07 / (duration_s / 60.0)
    } else {
        0.0
    };
    let f09: f64 = overlap_events.iter().map(|e| e.5 - e.4).sum();

    let mut f10 = 0.0;
    for &(c_start, _c_end) in caller_sorted {
        if agent_sorted
            .iter()
            .any(|&(a_start, a_end)| a_start < c_start && c_start < a_end)
        {
            f10 += 1.0;
        }
    }

    // F-11…F-13 — recovery after an overlap event.
    let mut recovery_delays: Vec<f64> = Vec::new();
    let mut abort_count = 0.0;
    for &(_a_start, _a_end, _c_start, c_end, ov_start, ov_end) in &overlap_events {
        if let Some(next) = first_at_or_after(caller_sorted, ov_end) {
            recovery_delays.push(next.0 - ov_end);
        }
        if (c_end - ov_start) <= ABORT_WINDOW_S {
            abort_count += 1.0;
        }
    }
    let (rec_mean, _rec_median, rec_std, _rec_min) = stats(&recovery_delays);
    let f11 = rec_mean;
    let f12 = cv(rec_std, f11);
    let f13 = if f07 > 0.0 { abort_count / f07 } else { 0.0 };

    [f07, f08, f09, f10, f11, f12, f13]
}
