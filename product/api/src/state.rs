use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};
use std::collections::HashMap;
use crate::metrics::DepStatus;

use crate::config::Config;
use crate::inference::model::Model;

/// `Model::score` takes `&mut self` (ort's `Session::run` requires unique
/// access), so concurrent `/detect` handlers share one model behind a
/// `Mutex` rather than each getting their own `Arc<Model>`. Scoring is a
/// single small matmul (microseconds), so the lock is never held across an
/// `.await` and contention is a non-issue.
pub type SharedModel = Arc<Mutex<Model>>;

#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub started_at: SystemTime,
    pub git_sha: String,
    pub model: Option<SharedModel>,
    /// Dependency status registry: other modules update these statuses;
    /// `/health` reads them without performing network I/O.
    pub deps: Arc<Mutex<HashMap<String, DepStatus>>>,
    /// Metrics registry (process-wide); initialized once and used by
    /// pipeline/routes to record and expose metrics.
    pub metrics: crate::metrics::Metrics,
    /// Live feed for recent verdicts and websocket subscriptions.
    pub feed: std::sync::Arc<crate::feed::Feed>,
    /// The live local semantic layer (T045, ADR-008): probe detector +
    /// whisper-rs ASR + fusion params, booted once at process start
    /// (`main.rs`, alongside the ONNX model) -- `None` when
    /// `CONCORDE_SEMANTIC_ENABLED=false` or boot failed (never blocks
    /// server startup, see `semantic::SemanticEngine::boot`'s docs).
    pub semantic: Option<Arc<crate::semantic::SemanticEngine>>,
}

impl AppState {
    pub fn new(config: Config, model: Option<SharedModel>) -> Self {
        Self::new_with_semantic(config, model, None)
    }

    /// `main.rs` uses this to pass in the eagerly-booted semantic engine;
    /// `new` (used throughout the existing test suite) keeps its old
    /// two-argument shape and always starts with semantic disabled, since
    /// none of those call sites need it.
    pub fn new_with_semantic(
        config: Config,
        model: Option<SharedModel>,
        semantic: Option<Arc<crate::semantic::SemanticEngine>>,
    ) -> Self {
        let git_sha = std::env::var("GIT_SHA")
            .ok()
            .or_else(|| option_env!("GIT_SHA").map(|s| s.to_string()))
            .unwrap_or_else(|| "unknown".to_string());

        Self {
            config,
            started_at: SystemTime::now(),
            git_sha,
            model,
            deps: Arc::new(Mutex::new(HashMap::new())),
            metrics: crate::metrics::GLOBAL_METRICS.clone(),
            feed: crate::feed::Feed::new(500, 50, 32),
            semantic,
        }
    }

    pub fn uptime(&self) -> Duration {
        self.started_at.elapsed().unwrap_or_default()
    }
}

pub type SharedState = Arc<AppState>;

