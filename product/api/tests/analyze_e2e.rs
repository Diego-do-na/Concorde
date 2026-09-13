//! Integration coverage for `POST /analyze` (T028, FR-009, ADR-013).
//!
//! Runs the assembled router (same pattern as `tests/detect_e2e.rs`)
//! against the committed dummy fc-1 fixture model, over 3 synthetic
//! val-shaped clips (the real `val` split is gitignored — §13.4, NFR-011
//! — so these stand in, exactly as `detect_e2e.rs` already does). Checks
//! the full §8.2 key set is present, `timeline` has the required 17
//! points whose last entry matches `verdict.confidence` exactly,
//! `top_factors`' weights sum to 1, and `rationale` is non-empty and
//! mentions its own top feature's name — then validates the whole payload
//! against `src/analysis/analyze.schema.json`.

use std::collections::HashSet;
use std::process::Command;
use std::sync::{Arc, Mutex};

use axum::body::{Body, HttpBody};
use axum::http::{Request, StatusCode};
use axum::Router;
use serde_json::Value;
use tower::ServiceExt;

use concorde_api::inference::model::Model;
use concorde_api::routes;
use concorde_api::{AppState, Config};

fn fixtures_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn schema() -> Value {
    let path = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("src/analysis/analyze.schema.json");
    let text = std::fs::read_to_string(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    serde_json::from_str(&text).expect("analyze.schema.json is valid JSON")
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
/// shaped like a real val call, not a degenerate all-silence clip. Mirrors
/// `detect_e2e.rs`'s helper of the same shape.
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

async fn post_analyze(app: &Router, wav: Vec<u8>) -> (StatusCode, Value) {
    let response = app
        .clone()
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
    let status = response.status();
    let mut body = response.into_body();
    let mut buf = Vec::new();
    while let Some(chunk) = body.data().await {
        buf.extend_from_slice(&chunk.unwrap());
    }
    (status, serde_json::from_slice(&buf).unwrap())
}

/// A small, dependency-free JSON Schema (draft-07 subset: `type`,
/// `required`, `properties`, `additionalProperties`, `items`, `enum`,
/// `minItems`/`maxItems`, `minProperties`/`maxProperties`) validator —
/// enough for `analyze.schema.json`'s own shape, without pulling in a
/// full external schema crate for one fixed document.
fn validate(schema: &Value, instance: &Value, path: &str) {
    if let Some(expected) = schema.get("type") {
        assert!(type_matches(expected, instance), "{path}: expected type {expected}, got {instance}");
    }
    if let Some(required) = schema.get("required").and_then(Value::as_array) {
        let obj = instance.as_object().unwrap_or_else(|| panic!("{path}: expected object, got {instance}"));
        for key in required {
            let key = key.as_str().unwrap();
            assert!(obj.contains_key(key), "{path}: missing required key '{key}'");
        }
    }
    if let Some(props) = schema.get("properties").and_then(Value::as_object) {
        if let Some(obj) = instance.as_object() {
            for (key, subschema) in props {
                if let Some(value) = obj.get(key) {
                    validate(subschema, value, &format!("{path}.{key}"));
                }
            }
        }
    }
    if let Some(add) = schema.get("additionalProperties") {
        if add.is_object() {
            if let Some(obj) = instance.as_object() {
                let known: HashSet<&str> =
                    schema.get("properties").and_then(Value::as_object).map(|p| p.keys().map(String::as_str).collect()).unwrap_or_default();
                for (key, value) in obj {
                    if !known.contains(key.as_str()) {
                        validate(add, value, &format!("{path}.{key}"));
                    }
                }
            }
        }
    }
    if let Some(items_schema) = schema.get("items") {
        if let Some(arr) = instance.as_array() {
            for (i, item) in arr.iter().enumerate() {
                validate(items_schema, item, &format!("{path}[{i}]"));
            }
        }
    }
    if let Some(min_items) = schema.get("minItems").and_then(Value::as_u64) {
        let len = instance.as_array().map(|a| a.len()).unwrap_or(0);
        assert!(len as u64 >= min_items, "{path}: expected at least {min_items} items, got {len}");
    }
    if let Some(max_items) = schema.get("maxItems").and_then(Value::as_u64) {
        let len = instance.as_array().map(|a| a.len()).unwrap_or(0);
        assert!(len as u64 <= max_items, "{path}: expected at most {max_items} items, got {len}");
    }
    if let Some(min_props) = schema.get("minProperties").and_then(Value::as_u64) {
        let len = instance.as_object().map(|o| o.len()).unwrap_or(0);
        assert!(len as u64 >= min_props, "{path}: expected at least {min_props} properties, got {len}");
    }
    if let Some(max_props) = schema.get("maxProperties").and_then(Value::as_u64) {
        let len = instance.as_object().map(|o| o.len()).unwrap_or(0);
        assert!(len as u64 <= max_props, "{path}: expected at most {max_props} properties, got {len}");
    }
    if let Some(allowed) = schema.get("enum").and_then(Value::as_array) {
        assert!(allowed.contains(instance), "{path}: {instance} not in enum {allowed:?}");
    }
}

fn type_matches(expected: &Value, instance: &Value) -> bool {
    let check_one = |ty: &str| match ty {
        "object" => instance.is_object(),
        "array" => instance.is_array(),
        "string" => instance.is_string(),
        "number" => instance.is_number(),
        "boolean" => instance.is_boolean(),
        "null" => instance.is_null(),
        other => panic!("unknown schema type '{other}'"),
    };
    match expected {
        Value::String(ty) => check_one(ty),
        Value::Array(types) => types.iter().any(|t| check_one(t.as_str().unwrap())),
        _ => panic!("schema 'type' must be a string or array of strings"),
    }
}

#[tokio::test]
async fn three_val_shaped_clips_produce_a_schema_valid_superset_payload() {
    let app = test_app();
    let schema = schema();

    // 3 clips, deliberately varied shape (silence-heavy, overlap-heavy,
    // and a plain alternating call) rather than the same bytes 3x.
    let clips: [Vec<u8>; 3] = [
        synthetic_call_wav(8_000, 4, 600, 1_500), // long mutual-silence gaps
        synthetic_call_wav(8_000, 9, 250, 80),    // frequent, short, close turns (overlap-prone)
        synthetic_call_wav(8_000, 6, 900, 300),   // plain alternating call
    ];

    for (i, wav) in clips.into_iter().enumerate() {
        let (status, body) = post_analyze(&app, wav).await;
        assert_eq!(status, StatusCode::OK, "clip {i}");

        validate(&schema, &body, &format!("clip{i}"));

        let verdict_confidence = body["verdict"]["confidence"].as_f64().unwrap();
        let timeline = body["timeline"].as_array().unwrap();
        assert_eq!(timeline.len(), 17, "clip {i}: timeline must have 17 points");
        let last = timeline.last().unwrap()["confidence"].as_f64().unwrap();
        assert!(
            (last - verdict_confidence).abs() < 1e-9,
            "clip {i}: timeline's last confidence ({last}) must equal verdict.confidence ({verdict_confidence})"
        );

        let top_factors = body["top_factors"].as_array().unwrap();
        assert!(!top_factors.is_empty(), "clip {i}: top_factors must be non-empty");
        let weight_sum: f64 = top_factors.iter().map(|f| f["weight"].as_f64().unwrap()).sum();
        assert!((weight_sum - 1.0).abs() < 1e-6, "clip {i}: top_factors weights sum to {weight_sum}, want 1");

        let rationale = body["rationale"].as_str().unwrap();
        assert!(!rationale.is_empty(), "clip {i}: rationale must be non-empty");
        let top_feature = top_factors[0]["feature"].as_str().unwrap();
        assert!(
            rationale.contains(top_feature),
            "clip {i}: rationale ({rationale:?}) doesn't mention top feature '{top_feature}'"
        );

        // The /detect contract stays exactly two keys, untouched by /analyze
        // existing at all (ADR-013).
        assert!(body.get("is_synthetic").is_none(), "clip {i}: /analyze must not carry /detect's shape");
    }
}
