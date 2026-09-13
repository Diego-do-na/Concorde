//! `POST /analyze` — the internal superset payload for the dashboard
//! (§8.2, FR-009, ADR-013). This route is **not** scored: unlike
//! `/detect`, a failure here returns a `{"error": "..."}` envelope at
//! HTTP 200 instead of the two-key fallback verdict — there is no
//! external grader depending on this shape, only our own frontend.
//!
//! Shares `/detect`'s request parser (`http::parse::extract_audio`) and
//! pipeline (`pipeline::analyze_bytes`), and the same panic/timeout
//! safety net in spirit: a panic or an overrun both fall back to the
//! error envelope rather than ever taking the process down or hanging the
//! caller. `http::failsafe` itself always serializes to `DetectResponse`,
//! so this handler rolls its own equivalent using `tokio::spawn` (whose
//! `JoinHandle` reports a panic as an `Err` rather than unwinding through
//! the caller) plus `tokio::time::timeout`, reusing `/detect`'s own
//! `CONCORDE_HANDLER_TIMEOUT_MS` budget.

use std::time::Duration;

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use bytes::Bytes;
use tracing::error;

use crate::analysis;
use crate::http::parse;
use crate::pipeline;
use crate::state::SharedState;

pub async fn analyze(State(state): State<SharedState>, headers: HeaderMap, body: Bytes) -> impl IntoResponse {
    let byte_size = body.len();
    let timeout_ms = state.config.handler_timeout_ms;

    let task = tokio::spawn(process(state, headers, body));

    match tokio::time::timeout(Duration::from_millis(timeout_ms), task).await {
        Ok(Ok(Ok(value))) => (StatusCode::OK, Json(value)).into_response(),
        Ok(Ok(Err(reason))) => error_envelope(byte_size, &reason),
        Ok(Err(join_err)) => {
            let reason = if join_err.is_panic() { "analyze handler panicked" } else { "analyze handler cancelled" };
            error_envelope(byte_size, reason)
        }
        Err(_elapsed) => error_envelope(byte_size, &format!("handler exceeded {timeout_ms}ms")),
    }
}

/// Parse -> pipeline -> build the payload -> serialize, all fallible steps
/// collapsed to `Result<Value, String>` so the handler above has one
/// uniform failure path. Serialization happens in here (not after the
/// `match` above) so a value that somehow can't serialize (e.g. a non-
/// finite float slipping through) is caught as a normal failure too,
/// rather than risking a panic from an infallible-looking `Json` wrapper
/// downstream.
async fn process(state: SharedState, headers: HeaderMap, body: Bytes) -> Result<serde_json::Value, String> {
    let parsed = parse::extract_audio(&headers, body).await.map_err(|e| e.to_string())?;
    let analysis = pipeline::analyze_bytes(&state, &parsed.wav).map_err(|e| e.to_string())?;
    // Publish to feed for dashboard real-time updates (T027)
    let _ = state.feed.push(parsed.call_id.clone(), &analysis);
    let payload = analysis::build(&state, &analysis).map_err(|e| e.to_string())?;
    serde_json::to_value(&payload).map_err(|e| e.to_string())
}

fn error_envelope(byte_size: usize, reason: &str) -> Response {
    error!(event = "incident", route = "analyze", byte_size, reason, "analyze handler failed");
    (StatusCode::OK, Json(serde_json::json!({ "error": reason }))).into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::inference::model::Model;
    use crate::state::AppState;
    use axum::body::{Body, HttpBody};
    use axum::http::Request;
    use axum::routing::post;
    use axum::Router;
    use std::process::Command;
    use std::sync::{Arc, Mutex};
    use tower::ServiceExt;

    fn fixtures_dir() -> std::path::PathBuf {
        std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
    }

    fn test_state() -> SharedState {
        let dir = fixtures_dir();
        let onnx = dir.join("dummy_fc1.onnx");
        if !onnx.exists() {
            let status =
                Command::new("python3").arg(dir.join("make_dummy_model.py")).status().expect("python3 available");
            assert!(status.success());
        }
        let model = Model::load(&onnx).expect("load dummy model");
        let mut config = Config::from_env();
        config.strict = false;
        config.handler_timeout_ms = 5_000;
        Arc::new(AppState::new(config, Some(Arc::new(Mutex::new(model)))))
    }

    fn app() -> Router {
        Router::new().route("/analyze", post(analyze)).with_state(test_state())
    }

    fn stereo_wav(ch0: &[f32], ch1: &[f32], sr: u32) -> Vec<u8> {
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: sr,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut cursor = std::io::Cursor::new(Vec::new());
        let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();
        let len = ch0.len().max(ch1.len());
        for i in 0..len {
            let a = *ch0.get(i).unwrap_or(&0.0);
            let b = *ch1.get(i).unwrap_or(&0.0);
            writer.write_sample((a.clamp(-1.0, 1.0) * i16::MAX as f32) as i16).unwrap();
            writer.write_sample((b.clamp(-1.0, 1.0) * i16::MAX as f32) as i16).unwrap();
        }
        writer.finalize().unwrap();
        cursor.into_inner()
    }

    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n).map(|i| amp * (2.0 * std::f32::consts::PI * freq * i as f32 / sr as f32).sin()).collect()
    }

    async fn body_json(response: axum::response::Response) -> serde_json::Value {
        let mut body = response.into_body();
        let mut buf = Vec::new();
        while let Some(chunk) = body.data().await {
            buf.extend_from_slice(&chunk.unwrap());
        }
        serde_json::from_slice(&buf).unwrap()
    }

    #[tokio::test]
    async fn valid_wav_returns_the_full_superset_payload() {
        let sr = 8_000u32;
        let ch0 = sine(sr, 440.0, 300, 0.5);
        let ch1 = sine(sr, 300.0, 300, 0.5);
        let wav = stereo_wav(&ch0, &ch1, sr);

        let response = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/analyze")
                    .header("content-type", "application/octet-stream")
                    .body(Body::from(wav))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        for key in
            ["verdict", "signals", "degraded", "timeline", "turns", "events", "features", "top_factors", "rationale", "timings_ms", "meta"]
        {
            assert!(body.get(key).is_some(), "missing key {key}");
        }
        assert_eq!(body["timeline"].as_array().unwrap().len(), 17);
    }

    #[tokio::test]
    async fn malformed_body_returns_200_with_error_envelope_not_the_detect_fallback() {
        let response = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/analyze")
                    .header("content-type", "application/json")
                    .body(Body::from("{\"unexpected\":\"field\"}"))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        assert!(body.get("error").is_some());
        assert!(body.get("is_synthetic").is_none(), "/analyze must never emit /detect's shape");
    }

    #[tokio::test]
    async fn empty_body_returns_200_with_error_envelope() {
        let response =
            app().oneshot(Request::builder().method("POST").uri("/analyze").body(Body::empty()).unwrap()).await.unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        assert!(body.get("error").is_some());
    }
}
