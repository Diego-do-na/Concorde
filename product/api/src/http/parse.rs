//! Request body parsing for `POST /detect` (FR-003).
//!
//! Altur's judge client (`hackmty26/scripts/check_endpoint.py`, commit
//! `429adf7`) is the canonical caller: `Content-Type: application/json` with
//! body `{"call_id": "...", "audio_base64": "<base64 of the complete WAV>",
//! "sample_rate": 8000, "channels": 2}`. That shape is tried first. The
//! remaining shapes (ADR-009) are kept as cheap, no-configuration robustness
//! against a caller that doesn't quite match, not as the primary target.
//!
//! Priority order:
//! 1. Canonical JSON — `audio_base64` (+ `call_id`/`sample_rate`/`channels`
//!    passed through for logging only; if they disagree with the decoded
//!    WAV's own header, the header wins downstream and a warning is logged
//!    here).
//! 2. JSON with any of `audio`, `wav`, `data`, `file`, `clip`, `content`.
//! 3. Raw binary WAV (`RIFF....WAVE` magic).
//! 4. Raw base64 body.
//! 5. `multipart/form-data` with a single file part.
//!
//! Base64 decoding tolerates a leading `data:...;base64,` URI prefix,
//! embedded whitespace, padded/unpadded input, and the URL-safe alphabet.
//! Nothing in this module panics: every failure is a typed [`ParseError`].

use base64::engine::general_purpose::{STANDARD, STANDARD_NO_PAD, URL_SAFE, URL_SAFE_NO_PAD};
use base64::Engine;
use bytes::Bytes;
use serde_json::Value;
use thiserror::Error;
use tracing::warn;

use axum::http::{header::CONTENT_TYPE, HeaderMap};

/// Default for `CONCORDE_MAX_BODY_BYTES` (16 MiB) — see T001 / `config.rs`
/// and `product/api/README.md` for the rationale (judge payload medians
/// ~6.2 MB, max observed ~11.7 MB).
pub const DEFAULT_MAX_BODY_BYTES: usize = 16_777_216;

/// JSON keys that may carry the base64-encoded WAV, in priority order.
/// `audio_base64` is Altur's canonical key and is always tried first.
const AUDIO_KEYS: [&str; 7] = ["audio_base64", "audio", "wav", "data", "file", "clip", "content"];

/// The result of successfully locating a WAV payload in a request.
#[derive(Debug, Clone)]
pub struct ParsedRequest {
    pub wav: Bytes,
    pub call_id: Option<String>,
    pub declared_sample_rate: Option<u32>,
    pub declared_channels: Option<u16>,
}

/// Typed parse failures. `/detect`'s handler (T008) maps every variant to
/// the fallback verdict — never a 5xx (ADR-006) — but keeps the variant
/// around for internal logging so a degradation is never unmarked (§ "Never
/// emit an unmarked degradation").
#[derive(Debug, Error)]
pub enum ParseError {
    #[error("request body of {actual} bytes exceeds the {limit} byte limit")]
    BodyTooLarge { limit: usize, actual: usize },

    #[error("request body is empty")]
    EmptyBody,

    #[error("could not decode base64 audio payload: {0}")]
    InvalidBase64(String),

    #[error("JSON body did not contain a recognized audio field")]
    UnknownJsonShape,

    #[error("multipart/form-data parsing failed: {0}")]
    Multipart(String),

    #[error("request body is not recognized JSON, WAV, base64, or multipart")]
    UnrecognizedFormat,
}

/// Extract the WAV payload (plus any declared metadata) from a `/detect`
/// request, trying each of the five tolerated shapes in priority order.
/// Reads `CONCORDE_MAX_BODY_BYTES` (default [`DEFAULT_MAX_BODY_BYTES`]) for
/// the size cap; see [`extract_audio_with_limit`] to pin the limit
/// explicitly (used by this module's own tests).
pub async fn extract_audio(headers: &HeaderMap, body: Bytes) -> Result<ParsedRequest, ParseError> {
    extract_audio_with_limit(headers, body, max_body_bytes_from_env()).await
}

fn max_body_bytes_from_env() -> usize {
    std::env::var("CONCORDE_MAX_BODY_BYTES")
        .ok()
        .and_then(|s| s.parse().ok())
        .unwrap_or(DEFAULT_MAX_BODY_BYTES)
}

/// Same as [`extract_audio`] with an explicit size cap, so callers (and
/// tests) don't have to go through the environment.
pub async fn extract_audio_with_limit(
    headers: &HeaderMap,
    body: Bytes,
    max_body_bytes: usize,
) -> Result<ParsedRequest, ParseError> {
    if body.len() > max_body_bytes {
        return Err(ParseError::BodyTooLarge { limit: max_body_bytes, actual: body.len() });
    }
    if body.is_empty() {
        return Err(ParseError::EmptyBody);
    }

    let content_type = headers.get(CONTENT_TYPE).and_then(|v| v.to_str().ok()).unwrap_or("");

    // Shape 5: multipart/form-data. Gated on the Content-Type header (a
    // multipart body wouldn't parse as JSON/WAV-magic/base64 anyway, but
    // checking the header first is cheap, unambiguous, and lets us report a
    // multipart-specific error instead of a confusing UnrecognizedFormat).
    // Only the media-type token is matched case-insensitively — the
    // boundary itself is case-sensitive and must be passed through as-is.
    if content_type.to_ascii_lowercase().starts_with("multipart/form-data") {
        return extract_from_multipart(content_type, body).await;
    }

    // Shapes 1+2: JSON body. `try_json` itself tries `audio_base64` (the
    // canonical key) before the other fallback keys.
    if let Some(result) = try_json(&body) {
        return result;
    }

    // Shape 3: raw binary WAV — magic bytes only; hound (T009's decode.rs)
    // does the real validation later in the pipeline.
    if is_wav_magic(&body) {
        return Ok(finish(body, None, None, None));
    }

    // Shape 4: raw base64 body.
    match std::str::from_utf8(&body) {
        Ok(text) => decode_tolerant_base64(text).map(|bytes| finish(Bytes::from(bytes), None, None, None)),
        Err(_) => Err(ParseError::UnrecognizedFormat),
    }
}

/// Try to parse `body` as the canonical/fallback JSON shapes. Returns
/// `None` when `body` isn't a JSON object at all (so the caller can fall
/// through to the next shape), `Some(Err(UnknownJsonShape))` when it is a
/// JSON object but carries none of the known audio keys, and
/// `Some(Ok(..))`/`Some(Err(InvalidBase64(..)))` once a key is found.
fn try_json(body: &[u8]) -> Option<Result<ParsedRequest, ParseError>> {
    let value: Value = serde_json::from_slice(body).ok()?;
    let obj = value.as_object()?;

    let call_id = obj.get("call_id").and_then(Value::as_str).map(str::to_string);
    let declared_sample_rate =
        obj.get("sample_rate").and_then(Value::as_u64).and_then(|n| u32::try_from(n).ok());
    let declared_channels =
        obj.get("channels").and_then(Value::as_u64).and_then(|n| u16::try_from(n).ok());

    for key in AUDIO_KEYS {
        if let Some(s) = obj.get(key).and_then(Value::as_str) {
            return Some(decode_tolerant_base64(s).map(|bytes| {
                finish(Bytes::from(bytes), call_id.clone(), declared_sample_rate, declared_channels)
            }));
        }
    }
    Some(Err(ParseError::UnknownJsonShape))
}

fn is_wav_magic(bytes: &[u8]) -> bool {
    bytes.len() >= 12 && &bytes[0..4] == b"RIFF" && &bytes[8..12] == b"WAVE"
}

/// Strip a `data:...;base64,` prefix if present.
fn strip_data_uri(s: &str) -> &str {
    if let Some(rest) = s.strip_prefix("data:") {
        if let Some(idx) = rest.find(',') {
            return &rest[idx + 1..];
        }
    }
    s
}

/// Decode base64 tolerating a data-URI prefix, embedded whitespace,
/// padded/unpadded input, and the URL-safe alphabet.
fn decode_tolerant_base64(raw: &str) -> Result<Vec<u8>, ParseError> {
    let stripped = strip_data_uri(raw.trim());
    let cleaned: String = stripped.chars().filter(|c| !c.is_whitespace()).collect();
    if cleaned.is_empty() {
        return Err(ParseError::EmptyBody);
    }
    for engine in [STANDARD, URL_SAFE, STANDARD_NO_PAD, URL_SAFE_NO_PAD] {
        if let Ok(bytes) = engine.decode(&cleaned) {
            return Ok(bytes);
        }
    }
    Err(ParseError::InvalidBase64(format!(
        "{} chars did not decode under any tolerated base64 alphabet/padding",
        cleaned.len()
    )))
}

/// Build the final [`ParsedRequest`], logging (never masking — §7.2) a
/// warning if a caller-declared sample rate/channel count disagrees with
/// what the WAV header itself says. The header wins downstream (T009); this
/// module only surfaces the disagreement.
fn finish(
    wav: Bytes,
    call_id: Option<String>,
    declared_sample_rate: Option<u32>,
    declared_channels: Option<u16>,
) -> ParsedRequest {
    if let Some((actual_sample_rate, actual_channels)) = peek_wav_spec(&wav) {
        if let Some(declared) = declared_sample_rate {
            if declared != actual_sample_rate {
                warn!(
                    call_id = call_id.as_deref().unwrap_or(""),
                    declared_sample_rate = declared,
                    actual_sample_rate,
                    "declared sample_rate disagrees with WAV header; header wins"
                );
            }
        }
        if let Some(declared) = declared_channels {
            if declared != actual_channels {
                warn!(
                    call_id = call_id.as_deref().unwrap_or(""),
                    declared_channels = declared,
                    actual_channels,
                    "declared channels disagrees with WAV header; header wins"
                );
            }
        }
    }
    ParsedRequest { wav, call_id, declared_sample_rate, declared_channels }
}

/// Cheaply peek the WAV header's sample rate/channel count. Returns `None`
/// on anything hound can't parse — full validation is T009's job, not
/// this module's.
fn peek_wav_spec(wav: &Bytes) -> Option<(u32, u16)> {
    hound::WavReader::new(std::io::Cursor::new(wav.as_ref()))
        .ok()
        .map(|r| {
            let spec = r.spec();
            (spec.sample_rate, spec.channels)
        })
}

/// A one-shot `Stream<Item = Result<Bytes, Infallible>>` over an
/// already-buffered body, so `multer` can be driven without pulling in a
/// full async-stream/futures dependency.
struct OnceBytes(Option<Result<Bytes, std::convert::Infallible>>);

impl futures_core::Stream for OnceBytes {
    type Item = Result<Bytes, std::convert::Infallible>;

    fn poll_next(
        self: std::pin::Pin<&mut Self>,
        _cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<Option<Self::Item>> {
        std::task::Poll::Ready(self.get_mut().0.take())
    }
}

/// Shape 5: multipart/form-data with a single file part. Metadata fields
/// (`call_id`, `sample_rate`, `channels`) are recognized by name if present;
/// the first field that isn't one of those is treated as the file part,
/// regardless of its own field name.
async fn extract_from_multipart(content_type: &str, body: Bytes) -> Result<ParsedRequest, ParseError> {
    let boundary = multer::parse_boundary(content_type).map_err(|e| ParseError::Multipart(e.to_string()))?;
    let stream = OnceBytes(Some(Ok(body)));
    let mut multipart = multer::Multipart::new(stream, boundary);

    let mut call_id = None;
    let mut declared_sample_rate = None;
    let mut declared_channels = None;
    let mut wav_bytes: Option<Bytes> = None;

    while let Some(field) =
        multipart.next_field().await.map_err(|e| ParseError::Multipart(e.to_string()))?
    {
        let name = field.name().unwrap_or("").to_string();
        match name.as_str() {
            "call_id" => call_id = field.text().await.ok(),
            "sample_rate" => {
                declared_sample_rate = field.text().await.ok().and_then(|s| s.trim().parse().ok())
            }
            "channels" => {
                declared_channels = field.text().await.ok().and_then(|s| s.trim().parse().ok())
            }
            _ if wav_bytes.is_none() => {
                wav_bytes =
                    Some(field.bytes().await.map_err(|e| ParseError::Multipart(e.to_string()))?);
            }
            // A "single file part" is expected; extra parts beyond the
            // first non-metadata one are ignored rather than erroring.
            _ => {}
        }
    }

    let raw = wav_bytes.ok_or_else(|| ParseError::Multipart("no file part found".to_string()))?;
    let wav = if is_wav_magic(&raw) {
        raw
    } else {
        match std::str::from_utf8(&raw) {
            Ok(text) => Bytes::from(decode_tolerant_base64(text)?),
            Err(_) => return Err(ParseError::UnrecognizedFormat),
        }
    };
    Ok(finish(wav, call_id, declared_sample_rate, declared_channels))
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    /// A tiny deterministic "synthetic" WAV, generated with hound rather
    /// than checked in as a fixture (no dataset/audio in the repo, NFR-011).
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

    fn headers(content_type: Option<&str>) -> HeaderMap {
        let mut h = HeaderMap::new();
        if let Some(ct) = content_type {
            h.insert(CONTENT_TYPE, HeaderValue::from_str(ct).unwrap());
        }
        h
    }

    async fn parse(headers: &HeaderMap, body: Vec<u8>) -> Result<ParsedRequest, ParseError> {
        extract_audio_with_limit(headers, Bytes::from(body), DEFAULT_MAX_BODY_BYTES).await
    }

    fn maybe_data_uri(b64: String, data_uri: bool) -> String {
        if data_uri {
            format!("data:audio/wav;base64,{b64}")
        } else {
            b64
        }
    }

    fn maybe_unpad(b64: String, padded: bool) -> String {
        if padded {
            b64
        } else {
            b64.trim_end_matches('=').to_string()
        }
    }

    fn encode_variant(wav: &[u8], padded: bool, data_uri: bool) -> String {
        let b64 = STANDARD.encode(wav);
        maybe_data_uri(maybe_unpad(b64, padded), data_uri)
    }

    #[tokio::test]
    async fn canonical_json_shape_matches_altur_client() {
        let wav = synthetic_wav();
        let body = serde_json::json!({
            "call_id": "abc123",
            "audio_base64": STANDARD.encode(&wav),
            "sample_rate": 8000,
            "channels": 2,
        })
        .to_string();
        let parsed = parse(&headers(Some("application/json")), body.into_bytes()).await.unwrap();
        assert_eq!(parsed.wav.as_ref(), wav.as_slice());
        assert_eq!(parsed.call_id, Some("abc123".to_string()));
        assert_eq!(parsed.declared_sample_rate, Some(8000));
        assert_eq!(parsed.declared_channels, Some(2));
    }

    #[tokio::test]
    async fn fallback_json_keys_x_padding_x_data_uri() {
        let wav = synthetic_wav();
        for key in ["audio", "wav", "data", "file", "clip", "content"] {
            for padded in [true, false] {
                for data_uri in [true, false] {
                    let encoded = encode_variant(&wav, padded, data_uri);
                    let body =
                        serde_json::json!({ key: encoded }).to_string();
                    let parsed = parse(&headers(Some("application/json")), body.into_bytes())
                        .await
                        .unwrap_or_else(|e| {
                            panic!("key={key} padded={padded} data_uri={data_uri}: {e}")
                        });
                    assert_eq!(
                        parsed.wav.as_ref(),
                        wav.as_slice(),
                        "key={key} padded={padded} data_uri={data_uri}"
                    );
                }
            }
        }
    }

    #[tokio::test]
    async fn raw_base64_body_x_padding_x_data_uri() {
        let wav = synthetic_wav();
        for padded in [true, false] {
            for data_uri in [true, false] {
                let encoded = encode_variant(&wav, padded, data_uri);
                let parsed = parse(&headers(None), encoded.into_bytes())
                    .await
                    .unwrap_or_else(|e| panic!("padded={padded} data_uri={data_uri}: {e}"));
                assert_eq!(parsed.wav.as_ref(), wav.as_slice());
            }
        }
    }

    #[tokio::test]
    async fn raw_binary_wav_body() {
        let wav = synthetic_wav();
        let parsed = parse(&headers(None), wav.clone()).await.unwrap();
        assert_eq!(parsed.wav.as_ref(), wav.as_slice());
    }

    #[tokio::test]
    async fn multipart_single_file_part() {
        let wav = synthetic_wav();
        let boundary = "concordeTestBoundary";
        let mut body = Vec::new();
        body.extend_from_slice(
            format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"call_id\"\r\n\r\nabc123\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(
            format!(
                "--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"call.wav\"\r\nContent-Type: audio/wav\r\n\r\n"
            )
            .as_bytes(),
        );
        body.extend_from_slice(&wav);
        body.extend_from_slice(format!("\r\n--{boundary}--\r\n").as_bytes());

        let ct = format!("multipart/form-data; boundary={boundary}");
        let parsed = parse(&headers(Some(&ct)), body).await.unwrap();
        assert_eq!(parsed.wav.as_ref(), wav.as_slice());
        assert_eq!(parsed.call_id, Some("abc123".to_string()));
    }

    #[tokio::test]
    async fn declared_sample_rate_mismatch_is_tolerated_and_logged() {
        // WAV header says 8 kHz; the request declares 16 kHz. Header wins;
        // we still parse successfully (mismatch is only logged).
        let wav = synthetic_wav();
        let body = serde_json::json!({
            "audio_base64": STANDARD.encode(&wav),
            "sample_rate": 16_000,
            "channels": 2,
        })
        .to_string();
        let parsed = parse(&headers(Some("application/json")), body.into_bytes()).await.unwrap();
        assert_eq!(parsed.wav.as_ref(), wav.as_slice());
        assert_eq!(parsed.declared_sample_rate, Some(16_000));
    }

    #[tokio::test]
    async fn oversize_body_is_rejected_before_decoding() {
        let body = vec![0u8; 17 * 1024 * 1024];
        let err = extract_audio_with_limit(&headers(None), Bytes::from(body), DEFAULT_MAX_BODY_BYTES)
            .await
            .unwrap_err();
        assert!(matches!(err, ParseError::BodyTooLarge { .. }));
    }

    #[tokio::test]
    async fn empty_body_is_rejected() {
        let err = parse(&headers(None), Vec::new()).await.unwrap_err();
        assert!(matches!(err, ParseError::EmptyBody));
    }

    #[tokio::test]
    async fn invalid_base64_is_rejected() {
        let err = parse(&headers(None), b"not-valid-base64!!!".to_vec()).await.unwrap_err();
        assert!(matches!(err, ParseError::InvalidBase64(_)));
    }

    #[tokio::test]
    async fn json_without_known_key_is_rejected() {
        let body = serde_json::json!({ "unexpected": "field" }).to_string();
        let err = parse(&headers(Some("application/json")), body.into_bytes()).await.unwrap_err();
        assert!(matches!(err, ParseError::UnknownJsonShape));
    }

    #[test]
    fn parse_error_variants_are_distinct() {
        // The four variants exercised above are meant to be distinguishable
        // by callers (e.g. for metrics/logging), not collapsed into one.
        fn discriminant_name(e: &ParseError) -> &'static str {
            match e {
                ParseError::BodyTooLarge { .. } => "BodyTooLarge",
                ParseError::EmptyBody => "EmptyBody",
                ParseError::InvalidBase64(_) => "InvalidBase64",
                ParseError::UnknownJsonShape => "UnknownJsonShape",
                ParseError::Multipart(_) => "Multipart",
                ParseError::UnrecognizedFormat => "UnrecognizedFormat",
            }
        }
        let variants = [
            ParseError::BodyTooLarge { limit: 1, actual: 2 },
            ParseError::EmptyBody,
            ParseError::InvalidBase64("x".into()),
            ParseError::UnknownJsonShape,
        ];
        let names: std::collections::HashSet<_> = variants.iter().map(discriminant_name).collect();
        assert_eq!(names.len(), variants.len());
    }
}
