//! F-19…F-22 — channel share, mutual-silence-break delay, and caller turn
//! count (§9).
//!
//! Mutual-silence gaps are computed over the union of both channels' turns
//! merged into busy spans, including the silence before the first turn and
//! after the last turn up to `D`. Mirrors
//! `product/ml/features/extract.py::extract` line by line.

use super::{cv, first_at_or_after, merge_busy_intervals, stats, Interval, SILENCE_GAP_S};

/// Returns `[caller_speech_ratio, speech_balance,
/// silence_break_delay_mean, silence_break_delay_cv, turn_count_caller]`
/// (F-19…F-22; F-21 is split into its mean/CV pair).
pub(crate) fn compute(
    caller_sorted: &[Interval],
    agent_sorted: &[Interval],
    duration_s: f64,
) -> [f64; 5] {
    let caller_total: f64 = caller_sorted.iter().map(|&(s, e)| e - s).sum();
    let agent_total: f64 = agent_sorted.iter().map(|&(s, e)| e - s).sum();
    let f19 = if duration_s > 0.0 {
        caller_total / duration_s
    } else {
        0.0
    };
    let f20 = if agent_total > 0.0 {
        caller_total / agent_total
    } else {
        0.0
    };

    // F-21 — silence-break delay: for every mutual-silence gap > 2.0s
    // (including before the first turn and after the last, up to D), the
    // time from the start of the gap until the caller's next turn begins.
    let mut busy: Vec<Interval> = caller_sorted.to_vec();
    busy.extend_from_slice(agent_sorted);
    let busy = merge_busy_intervals(&busy);

    let mut silence_gaps: Vec<f64> = Vec::new();
    let mut cursor = 0.0_f64;
    for &(start, end) in &busy {
        if start - cursor > SILENCE_GAP_S {
            silence_gaps.push(cursor);
        }
        cursor = cursor.max(end);
    }
    if duration_s - cursor > SILENCE_GAP_S {
        silence_gaps.push(cursor);
    }

    let mut silence_delays: Vec<f64> = Vec::new();
    for gap_start in silence_gaps {
        if let Some(next) = first_at_or_after(caller_sorted, gap_start) {
            silence_delays.push(next.0 - gap_start);
        }
    }
    let (sil_mean, _sil_median, sil_std, _sil_min) = stats(&silence_delays);
    let f21b = cv(sil_std, sil_mean);

    let f22 = caller_sorted.len() as f64;

    [f19, f20, sil_mean, f21b, f22]
}
