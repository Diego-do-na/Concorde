use std::path::Path;
use std::fs::File;
use std::io::Read;
use std::env;

use serde::Deserialize;
use anyhow::{Result, Context};

use ort::session::Session;

use crate::features::FC1_NAMES;

#[derive(Debug, Deserialize, Clone)]
pub struct Calibration {
    #[serde(rename = "type")]
    pub kind: String,
    pub a: f64,
    pub b: f64,
}

#[derive(Debug, Deserialize, Clone)]
pub struct Meta {
    pub model_version: String,
    pub feature_contract: String,
    pub calibration: Calibration,
    pub threshold: f32,
    pub git_sha: Option<String>,
    pub seed: Option<i64>,
    pub manifest_sha256: Option<String>,
    pub feature_names: Vec<String>,
    pub train_feature_means: Vec<f64>,
    pub train_feature_stds: Vec<f64>,
    pub feature_importance: Vec<f64>,
    pub direction_sign: Vec<i32>,
}

pub struct Scored {
    pub p_synthetic: f64,
    pub is_synthetic: bool,
    pub confidence: f64,
}

pub struct Model {
    pub session: Session,
    pub meta: Meta,
}

impl Model {
    /// Load an ONNX model and its sidecar <path>.meta.json, validate the
    /// feature_contract and feature_names against environment / code, and
    /// construct a single Session ready for inference.
    pub fn load(path: &Path) -> Result<Self> {
        let meta_path = path.with_extension(format!("{}.meta.json", path.extension().and_then(|s| s.to_str()).unwrap_or("")));
        // meta may alternatively be path + ".meta.json"
        let meta_path = if meta_path.exists() {
            meta_path
        } else {
            path.with_extension("onnx.meta.json")
        };

        // read meta json
        let mut f = File::open(&meta_path)
            .with_context(|| format!("open meta {}", meta_path.display()))?;
        let mut s = String::new();
        f.read_to_string(&mut s)?;
        let meta: Meta = serde_json::from_str(&s).context("parse meta.json")?;

        // validate feature_contract env
        let expected = env::var("CONCORDE_FEATURE_CONTRACT").unwrap_or_else(|_| "fc-1".to_string());
        if meta.feature_contract != expected {
            anyhow::bail!("meta.feature_contract '{}' != expected '{}'", meta.feature_contract, expected);
        }

        // validate feature names ordering exactly matches FC1_NAMES
        let expected_names: Vec<String> = FC1_NAMES.iter().map(|s| s.to_string()).collect();
        if meta.feature_names != expected_names {
            anyhow::bail!("meta.feature_names mismatch with FC1_NAMES");
        }

        // initialize ort environment (uses global init if not yet committed)
        // create session with 1 intra-thread
        let session = Session::builder()
            .map_err(|e| anyhow::anyhow!(e.to_string()))?
            .with_intra_threads(1)
            .map_err(|e| anyhow::anyhow!(e.to_string()))?
            .commit_from_file(path)
            .map_err(|e| anyhow::anyhow!(e.to_string()))?;

        Ok(Self { session, meta })
    }

    /// Score a 23-element feature vector. Runs the ONNX model, expects a
    /// single scalar output (raw logit or score), applies Platt calibration
    /// sigmoid(a * raw + b), then thresholds to produce verdict + confidence.
    pub fn score(&mut self, features: [f64; 23]) -> Result<Scored> {
        use ndarray::Array2;
        use ort::value::TensorRef;

        let vec_f32: Vec<f32> = features.iter().map(|&v| v as f32).collect();
        let arr = Array2::from_shape_vec((1, 23), vec_f32)
            .context("failed shape for input array")?;
        let inputs = ort::inputs![TensorRef::from_array_view(&arr)?];
        let outputs = self.session.run(inputs)?;
        let out = &outputs[0];
        let view = out.try_extract_array::<f32>()?;
        // Accept any output shape (`(1,)`, `(1, 1)`, ...) as long as it
        // carries exactly one scalar — the ONNX graph is trusted to emit a
        // single logit/probability per call, but its exact rank isn't part
        // of the meta contract, so don't require a particular one.
        let raw = *view
            .iter()
            .next()
            .context("model output tensor is empty")? as f64;

        // Platt sigmoid
        let a = self.meta.calibration.a;
        let b = self.meta.calibration.b;
        let p = 1.0 / (1.0 + (-(a * raw + b)).exp());

        // threshold: env override else meta
        let thr = env::var("CONCORDE_THRESHOLD").ok().and_then(|s| s.parse::<f64>().ok()).unwrap_or(self.meta.threshold as f64);
        let is_synthetic = p >= thr;
        let confidence = if is_synthetic { p } else { 1.0 - p };

        Ok(Scored { p_synthetic: p, is_synthetic, confidence })
    }
}

