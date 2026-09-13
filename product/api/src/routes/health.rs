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

    // Read dependency registry without performing network I/O.
    let deps_map = {
        let deps = state.deps.lock().unwrap();
        let mut m = serde_json::Map::new();
        let tiger = deps.get("tigerdata").copied().unwrap_or(crate::metrics::DepStatus::Disabled);
        let gemini = deps.get("gemini").copied().unwrap_or(crate::metrics::DepStatus::Disabled);
        m.insert("tigerdata".to_string(), serde_json::Value::String(tiger.to_string()));
        m.insert("gemini".to_string(), serde_json::Value::String(gemini.to_string()));
        serde_json::Value::Object(m)
    };

    let body = json!({
        "status": "ok",
        "model_version": model_version,
        "git_sha": state.git_sha,
        "uptime_s": uptime,
        "deps": deps_map
    });
    (StatusCode::OK, Json(body))
}

