use std::process::Command;
use std::sync::{Arc, Mutex};

use axum::body::{Body, HttpBody};
use axum::http::{Request, StatusCode};
use axum::Router;
use tower::ServiceExt;

use concorde_api::inference::model::Model;
use concorde_api::routes;
use concorde_api::{AppState, Config};

fn fixtures_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn test_app() -> Router {
    let dir = fixtures_dir();
    let onnx = dir.join("dummy_fc1.onnx");
    if !onnx.exists() {
        let status = Command::new("python3")
            .arg(dir.join("make_dummy_model.py"))
            .status()
            .expect("python3 available to build the dummy model fixture");
        assert!(status.success(), "make_dummy_model.py failed");
    }
    let model = Model::load(&onnx).expect("load dummy fc-1 model");

    let mut config = Config::from_env();
    config.strict = false;
    config.handler_timeout_ms = 20_000;
    let state = Arc::new(AppState::new(config, Some(Arc::new(Mutex::new(model)))));

    routes::router().with_state(state)
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

async fn post_wav(app: &Router, wav: Vec<u8>) -> (StatusCode, serde_json::Value) {
    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/detect")
                .header("content-type", "application/octet-stream")
                .body(Body::from(wav))
                .unwrap(),
        )
        .await
        .unwrap();
    let status = response.status();
    let mut body = response.into_body();
    let mut buf = Vec::new();
    while let Some(chunk) = body.data().await {
        buf.extend_from_slice(&chunk.unwrap());
    }
    (status, serde_json::from_slice(&buf).unwrap())
}

async fn get_metrics(app: &Router) -> serde_json::Value {
    let response = app
        .clone()
        .oneshot(Request::builder().uri("/metrics").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let mut body = response.into_body();
    let mut buf = Vec::new();
    while let Some(chunk) = body.data().await {
        buf.extend_from_slice(&chunk.unwrap());
    }
    serde_json::from_slice(&buf).unwrap()
}

async fn get_version(app: &Router) -> serde_json::Value {
    let response = app
        .clone()
        .oneshot(Request::builder().uri("/version").body(Body::empty()).unwrap())
        .await
        .unwrap();
    let mut body = response.into_body();
    let mut buf = Vec::new();
    while let Some(chunk) = body.data().await {
        buf.extend_from_slice(&chunk.unwrap());
    }
    serde_json::from_slice(&buf).unwrap()
}

#[tokio::test]
async fn metrics_and_health_and_version_behave() {
    let app = test_app();

    // three successful detects
    for _ in 0..3 {
        let wav = synthetic_wav();
        let (status, _body) = post_wav(&app, wav).await;
        assert_eq!(status, StatusCode::OK);
    }

    let metrics = get_metrics(&app).await;
    // routes.detect.count == 3
    let p50 = metrics["routes"]["detect"]["p50_ms"].as_f64().unwrap_or(0.0);
    let count = metrics["routes"]["detect"]["count"].as_u64().unwrap_or(0);
    assert_eq!(count, 3);
    assert!(p50 > 0.0, "p50 should be non-zero after real runs");

    // force a fallback by sending malformed body
    let response = app
        .clone()
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

    let metrics2 = get_metrics(&app).await;
    let fallbacks = metrics2["fallbacks"].as_u64().unwrap_or(0);
    assert!(fallbacks >= 1, "fallback counter should have incremented");

    // /version keys present
    let v = get_version(&app).await;
    assert!(v.get("git_sha").is_some());
    assert!(v.get("model_version").is_some());

    // /health returns 200 even when deps marked degraded
    // set a dep to degraded via the app state
    // extract state by building a new app and mutating its AppState
    let dir = fixtures_dir();
    let onnx = dir.join("dummy_fc1.onnx");
    let model = Model::load(&onnx).unwrap();
    let mut config = Config::from_env();
    config.strict = false;
    let state = Arc::new(AppState::new(config, Some(Arc::new(Mutex::new(model)))));
    // mark tigerdata degraded
    {
        let mut d = state.deps.lock().unwrap();
        d.insert("tigerdata".to_string(), concorde_api::metrics::DepStatus::Degraded);
    }
    let app2 = routes::router().with_state(state);
    let response = app2
        .oneshot(Request::builder().uri("/health").body(Body::empty()).unwrap())
        .await
        .unwrap();
    assert_eq!(response.status(), StatusCode::OK);
}

