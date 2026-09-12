//! Audio-domain modules: WAV decoding (T009) and voice activity detection.
//!
//! `vad/` runs in the inference path itself (ADR-003, FR-004) — unlike the
//! practice dataset's `turns/<id>.json`, `/detect` only ever receives raw
//! WAV bytes, so the system must derive its own speech/silence segmentation
//! at request time.

pub mod vad;
