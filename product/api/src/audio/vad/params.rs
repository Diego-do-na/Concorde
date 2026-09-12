//! All VAD tunables, in one place, with their defaults.
//!
//! Stage A (energy framing + adaptive threshold, T010) uses `frame_ms`,
//! `hop_ms`, `noise_floor_percentile`, and `threshold_db_above_floor`.
//! Stage B (on/off hysteresis + minimum-duration merging, T011) uses
//! `on_frames`, `off_frames`, `min_speech_ms`, and `min_gap_ms` — their
//! fields live here now so the contract is frozen in one struct from the
//! start, but T011 owns tuning their defaults and consuming them.

/// Tunable parameters for both VAD stages.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct VadParams {
    /// Analysis frame length, in milliseconds.
    pub frame_ms: u32,
    /// Hop (stride) between successive frame starts, in milliseconds.
    /// `hop_ms <= frame_ms` gives overlapping frames.
    pub hop_ms: u32,
    /// Percentile (in `[0, 1]`) of per-frame energy, over the whole
    /// channel, used as the noise-floor estimate. Low (e.g. `0.10`) so a
    /// handful of loud frames can't drag the floor up.
    pub noise_floor_percentile: f32,
    /// Fixed offset, in dB, added on top of the noise floor to get the
    /// activity threshold.
    pub threshold_db_above_floor: f32,

    /// Stage B (T011): consecutive above-threshold frames required before
    /// declaring speech onset (hysteresis, avoids single-frame flicker).
    pub on_frames: u32,
    /// Stage B (T011): consecutive below-threshold frames required before
    /// declaring speech offset.
    pub off_frames: u32,
    /// Stage B (T011): minimum duration, in milliseconds, for a segment to
    /// be kept as a speech turn.
    pub min_speech_ms: u32,
    /// Stage B (T011): minimum gap, in milliseconds, between two segments
    /// before they are merged into one turn.
    pub min_gap_ms: u32,
}

impl Default for VadParams {
    fn default() -> Self {
        Self {
            frame_ms: 20,
            hop_ms: 10,
            noise_floor_percentile: 0.10,
            threshold_db_above_floor: 6.0,
            on_frames: 3,
            off_frames: 15,
            min_speech_ms: 200,
            min_gap_ms: 250,
        }
    }
}
