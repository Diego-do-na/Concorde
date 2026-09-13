pub mod health;
pub mod detect;
pub mod analyze;
pub mod metrics;
pub mod version;
pub mod feed;
pub mod history;

use axum::extract::DefaultBodyLimit;
use axum::routing::{get, post};
use axum::Router;

use crate::state::SharedState;

/// Assemble the HTTP router. Each route is mounted here as its handler
/// lands — `/health` (T001), `/detect` (T008) and `/analyze` (T028) today;
/// the remaining modules above are still placeholders and stay unmounted
/// until their own task fills them in.
///
/// `DefaultBodyLimit::disable()` turns off axum's own 2 MB request-body cap
/// (which otherwise answers oversized bodies with a bare HTTP 413 before a
/// request ever reaches our handler — legitimate judge payloads run 6-12 MB
/// per `product/api/README.md`, and even a genuinely oversized body must
/// still come back as the fallback verdict at HTTP 200, never a raw 413:
/// ADR-006, FR-003). `http::parse::extract_audio` enforces
/// `CONCORDE_MAX_BODY_BYTES` itself, after buffering, exactly as T007
/// already implements and tests.
pub fn router() -> Router<SharedState> {
    Router::new()
        .route("/health", get(health::health))
        .route("/metrics", get(metrics::metrics))
        .route("/version", get(version::version))
        .route("/detect", post(detect::detect))
        .route("/analyze", post(analyze::analyze))
        .layer(DefaultBodyLimit::disable())
}
