use std::sync::Arc;
use std::time::{Duration, SystemTime};

use crate::config::Config;

#[derive(Clone)]
pub struct AppState {
    pub config: Config,
    pub started_at: SystemTime,
    pub git_sha: String,
    pub model: Option<()>,
}

impl AppState {
    pub fn new(config: Config) -> Self {
        let git_sha = std::env::var("GIT_SHA")
            .ok()
            .or_else(|| option_env!("GIT_SHA").map(|s| s.to_string()))
            .unwrap_or_else(|| "unknown".to_string());

        Self {
            config,
            started_at: SystemTime::now(),
            git_sha,
            model: None,
        }
    }

    pub fn uptime(&self) -> Duration {
        self.started_at.elapsed().unwrap_or_default()
    }
}

pub type SharedState = Arc<AppState>;

