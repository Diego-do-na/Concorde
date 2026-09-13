//! Fail-safe handler boundary for `POST /detect` (ADR-006, NFR-005).
//!
//! `/detect` is the only scored artifact (§8.1) and must never return
//! anything but HTTP 200 with `{"is_synthetic": <bool>, "confidence":
//! <float in [0,1]>}` — no matter what goes wrong inside the handler.
//! [`run_failsafe`] is the boundary that guarantees this: it wraps a
//! handler future with a panic catch (`catch_unwind` +
//! `AssertUnwindSafe`, since the future may not itself be `UnwindSafe`)
//! and a `CONCORDE_HANDLER_TIMEOUT_MS` timeout (default 20 s — the judge
//! allows 30 s per call including network, so this keeps a margin while
//! never leaving the judge waiting on us). A panic, a timeout, or the
//! future's own reported error are all converted into the fallback verdict
//! `{"is_synthetic": false, "confidence": 0.50}` at HTTP 200, and a
//! structured `incident` event is always logged internally — a
//! degradation is never silently unmarked (§7.2).
//!
//! `CONCORDE_STRICT=1` (dev only) propagates the failure as HTTP 500
//! instead of masking it behind the fallback body, so a developer sees it
//! immediately; production never sets this.

use std::future::Future;
use std::panic::AssertUnwindSafe;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde::Serialize;
use tracing::error;

/// The wire body of `POST /detect` (§8.1). Exactly two fields, in this
/// order — `serde` serializes a struct in field-declaration order, and
/// there is no way to attach extra keys by accident.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct DetectResponse {
    pub is_synthetic: bool,
    pub confidence: f64,
}

impl DetectResponse {
    /// Clamp `confidence` to `[0, 1]` and round to 4 decimals before
    /// building the response — every producer of a verdict (placeholder
    /// today, the real model later) goes through this constructor so the
    /// contract holds regardless of what upstream computed.
    pub fn new(is_synthetic: bool, confidence: f64) -> Self {
        let clamped = confidence.clamp(0.0, 1.0);
        let rounded = (clamped * 10_000.0).round() / 10_000.0;
        Self { is_synthetic, confidence: rounded }
    }
}

/// The fallback verdict (ADR-006): `is_synthetic: false, confidence: 0.50`.
pub fn fallback() -> DetectResponse {
    DetectResponse::new(false, 0.50)
}

/// Reported by the wrapped future when it fails on its own terms (as
/// opposed to panicking or timing out, which `run_failsafe` catches from
/// the outside and has no way to attribute to a `call_id`).
#[derive(Debug)]
pub struct DetectFailure {
    pub reason: String,
    pub call_id: Option<String>,
}

impl DetectFailure {
    pub fn new(reason: impl Into<String>, call_id: Option<String>) -> Self {
        Self { reason: reason.into(), call_id }
    }
}

/// Everything `run_failsafe` needs to log and time-box one `/detect` call.
pub struct FailsafeContext {
    pub request_id: String,
    pub byte_size: usize,
    pub timeout_ms: u64,
    pub strict: bool,
}

impl FailsafeContext {
    pub fn new(byte_size: usize, timeout_ms: u64, strict: bool) -> Self {
        Self { request_id: next_request_id(), byte_size, timeout_ms, strict }
    }
}

static REQUEST_COUNTER: AtomicU64 = AtomicU64::new(0);

/// A cheap, dependency-free request id for incident correlation: wall-clock
/// nanos plus a per-process counter (uniqueness, not randomness, is all
/// that's needed here — this never leaves the process).
fn next_request_id() -> String {
    let n = REQUEST_COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    format!("req-{nanos:x}-{n}")
}

/// Run `fut` behind the fail-safe boundary described at the module level.
/// `fut` resolves to `Ok(DetectResponse)` on success or `Err(DetectFailure)`
/// when it fails on its own terms; a panic or a timeout are caught here
/// regardless of what `fut` itself does.
pub async fn run_failsafe<F>(ctx: FailsafeContext, fut: F) -> Response
where
    F: Future<Output = Result<DetectResponse, DetectFailure>> + Send + 'static,
{
    let guarded = CatchUnwind { inner: Box::pin(fut) };
    match tokio::time::timeout(Duration::from_millis(ctx.timeout_ms), guarded).await {
        Ok(Ok(Ok(response))) => (StatusCode::OK, Json(response)).into_response(),
        Ok(Ok(Err(failure))) => {
            incident_response(&ctx, "error", &failure.reason, failure.call_id)
        }
        Ok(Err(panic_payload)) => {
            incident_response(&ctx, "panic", &panic_message(&panic_payload), None)
        }
        Err(_elapsed) => incident_response(
            &ctx,
            "timeout",
            &format!("handler exceeded {}ms", ctx.timeout_ms),
            None,
        ),
    }
}

/// Log the `incident` event and build the response: the fallback verdict at
/// HTTP 200 normally, or HTTP 500 with the failure detail when
/// `CONCORDE_STRICT=1` (dev only — see module docs).
fn incident_response(
    ctx: &FailsafeContext,
    reason: &str,
    detail: &str,
    call_id: Option<String>,
) -> Response {
    error!(
        event = "incident",
        request_id = %ctx.request_id,
        call_id = call_id.as_deref().unwrap_or(""),
        byte_size = ctx.byte_size,
        reason,
        detail,
        "detect handler failed; falling back to default verdict"
    );
    if ctx.strict {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({ "reason": reason, "detail": detail })),
        )
            .into_response()
    } else {
        // Record fallback in global metrics so /metrics reflects incidents
        // even when the handler didn't reach the normal success path.
        crate::metrics::GLOBAL_METRICS.record("detect", "fallback", 0.0, true);
        (StatusCode::OK, Json(fallback())).into_response()
    }
}

fn panic_message(payload: &Box<dyn std::any::Any + Send>) -> String {
    if let Some(s) = payload.downcast_ref::<&str>() {
        (*s).to_string()
    } else if let Some(s) = payload.downcast_ref::<String>() {
        s.clone()
    } else {
        "non-string panic payload".to_string()
    }
}

/// A `Future` adapter that catches a panic raised during any single `poll`
/// call and reports it as `Err` instead of unwinding through the executor.
/// `inner` is boxed so `CatchUnwind<T>` is `Unpin` regardless of the wrapped
/// future, which is what lets `poll` below use `Pin::get_mut` without any
/// `unsafe`.
struct CatchUnwind<T> {
    inner: Pin<Box<dyn Future<Output = T> + Send>>,
}

impl<T> Future for CatchUnwind<T> {
    type Output = std::thread::Result<T>;

    fn poll(self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Self::Output> {
        let this = self.get_mut();
        let inner = &mut this.inner;
        match std::panic::catch_unwind(AssertUnwindSafe(|| inner.as_mut().poll(cx))) {
            Ok(Poll::Ready(v)) => Poll::Ready(Ok(v)),
            Ok(Poll::Pending) => Poll::Pending,
            Err(payload) => Poll::Ready(Err(payload)),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::body::{Body, HttpBody};
    use axum::http::Request;
    use axum::routing::get;
    use axum::Router;
    use tower::ServiceExt;

    fn test_ctx(timeout_ms: u64, strict: bool) -> FailsafeContext {
        FailsafeContext::new(0, timeout_ms, strict)
    }

    async fn call(router: Router) -> (StatusCode, serde_json::Value) {
        let response = router
            .oneshot(Request::builder().uri("/t").body(Body::empty()).unwrap())
            .await
            .unwrap();
        let status = response.status();
        let mut body = response.into_body();
        let mut buf = Vec::new();
        while let Some(chunk) = body.data().await {
            buf.extend_from_slice(&chunk.unwrap());
        }
        let body: serde_json::Value = serde_json::from_slice(&buf).unwrap();
        (status, body)
    }

    #[tokio::test]
    async fn success_returns_200_with_exact_keys() {
        let router = Router::new().route(
            "/t",
            get(|| async {
                run_failsafe(test_ctx(1_000, false), async {
                    Ok(DetectResponse::new(true, 0.9123456))
                })
                .await
            }),
        );
        let (status, body) = call(router).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body.as_object().unwrap().len(), 2);
        assert_eq!(body["is_synthetic"], serde_json::json!(true));
        // Rounded to 4 decimals.
        assert_eq!(body["confidence"], serde_json::json!(0.9123));
    }

    #[tokio::test]
    async fn panicking_handler_returns_200_and_fallback() {
        let router = Router::new().route(
            "/t",
            get(|| async {
                run_failsafe(test_ctx(1_000, false), async {
                    let _: Result<DetectResponse, DetectFailure> = if true {
                        panic!("boom")
                    } else {
                        Ok(fallback())
                    };
                    unreachable!()
                })
                .await
            }),
        );
        let (status, body) = call(router).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["is_synthetic"], serde_json::json!(false));
        assert_eq!(body["confidence"], serde_json::json!(0.50));
    }

    #[tokio::test]
    async fn panicking_handler_under_strict_returns_500() {
        let router = Router::new().route(
            "/t",
            get(|| async {
                run_failsafe(test_ctx(1_000, true), async {
                    let _: Result<DetectResponse, DetectFailure> = if true {
                        panic!("boom")
                    } else {
                        Ok(fallback())
                    };
                    unreachable!()
                })
                .await
            }),
        );
        let (status, _body) = call(router).await;
        assert_eq!(status, StatusCode::INTERNAL_SERVER_ERROR);
    }

    #[tokio::test]
    async fn slow_handler_past_timeout_returns_200_and_fallback() {
        let router = Router::new().route(
            "/t",
            get(|| async {
                run_failsafe(test_ctx(10, false), async {
                    tokio::time::sleep(Duration::from_millis(200)).await;
                    Ok(DetectResponse::new(true, 0.99))
                })
                .await
            }),
        );
        let (status, body) = call(router).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["is_synthetic"], serde_json::json!(false));
        assert_eq!(body["confidence"], serde_json::json!(0.50));
    }

    #[tokio::test]
    async fn reported_error_returns_200_and_fallback() {
        let router = Router::new().route(
            "/t",
            get(|| async {
                run_failsafe(test_ctx(1_000, false), async {
                    Err(DetectFailure::new("parse failed", None))
                })
                .await
            }),
        );
        let (status, body) = call(router).await;
        assert_eq!(status, StatusCode::OK);
        assert_eq!(body["is_synthetic"], serde_json::json!(false));
        assert_eq!(body["confidence"], serde_json::json!(0.50));
    }

    #[test]
    fn confidence_is_clamped_and_rounded() {
        assert_eq!(DetectResponse::new(true, 1.5).confidence, 1.0);
        assert_eq!(DetectResponse::new(true, -0.2).confidence, 0.0);
        assert_eq!(DetectResponse::new(true, 0.123456).confidence, 0.1235);
    }
}
