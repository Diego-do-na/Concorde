use axum::{extract::State, http::StatusCode, response::Json};
use serde_json::json;
use std::sync::Arc;

use crate::state::AppState;

pub async fn health(State(state): State<Arc<AppState>>) -> (StatusCode, Json<serde_json::Value>) {
    let uptime = state.uptime().as_secs();
    let model_version = state
        .model
        .as_ref()
        .map(|m| m.lock().expect("model mutex poisoned").meta.model_version.clone())
        .unwrap_or_else(|| "none".to_string());

    let body = json!({
        "status": "ok",
        "model_version": model_version,
        "git_sha": state.git_sha,
        "uptime_s": uptime,
        "deps": {}
    });
    (StatusCode::OK, Json(body))
}

