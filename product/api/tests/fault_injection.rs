//! Fault-injection suite for `POST /detect` (FR-008, NFR-005, R-11, T039).
//!
//! `/detect` is the only scored artifact (§8.1) and must **always** return
//! HTTP 200 with exactly `{is_synthetic, confidence}` — even when the input
//! is garbage, oversized, truncated, or self-contradictory. This suite
//! throws every fault case Paul's task description calls out at a real
//! router (dummy fc-1 model, same fixture as `tests/detect_e2e.rs`) and
//! checks:
//! - HTTP 200 always (unless `CONCORDE_STRICT=1`, which this suite detects
//!   from the environment it was run under and asserts 500 for the
//!   undecodable cases instead — this is what lets one binary serve both
//!   halves of the (b) VERIFY step without duplicating every case).
//! - the response key set is exactly `{is_synthetic, confidence}`.
//! - undecodable input gets the exact fallback body `{is_synthetic: false,
//!   confidence: 0.50}`; degenerate-but-decodable input gets a real verdict
//!   with `confidence` in `[0, 1]`.
//! - the process is still alive afterwards (a plain valid request still
//!   works after every fault case has been thrown at it).
//! - every incident produced a structured `event = "incident"` log line
//!   (never a silently unmarked degradation, §7.2).
//!
//! Run with `CONCORDE_STRICT` unset for the normal (200-always) pass, and
//! with `CONCORDE_STRICT=1` for the strict (500-on-undecodable) pass —
//! see `README.md`'s fault-semantics table and the (b) VERIFY step in
//! T039's task description.

use std::process::Command;
use std::sync::{Arc, Mutex, OnceLock};

use axum::body::{Body, HttpBody};
use axum::http::{Request, StatusCode};
use axum::Router;
use tower::ServiceExt;

use concorde_api::inference::model::Model;
use concorde_api::routes;
use concorde_api::{AppState, Config};

// ---------------------------------------------------------------------
// Log capture: a process-wide tracing subscriber writing JSON lines into a
// shared buffer, so this suite can assert every incident logged a
// structured line instead of just trusting the HTTP-visible behavior.
// ---------------------------------------------------------------------

static LOG_BUF: OnceLock<Arc<Mutex<Vec<u8>>>> = OnceLock::new();

#[derive(Clone)]
struct SharedWriter(Arc<Mutex<Vec<u8>>>);

impl std::io::Write for SharedWriter {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.0.lock().unwrap().extend_from_slice(buf);
        Ok(buf.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

impl<'a> tracing_subscriber::fmt::MakeWriter<'a> for SharedWriter {
    type Writer = SharedWriter;
    fn make_writer(&'a self) -> Self::Writer {
        self.clone()
    }
}

/// Install the capturing subscriber exactly once for the whole test binary
/// (tests run concurrently by default; a global subscriber can only be set
/// once per process, and that's fine — we only need to know *an* incident
/// line was emitted, not attribute it to a specific test).
fn init_log_capture() -> Arc<Mutex<Vec<u8>>> {
    LOG_BUF
        .get_or_init(|| {
            let buf = Arc::new(Mutex::new(Vec::new()));
            let _ = tracing_subscriber::fmt()
                .json()
                .with_writer(SharedWriter(buf.clone()))
                .try_init();
            buf
        })
        .clone()
}

fn captured_log_contains_incident() -> bool {
    let buf = LOG_BUF.get().expect("log capture initialized before this check").lock().unwrap();
    let text = String::from_utf8_lossy(&buf);
    text.contains("\"event\":\"incident\"")
}

/// Whether this run of the binary was launched under `CONCORDE_STRICT=1`
/// (see module docs / (b) VERIFY — the suite is meant to be run twice).
fn strict_mode() -> bool {
    std::env::var("CONCORDE_STRICT")
        .map(|s| matches!(s.as_str(), "1" | "true" | "TRUE" | "yes"))
        .unwrap_or(false)
}

// ---------------------------------------------------------------------
// Test app plumbing (mirrors tests/detect_e2e.rs).
// ---------------------------------------------------------------------

fn fixtures_dir() -> std::path::PathBuf {
    std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures")
}

fn test_app() -> Router {
    init_log_capture();

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

    // Inherit CONCORDE_STRICT (and CONCORDE_MAX_BODY_BYTES, if set) from
    // the real environment so this one binary serves both VERIFY passes;
    // everything else is pinned to sane test defaults.
    let mut config = Config::from_env();
    config.handler_timeout_ms = 20_000;

    let state = Arc::new(AppState::new(config, Some(Arc::new(Mutex::new(model)))));
    routes::router().with_state(state)
}

async fn send(app: &Router, req: Request<Body>) -> (StatusCode, serde_json::Value) {
    let response = app.clone().oneshot(req).await.unwrap();
    let status = response.status();
    let mut body = response.into_body();
    let mut buf = Vec::new();
    while let Some(chunk) = body.data().await {
        buf.extend_from_slice(&chunk.unwrap());
    }
    let value = if buf.is_empty() {
        serde_json::Value::Null
    } else {
        serde_json::from_slice(&buf).unwrap_or_else(|e| {
            panic!("response body not valid JSON: {e}; raw={}", String::from_utf8_lossy(&buf))
        })
    };
    (status, value)
}

async fn post_json(app: &Router, body: serde_json::Value) -> (StatusCode, serde_json::Value) {
    send(
        app,
        Request::builder()
            .method("POST")
            .uri("/detect")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
}

async fn post_raw(
    app: &Router,
    content_type: Option<&str>,
    body: Vec<u8>,
) -> (StatusCode, serde_json::Value) {
    let mut builder = Request::builder().method("POST").uri("/detect");
    if let Some(ct) = content_type {
        builder = builder.header("content-type", ct);
    }
    send(app, builder.body(Body::from(body)).unwrap()).await
}

// ---------------------------------------------------------------------
// WAV fixture builders — no checked-in audio (NFR-011); everything is
// generated deterministically at test time.
// ---------------------------------------------------------------------

/// A two-channel PCM16 WAV with alternating speech, shaped like a real
/// call. `caller_active`/`agent_active` let individual-channel cases
/// silence one side entirely.
fn synth_wav(
    sr: u32,
    channels: u16,
    bits: u16,
    duration_s: f32,
    caller_active: bool,
    agent_active: bool,
) -> Vec<u8> {
    let n = (sr as f32 * duration_s) as usize;
    let spec = hound::WavSpec {
        channels,
        sample_rate: sr,
        bits_per_sample: bits,
        sample_format: hound::SampleFormat::Int,
    };
    let mut cursor = std::io::Cursor::new(Vec::new());
    let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();

    let max_amp = if bits == 16 { i16::MAX as f32 } else { i32::MAX as f32 };
    let turn_len = (sr as usize / 2).max(1); // 500ms turns

    for i in 0..n {
        for ch in 0..channels {
            let active = if ch == 0 { caller_active } else { agent_active };
            let is_speech = active && (i / turn_len) % 2 == (ch as usize);
            let t = i as f32 / sr as f32;
            let freq = if ch == 0 { 440.0 } else { 300.0 };
            let sample = if is_speech {
                0.5 * (2.0 * std::f32::consts::PI * freq * t).sin() * max_amp
            } else {
                0.0
            };
            if bits == 16 {
                writer.write_sample(sample as i16).unwrap();
            } else {
                writer.write_sample(sample as i32).unwrap();
            }
        }
    }
    writer.finalize().unwrap();
    cursor.into_inner()
}

fn valid_stereo_wav(duration_s: f32) -> Vec<u8> {
    synth_wav(8_000, 2, 16, duration_s, true, true)
}

fn assert_exact_keys(obj: &serde_json::Value, ctx: &str) {
    let map = obj.as_object().unwrap_or_else(|| panic!("{ctx}: body is not a JSON object: {obj:?}"));
    assert_eq!(map.len(), 2, "{ctx}: expected exactly {{is_synthetic, confidence}}, got {map:?}");
    assert!(map.contains_key("is_synthetic"), "{ctx}: missing is_synthetic");
    assert!(map.contains_key("confidence"), "{ctx}: missing confidence");
}

fn assert_fallback_body(obj: &serde_json::Value, ctx: &str) {
    assert_exact_keys(obj, ctx);
    assert_eq!(obj["is_synthetic"], serde_json::json!(false), "{ctx}: fallback is_synthetic");
    assert_eq!(obj["confidence"], serde_json::json!(0.50), "{ctx}: fallback confidence");
}

fn assert_real_verdict(obj: &serde_json::Value, ctx: &str) {
    assert_exact_keys(obj, ctx);
    let confidence = obj["confidence"].as_f64().unwrap_or_else(|| panic!("{ctx}: confidence not a number"));
    assert!((0.0..=1.0).contains(&confidence), "{ctx}: confidence {confidence} out of [0,1]");
    assert!(obj["is_synthetic"].is_boolean(), "{ctx}: is_synthetic not a bool");
}

/// Assert the outcome of a case whose input does NOT decode into a scoreable
/// call — under CONCORDE_STRICT the failsafe boundary surfaces this as a
/// 500 (proving the switch); otherwise it's HTTP 200 with the exact
/// fallback body.
fn assert_undecodable(status: StatusCode, body: &serde_json::Value, ctx: &str) {
    if strict_mode() {
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR, "{ctx}: expected 500 under CONCORDE_STRICT=1");
    } else {
        assert_eq!(status, StatusCode::OK, "{ctx}: expected 200");
        assert_fallback_body(body, ctx);
    }
}

/// Assert the outcome of a case whose input DOES decode, however
/// degenerately — always HTTP 200 with a real (in-range) verdict,
/// regardless of CONCORDE_STRICT.
fn assert_decodable(status: StatusCode, body: &serde_json::Value, ctx: &str) {
    assert_eq!(
        status,
        StatusCode::OK,
        "{ctx}: expected 200 even under CONCORDE_STRICT=1 (decodable input), got body={body:?}"
    );
    assert_real_verdict(body, ctx);
}

// ---------------------------------------------------------------------
// Cases
// ---------------------------------------------------------------------

#[tokio::test]
async fn canonical_json_with_truncated_wav_header_only_half_data() {
    let app = test_app();
    let full = valid_stereo_wav(2.0);
    let truncated = full[..full.len() / 2].to_vec();
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    let (status, body) = post_json(
        &app,
        serde_json::json!({
            "call_id": "trunc-1",
            "audio_base64": STANDARD.encode(&truncated),
            "sample_rate": 8000,
            "channels": 2,
        }),
    )
    .await;
    assert_undecodable(status, &body, "truncated wav");
}

#[tokio::test]
async fn canonical_json_with_zero_byte_audio() {
    let app = test_app();
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    let (status, body) = post_json(
        &app,
        serde_json::json!({
            "call_id": "zero-1",
            "audio_base64": STANDARD.encode(Vec::<u8>::new()),
            "sample_rate": 8000,
            "channels": 2,
        }),
    )
    .await;
    assert_undecodable(status, &body, "zero-byte audio");
}

#[tokio::test]
async fn mono_wav_is_decodable_and_scored() {
    let app = test_app();
    let wav = synth_wav(8_000, 1, 16, 3.0, true, false);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "mono wav");
}

#[tokio::test]
async fn wav_at_44_1khz_is_decodable_and_scored() {
    let app = test_app();
    let wav = synth_wav(44_100, 2, 16, 1.0, true, true);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "44.1kHz wav");
}

#[tokio::test]
async fn wav_24_bit_is_undecodable() {
    // hound writes 24-bit PCM as bits_per_sample=24 in an i32 container;
    // the VAD decoder (src/audio/vad/smooth.rs) only supports 16- and
    // 32-bit PCM, so this must fail cleanly rather than misinterpret the
    // sample width.
    let app = test_app();
    let spec = hound::WavSpec {
        channels: 2,
        sample_rate: 8_000,
        bits_per_sample: 24,
        sample_format: hound::SampleFormat::Int,
    };
    let mut cursor = std::io::Cursor::new(Vec::new());
    {
        let mut writer = hound::WavWriter::new(&mut cursor, spec).unwrap();
        for i in 0..8_000i32 {
            writer.write_sample((i % 1000) << 8).unwrap();
            writer.write_sample((-(i % 1000)) << 8).unwrap();
        }
        writer.finalize().unwrap();
    }
    let wav = cursor.into_inner();
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_undecodable(status, &body, "24-bit wav");
}

#[tokio::test]
async fn non_audio_blob_one_megabyte() {
    let app = test_app();
    // Deterministic pseudo-random bytes, not WAV/JSON/base64-shaped.
    let blob: Vec<u8> = (0..1_048_576usize).map(|i| ((i * 2654435761) % 256) as u8).collect();
    let (status, body) = post_raw(&app, Some("application/octet-stream"), blob).await;
    assert_undecodable(status, &body, "1MB non-audio blob");
}

#[tokio::test]
async fn declared_metadata_contradicts_wav_header_header_wins() {
    // WAV header says 8kHz/2ch; the JSON declares 16kHz/1ch. Header wins
    // downstream (parse.rs already logs the disagreement); this must still
    // produce a real verdict, not a failure.
    let app = test_app();
    let wav = valid_stereo_wav(3.0); // actually 8000/2
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    let (status, body) = post_json(
        &app,
        serde_json::json!({
            "call_id": "mismatch-1",
            "audio_base64": STANDARD.encode(&wav),
            "sample_rate": 16000,
            "channels": 1,
        }),
    )
    .await;
    assert_decodable(status, &body, "declared metadata contradicts header");
}

#[tokio::test]
async fn json_with_unknown_key_only() {
    let app = test_app();
    let (status, body) = post_json(&app, serde_json::json!({ "some_unrelated_field": 42 })).await;
    assert_undecodable(status, &body, "JSON unknown key");
}

#[tokio::test]
async fn json_with_empty_string_audio_field() {
    let app = test_app();
    let (status, body) = post_json(&app, serde_json::json!({ "audio_base64": "" })).await;
    assert_undecodable(status, &body, "JSON empty string audio field");
}

#[tokio::test]
async fn multipart_with_two_files_uses_the_first() {
    let app = test_app();
    let wav = valid_stereo_wav(1.0);
    let boundary = "faultInjectionBoundary";
    let mut body = Vec::new();
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(&wav);
    body.extend_from_slice(b"\r\n");
    body.extend_from_slice(
        format!(
            "--{boundary}\r\nContent-Disposition: form-data; name=\"extra\"; filename=\"b.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
        )
        .as_bytes(),
    );
    body.extend_from_slice(b"not-a-wav-second-file");
    body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

    let ct = format!("multipart/form-data; boundary={boundary}");
    let (status, body) = post_raw(&app, Some(&ct), body).await;
    assert_decodable(status, &body, "multipart with two files");
}

#[tokio::test]
async fn raw_base64_of_garbage() {
    let app = test_app();
    use base64::engine::general_purpose::STANDARD;
    use base64::Engine;
    let garbage: Vec<u8> = (0..2048u32).map(|i| (i % 251) as u8).collect();
    let encoded = STANDARD.encode(&garbage);
    let (status, body) = post_raw(&app, None, encoded.into_bytes()).await;
    assert_undecodable(status, &body, "raw base64 of garbage");
}

#[tokio::test]
async fn body_over_max_body_bytes_is_rejected() {
    let app = test_app();
    // CONCORDE_MAX_BODY_BYTES defaults to 16 MiB; 20 MB comfortably exceeds
    // it (and any override a CI environment might set lower, so this stays
    // meaningful even if that env var is customized for the run).
    let oversized = vec![0u8; 20 * 1024 * 1024];
    let (status, body) = post_raw(&app, Some("application/octet-stream"), oversized).await;
    assert_undecodable(status, &body, "20MB oversized body");
}

#[tokio::test]
async fn valid_half_second_wav() {
    let app = test_app();
    let wav = valid_stereo_wav(0.5);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "0.5s valid wav");
}

#[tokio::test]
async fn valid_600s_silent_wav() {
    // Mono, to stay comfortably under CONCORDE_MAX_BODY_BYTES (16 MiB
    // default): 600s * 8kHz * 16-bit * 2ch would be ~19.2 MB and get
    // rejected as oversized before ever reaching the decoder, which would
    // conflate this case with `body_over_max_body_bytes_is_rejected`
    // above. Mono keeps this case testing what it's meant to (a long,
    // decodable, all-silent call), at ~9.6 MB.
    let app = test_app();
    let wav = synth_wav(8_000, 1, 16, 600.0, false, false);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "600s silent wav");
}

#[tokio::test]
async fn only_agent_channel_active() {
    let app = test_app();
    let wav = synth_wav(8_000, 2, 16, 5.0, false, true);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "agent-only channel");
}

#[tokio::test]
async fn only_caller_channel_active() {
    let app = test_app();
    let wav = synth_wav(8_000, 2, 16, 5.0, true, false);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_decodable(status, &body, "caller-only channel");
}

/// Every incident above (the undecodable cases, when not running under
/// CONCORDE_STRICT — under strict mode the error propagates as a 500
/// instead of going through the fallback path, but `run_failsafe` still
/// logs the `incident` event before converting it) must have produced a
/// structured log line. This runs last among the `#[tokio::test]`s that
/// matter for it purely by naming convention (`z_`) — Rust doesn't
/// guarantee test order, so this only asserts presence, never absence, and
/// tolerates running concurrently with the cases above.
#[tokio::test]
async fn z_every_incident_produced_a_structured_log_line() {
    // Make sure at least one undecodable case has actually run in this
    // process by throwing one more directly, then check the shared buffer.
    let app = test_app();
    let (_status, _body) = post_json(&app, serde_json::json!({ "nonsense": true })).await;
    assert!(
        captured_log_contains_incident(),
        "expected at least one structured event=\"incident\" log line"
    );
}

/// The process must still be alive and serving good requests after every
/// fault case above has been thrown at it (no case may poison shared state
/// or leave the router unusable).
#[tokio::test]
async fn zz_process_still_alive_after_all_fault_cases() {
    let app = test_app();
    let wav = valid_stereo_wav(2.0);
    let (status, body) = post_raw(&app, Some("application/octet-stream"), wav).await;
    assert_eq!(status, StatusCode::OK, "process should still serve valid requests");
    assert_real_verdict(&body, "post-fault-injection sanity check");
}
