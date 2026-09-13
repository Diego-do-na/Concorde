use axum::{extract::State, http::HeaderMap, response::IntoResponse};
use axum::http::{StatusCode, header};
use std::sync::Arc;

use crate::state::AppState;

pub async fn metrics(
    State(state): State<Arc<AppState>>,
    headers: HeaderMap,
) -> axum::response::Response {
    let accept = headers
        .get(header::ACCEPT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    if accept.contains("text/plain") {
        let body = state.metrics.render_prometheus();
        (StatusCode::OK, [(header::CONTENT_TYPE, "text/plain")], body).into_response()
    } else {
        let body = state.metrics.snapshot_json();
        let s = serde_json::to_string(&body).unwrap_or_else(|_| "{}".to_string());
        (StatusCode::OK, [(header::CONTENT_TYPE, "application/json")], s).into_response()
    }
}

