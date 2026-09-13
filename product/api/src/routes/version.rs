use axum::{extract::State, response::Json};
use axum::http::StatusCode;
use std::sync::Arc;

use crate::state::AppState;

pub async fn version(State(state): State<Arc<AppState>>) -> (StatusCode, Json<serde_json::Value>) {
    let build_time = option_env!("BUILD_TIME").unwrap_or("unknown");
    let rustc = option_env!("RUSTC_VERSION").unwrap_or("unknown");
    let feature_contract = option_env!("CONCORDE_FEATURE_CONTRACT").unwrap_or("fc-1");

    let model_version = state
        .model
        .as_ref()
        .map(|m| m.lock().expect("model mutex poisoned").meta.model_version.clone())
        .unwrap_or_else(|| "none".to_string());

    let body = serde_json::json!({
        "git_sha": state.git_sha,
        "build_time": build_time,
        "rustc": rustc,
        "model_version": model_version,
        "feature_contract": feature_contract,
    });
    (StatusCode::OK, Json(body))
}

