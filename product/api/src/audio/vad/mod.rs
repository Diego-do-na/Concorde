//! Voice activity detection, run in the inference path (ADR-003, FR-004).
//!
//! Stage A (this module's `energy`/`params`, T010) turns a decoded channel
//! into per-frame energy and a raw (unsmoothed) activity mask. Stage B
//! (T011) applies on/off hysteresis and minimum-duration merging on top of
//! that mask to produce final speech turns.

pub mod energy;
pub mod params;

pub use energy::{adaptive_threshold, frame_rms_db, raw_activity};
pub use params::VadParams;
