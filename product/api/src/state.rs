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
}

impl AppState {
    pub fn new(config: Config, model: Option<SharedModel>) -> Self {
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
        }
    }

    pub fn uptime(&self) -> Duration {
        self.started_at.elapsed().unwrap_or_default()
    }
}

pub type SharedState = Arc<AppState>;

