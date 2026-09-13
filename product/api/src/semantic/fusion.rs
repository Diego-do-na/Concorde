//! Runtime fusion of the behavioral verdict with the semantic
//! `invention_score`, from `product/artifacts/semantic_fusion.json` (T044's
//! frozen output — this module only *applies* `w`/`max_delta_p`, it never
//! re-derives them; that would need `val`, which is intocable, ADR-012).
//!
//! `logit(p_final) = logit(p_behavioral) + w * (invention_score - 0.5)`,
//! then clamped so `|p_final - p_behavioral| <= max_delta_p` — the runtime
//! rule documented in `product/ml/semantic/AB_REPORT.md`'s "(2) Runtime
//! fusion" section. `w` can be negative (it is, in the shipped artifact:
//! `-0.8`) — this module does not assume a sign.

use std::fs;
use std::path::Path;

use anyhow::{Context, Result};
use serde::Deserialize;

/// The fields this module actually needs from `semantic_fusion.json`; the
/// artifact carries additional provenance/reporting fields (AUC, Brier,
/// etc., see `AB_REPORT.md`) that are informational only and not parsed
/// here.
#[derive(Debug, Clone, Deserialize)]
pub struct FusionParams {
    pub w: f64,
    pub max_delta_p: f64,
    #[serde(default)]
    pub whisper_model: Option<String>,
    #[serde(default)]
    pub version: Option<String>,
}

impl FusionParams {
    pub fn load(path: &Path) -> Result<Self> {
        let raw = fs::read_to_string(path)
            .with_context(|| format!("read semantic fusion params {}", path.display()))?;
        serde_json::from_str(&raw).with_context(|| format!("parse semantic fusion params {}", path.display()))
    }
}

/// `logit(p) = ln(p / (1-p))`, with `p` clamped away from 0/1 first so the
/// result is always finite (an exact 0.0 or 1.0 behavioral probability is
/// possible in principle — e.g. a degenerate feature vector — and must
/// never turn fusion into `NaN`/`inf`).
fn logit(p: f64) -> f64 {
    let clamped = p.clamp(1e-6, 1.0 - 1e-6);
    (clamped / (1.0 - clamped)).ln()
}

fn sigmoid(x: f64) -> f64 {
    1.0 / (1.0 + (-x).exp())
}

/// Apply the fusion rule. Returns `p_final`, always in `[0, 1]` and always
/// within `max_delta_p` of `p_behavioral` (§ module docs).
pub fn apply(p_behavioral: f64, invention_score: f64, params: &FusionParams) -> f64 {
    let logit_final = logit(p_behavioral) + params.w * (invention_score - 0.5);
    let p_final_raw = sigmoid(logit_final);
    let delta = (p_final_raw - p_behavioral).clamp(-params.max_delta_p, params.max_delta_p);
    (p_behavioral + delta).clamp(0.0, 1.0)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params(w: f64, max_delta_p: f64) -> FusionParams {
        FusionParams { w, max_delta_p, whisper_model: None, version: None }
    }

    #[test]
    fn neutral_invention_score_is_a_no_op() {
        // invention_score == 0.5 -> the fusion term is exactly zero.
        let p = 0.73;
        let out = apply(p, 0.5, &params(-0.8, 0.15));
        assert!((out - p).abs() < 1e-9);
    }

    #[test]
    fn never_moves_further_than_max_delta_p() {
        for w in [-2.0, -0.8, 0.0, 0.8, 2.0] {
            for inv in [0.0, 0.05, 0.5, 0.9, 1.0] {
                for p in [0.01, 0.3, 0.5, 0.7, 0.99] {
                    let out = apply(p, inv, &params(w, 0.15));
                    assert!(
                        (out - p).abs() <= 0.15 + 1e-9,
                        "w={w} inv={inv} p={p} out={out}"
                    );
                    assert!((0.0..=1.0).contains(&out));
                }
            }
        }
    }

    #[test]
    fn monotonic_in_invention_score_for_fixed_sign_of_w() {
        // With w < 0 (the shipped value), p_final is non-increasing as
        // invention_score rises; with w > 0 it's non-decreasing.
        let p = 0.6;
        let neg = params(-0.8, 0.15);
        let mut prev = apply(p, 0.0, &neg);
        for inv in [0.1, 0.3, 0.5, 0.7, 0.9, 1.0] {
            let cur = apply(p, inv, &neg);
            assert!(cur <= prev + 1e-9, "expected non-increasing: prev={prev} cur={cur} inv={inv}");
            prev = cur;
        }

        let pos = params(0.8, 0.15);
        let mut prev = apply(p, 0.0, &pos);
        for inv in [0.1, 0.3, 0.5, 0.7, 0.9, 1.0] {
            let cur = apply(p, inv, &pos);
            assert!(cur >= prev - 1e-9, "expected non-decreasing: prev={prev} cur={cur} inv={inv}");
            prev = cur;
        }
    }

    #[test]
    fn zero_w_is_always_a_no_op() {
        for inv in [0.0, 0.3, 0.5, 0.8, 1.0] {
            let out = apply(0.42, inv, &params(0.0, 0.15));
            assert!((out - 0.42).abs() < 1e-9);
        }
    }

    #[test]
    fn output_always_in_unit_interval_even_at_extremes() {
        let out = apply(0.0, 1.0, &params(-5.0, 0.15));
        assert!((0.0..=1.0).contains(&out));
        let out = apply(1.0, 0.0, &params(5.0, 0.15));
        assert!((0.0..=1.0).contains(&out));
    }

    #[test]
    fn load_parses_the_shipped_artifact_shape() {
        let dir = std::env::temp_dir();
        let path = dir.join("concorde_test_semantic_fusion.json");
        std::fs::write(
            &path,
            r#"{"w": -0.8, "max_delta_p": 0.15, "whisper_model": "base", "version": "semantic-fusion-v1", "auc_val_fused": 0.94}"#,
        )
        .unwrap();
        let params = FusionParams::load(&path).unwrap();
        assert_eq!(params.w, -0.8);
        assert_eq!(params.max_delta_p, 0.15);
        assert_eq!(params.whisper_model.as_deref(), Some("base"));
        let _ = std::fs::remove_file(&path);
    }
}
