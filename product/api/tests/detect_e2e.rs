//! SECONDARY verification for T022 (the PRIMARY check is Altur's own
//! `check_endpoint.py` against a live server — see `product/api/README.md`
//! for that command). This integration test exercises the assembled
//! router end to end against the dummy fc-1 model
//! (`tests/fixtures/dummy_fc1.onnx`), asserting the wire contract that
//! matters regardless of what the real model scores:
//! - HTTP 200 always, exact `{is_synthetic, confidence}` key set, and
//!   `confidence` in `[0, 1]` — across 20 synthetic val-shaped clips.
//! - a ~180s call decodes + VADs + extracts + infers in well under the
//!   1.3s internal budget (NFR-001: 800 + 400 + 50 ms) on a dev laptop.

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

/// A deterministic two-channel WAV with a handful of alternating turns —
/// shaped like a real val call, not a degenerate all-silence clip.
fn synthetic_call_wav(sr: u32, n_turns: usize, turn_ms: usize, gap_ms: usize) -> Vec<u8> {
    fn sine(sr: u32, freq: f32, duration_ms: usize, amp: f32) -> Vec<f32> {
        let n = (sr as usize * duration_ms) / 1000;
        (0..n)
            .map(|i| {
                let t = i as f32 / sr as f32;
                amp * (2.0 * std::f32::consts::PI * freq * t).sin()
            })
            .collect()
    }

    let mut ch0 = Vec::new(); // caller
    let mut ch1 = Vec::new(); // agent
    let gap = vec![0.0f32; (sr as usize * gap_ms) / 1000];

    for turn in 0..n_turns {
        if turn % 2 == 0 {
            ch1.extend(sine(sr, 300.0, turn_ms, 0.5));
            ch1.extend(&gap);
            ch0.extend(vec![0.0f32; (sr as usize * turn_ms) / 1000]);
            ch0.extend(&gap);
        } else {
            ch0.extend(sine(sr, 440.0, turn_ms, 0.5));
            ch0.extend(&gap);
            ch1.extend(vec![0.0f32; (sr as usize * turn_ms) / 1000]);
            ch1.extend(&gap);
        }
    }

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

#[tokio::test]
async fn twenty_val_shaped_clips_all_return_200_with_valid_confidence() {
    let app = test_app();
    for i in 0..20u32 {
        // Vary shape a bit per clip rather than sending the same bytes 20x.
        let n_turns = 3 + (i % 5) as usize;
        let turn_ms = 300 + (i as usize * 17) % 500;
        let wav = synthetic_call_wav(8_000, n_turns, turn_ms, 250);

        let (status, body) = post_wav(&app, wav).await;
        assert_eq!(status, StatusCode::OK, "clip {i}");

        let obj = body.as_object().unwrap_or_else(|| panic!("clip {i}: body not an object"));
        assert_eq!(obj.len(), 2, "clip {i}: exact key set {{is_synthetic, confidence}}");
        assert!(obj.contains_key("is_synthetic"), "clip {i}");
        let confidence = obj.get("confidence").and_then(|v| v.as_f64()).unwrap_or_else(|| {
            panic!("clip {i}: confidence missing or not a number")
        });
        assert!((0.0..=1.0).contains(&confidence), "clip {i}: confidence {confidence} out of range");
    }
}

#[tokio::test]
async fn a_180s_call_completes_well_under_the_1_3s_internal_budget() {
    let app = test_app();
    // ~180s: 300 turns of 500ms + 100ms gap = 180s.
    let wav = synthetic_call_wav(8_000, 300, 500, 100);

    let start = std::time::Instant::now();
    let (status, body) = post_wav(&app, wav).await;
    let elapsed = start.elapsed();

    assert_eq!(status, StatusCode::OK);
    assert!(body.get("confidence").is_some());
    assert!(
        elapsed.as_secs_f64() < 1.3,
        "180s clip took {:.3}s, over the 1.3s NFR-001 budget (800+400+50ms)",
        elapsed.as_secs_f64()
    );
}
