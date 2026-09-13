//! `POST /detect` — the only scored artifact (§8.1, ADR-013, ADR-006).
//!
//! Handler shape: parse the request body (FR-003, T007) into a WAV payload,
//! run it through the real pipeline ([`crate::pipeline::analyze_bytes`] —
//! decode → VAD → features → ONNX, T009/T011/T014/T020), and run the whole
//! thing through [`crate::http::failsafe::run_failsafe`] so a panic, a
//! timeout, a parse failure, or a pipeline failure all become the fallback
//! verdict at HTTP 200 instead of ever surfacing as a 5xx to the judge.
//! Exactly one structured `event = "detect"` JSON log line is emitted per
//! request — success or failure — carrying request/timing/verdict metadata
//! but never audio or transcripts (NFR-008); a failure additionally gets
//! `run_failsafe`'s own `event = "incident"` line.

use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::IntoResponse;
use bytes::Bytes;
use tracing::info;

use crate::http::failsafe::{self, DetectFailure, DetectResponse, FailsafeContext};
use crate::http::parse::{self, ParsedRequest};
use crate::pipeline::{self, Analysis};
use crate::state::SharedState;

pub async fn detect(
    State(state): State<SharedState>,
    headers: HeaderMap,
    body: Bytes,
) -> impl IntoResponse {
    let ctx = FailsafeContext::new(body.len(), state.config.handler_timeout_ms, state.config.strict);
    let request_id = ctx.request_id.clone();
    let byte_size = body.len();

    failsafe::run_failsafe(ctx, async move {
        let parsed: ParsedRequest = parse::extract_audio(&headers, body)
            .await
            .map_err(|e| DetectFailure::new(e.to_string(), None))?;
        let call_id = parsed.call_id.clone();

        let analysis: Analysis = pipeline::analyze_bytes(&state, &parsed.wav)
            .map_err(|e| DetectFailure::new(e.to_string(), call_id.clone()))?;

        log_detect(&request_id, call_id.as_deref(), byte_size, &analysis);
        // Record success metrics for this detect call.
        state.metrics.record("detect", "ok", analysis.timings_ms.total, false);

        Ok(DetectResponse::new(analysis.verdict.is_synthetic, analysis.verdict.confidence))
    })
    .await
}

/// The one `event = "detect"` log line per successful request (§ "Never
/// emit an unmarked degradation" — failures instead go through
/// `run_failsafe`'s `event = "incident"` line, never both).
fn log_detect(request_id: &str, call_id: Option<&str>, byte_size: usize, analysis: &Analysis) {
    info!(
        event = "detect",
        request_id,
        call_id = call_id.unwrap_or(""),
        bytes = byte_size,
        duration_s = analysis.duration_s,
        turn_count_caller = analysis.turn_counts.caller,
        turn_count_agent = analysis.turn_counts.agent,
        decode_ms = analysis.timings_ms.decode,
        vad_ms = analysis.timings_ms.vad,
        features_ms = analysis.timings_ms.features,
        inference_ms = analysis.timings_ms.inference,
        total_ms = analysis.timings_ms.total,
        p_synthetic = analysis.verdict.p_synthetic,
        is_synthetic = analysis.verdict.is_synthetic,
        confidence = analysis.verdict.confidence,
        model_version = analysis.model_version.as_deref().unwrap_or("none"),
        "detect handler completed"
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Config;
    use crate::state::AppState;
    use axum::body::{Body, HttpBody};
    use axum::http::{Request, StatusCode};
    use axum::routing::post;
    use axum::Router;
    use std::sync::Arc;
    use tower::ServiceExt;

    fn test_state() -> SharedState {
        let mut config = Config::from_env();
        config.handler_timeout_ms = 5_000;
        config.strict = false;
        Arc::new(AppState::new(config, None))
    }

    fn app() -> Router {
        Router::new().route("/detect", post(detect)).with_state(test_state())
    }

    fn synthetic_wav() -> Vec<u8> {
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: 8_000,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut cursor = std::io::Cursor::new(Vec::new());
        {
            let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();
            for i in 0..800i16 {
                writer.write_sample(i).unwrap();
                writer.write_sample(-i).unwrap();
            }
            writer.finalize().unwrap();
        }
        cursor.into_inner()
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
    async fn valid_wav_returns_200_with_exact_key_set() {
        use base64::engine::general_purpose::STANDARD;
        use base64::Engine;

        let wav = synthetic_wav();
        let payload = serde_json::json!({
            "call_id": "abc123",
            "audio_base64": STANDARD.encode(&wav),
            "sample_rate": 8000,
            "channels": 2,
        })
        .to_string();

        let response = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/detect")
                    .header("content-type", "application/json")
                    .body(Body::from(payload))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        let obj = body.as_object().unwrap();
        assert_eq!(obj.len(), 2, "response must carry exactly {{is_synthetic, confidence}}");
        assert!(obj.contains_key("is_synthetic"));
        assert!(obj.contains_key("confidence"));
    }

    #[tokio::test]
    async fn malformed_body_still_returns_200_with_fallback() {
        let response = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/detect")
                    .header("content-type", "application/json")
                    .body(Body::from("{\"unexpected\":\"field\"}"))
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        assert_eq!(body["is_synthetic"], serde_json::json!(false));
        assert_eq!(body["confidence"], serde_json::json!(0.50));
    }

    #[tokio::test]
    async fn empty_body_still_returns_200_with_fallback() {
        let response = app()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri("/detect")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .unwrap();

        assert_eq!(response.status(), StatusCode::OK);
        let body = body_json(response).await;
        assert_eq!(body["is_synthetic"], serde_json::json!(false));
        assert_eq!(body["confidence"], serde_json::json!(0.50));
    }
}
