use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime};

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
        }
    }

    pub fn uptime(&self) -> Duration {
        self.started_at.elapsed().unwrap_or_default()
    }
}

pub type SharedState = Arc<AppState>;

