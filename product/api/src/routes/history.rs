//! `GET /history` — historical chart data for the console's exec view
//! (FR-015, T049), read from the Tiger Data continuous aggregate
//! `detection_stats_1m_readable` (schema: `product/deploy/sql/001_init.sql`,
//! documented in `docs/tigerdata.md`).
//!
//! Like storage's write side (T048, `storage/mod.rs`), this route treats
//! Tiger Data as observability, never a dependency `/detect` or the
//! console can be blocked by (§7.2, AGENTS.md rule 4): when storage is
//! disabled (`TIGERDATA_URL` unset) or degraded (connect/query failure),
//! this route still returns HTTP 200 with `{"degraded": true, "buckets":
//! []}` and logs the incident internally — never a 5xx, never an error
//! body (ADR-006's "no unmarked degradation" principle extended to reads).
//!
//! This route keeps its own lazily-connected read pool, independent of
//! `storage::EventWriter`'s write-side pool — this task's scope is
//! `routes/history.rs` only, and a read query has no reason to share a
//! connection pool with the batched async writer.

use axum::extract::{Query, State};
use axum::response::IntoResponse;
use axum::Json;
use serde::{Deserialize, Serialize};
use serde_json::json;
use sqlx::postgres::{PgPoolOptions, PgSslMode};
use sqlx::{ConnectOptions, Row};
use std::sync::OnceLock;
use std::time::Duration;
use tokio::sync::Mutex as AsyncMutex;

use crate::state::SharedState;

const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
const QUERY_TIMEOUT: Duration = Duration::from_secs(5);

/// One process-wide, lazily-connected read pool -- booted on the first
/// `/history` call that has `TIGERDATA_URL` configured, mirroring
/// `storage::WRITER`'s singleton pattern but kept separate from it.
static READ_POOL: OnceLock<AsyncMutex<Option<sqlx::PgPool>>> = OnceLock::new();

fn read_pool_cell() -> &'static AsyncMutex<Option<sqlx::PgPool>> {
    READ_POOL.get_or_init(|| AsyncMutex::new(None))
}

#[derive(Deserialize)]
pub struct HistoryQuery {
    /// Lookback window, e.g. "24h", "7d", "90m". Defaults to "24h".
    pub window: Option<String>,
    /// Bucket width, one of "1m" (native aggregate granularity),
    /// "5m", "15m", "1h", "1d" (re-bucketed via `time_bucket` over the
    /// readable view). Defaults to "1m".
    pub bucket: Option<String>,
}

#[derive(Serialize)]
pub struct HistoryBucket {
    /// RFC 3339 bucket start timestamp.
    pub bucket: String,
    pub count: i64,
    pub synthetic_count: i64,
    pub p50_latency_ms: Option<f64>,
    pub p95_latency_ms: Option<f64>,
    pub p99_latency_ms: Option<f64>,
    pub avg_confidence: Option<f64>,
}

/// `GET /history?window=24h&bucket=1m`. Always HTTP 200 (§ task VERIFY):
/// storage disabled/degraded/any query error -> `{"degraded": true,
/// "buckets": []}`; otherwise `{"degraded": false, "buckets": [...]}`.
pub async fn history(
    State(state): State<SharedState>,
    Query(q): Query<HistoryQuery>,
) -> impl IntoResponse {
    let window = parse_duration(q.window.as_deref().unwrap_or("24h")).unwrap_or(Duration::from_secs(24 * 3600));
    let bucket_interval = match parse_bucket(q.bucket.as_deref().unwrap_or("1m")) {
        Some(b) => b,
        None => return degraded_response(),
    };

    let Some(url) = state.config.tigerdata_url.clone() else {
        // Disabled cleanly: no attempt to touch the network at all.
        return degraded_response();
    };

    match fetch_buckets(&url, window, bucket_interval).await {
        Ok(buckets) => Json(json!({ "degraded": false, "buckets": buckets })).into_response(),
        Err(e) => {
            tracing::error!(
                event = "incident",
                component = "history_route",
                detail = %e,
                "GET /history query failed; returning degraded response"
            );
            degraded_response()
        }
    }
}

fn degraded_response() -> axum::response::Response {
    Json(json!({ "degraded": true, "buckets": [] })).into_response()
}

/// Parses simple durations like "24h", "7d", "90m", "45s". Returns `None`
/// on anything unparseable (caller falls back to the default).
fn parse_duration(s: &str) -> Option<Duration> {
    let s = s.trim();
    if s.len() < 2 {
        return None;
    }
    let (num, unit) = s.split_at(s.len() - 1);
    let n: u64 = num.parse().ok()?;
    let secs = match unit {
        "s" => n,
        "m" => n * 60,
        "h" => n * 3600,
        "d" => n * 86400,
        _ => return None,
    };
    Some(Duration::from_secs(secs))
}

/// Bucket widths we accept, mapped to a Postgres `interval` literal usable
/// with `time_bucket(...)`. `1m` is the aggregate's native granularity
/// (queried straight from `detection_stats_1m_readable`); anything coarser
/// is re-bucketed on read.
fn parse_bucket(s: &str) -> Option<&'static str> {
    match s.trim() {
        "1m" => Some("1 minute"),
        "5m" => Some("5 minutes"),
        "15m" => Some("15 minutes"),
        "1h" => Some("1 hour"),
        "1d" => Some("1 day"),
        _ => None,
    }
}

async fn ensure_pool(url: &str) -> Result<sqlx::PgPool, sqlx::Error> {
    let cell = read_pool_cell();
    let mut guard = cell.lock().await;
    if let Some(p) = guard.as_ref() {
        return Ok(p.clone());
    }
    let mut opts: sqlx::postgres::PgConnectOptions = url.parse()?;
    if !url.contains("sslmode") {
        opts = opts.ssl_mode(PgSslMode::Require);
    }
    opts = opts.disable_statement_logging();

    let pool = tokio::time::timeout(
        CONNECT_TIMEOUT,
        PgPoolOptions::new().max_connections(2).acquire_timeout(CONNECT_TIMEOUT).connect_with(opts),
    )
    .await
    .map_err(|_| sqlx::Error::PoolTimedOut)??;

    *guard = Some(pool.clone());
    Ok(pool)
}

async fn fetch_buckets(
    url: &str,
    window: Duration,
    bucket_interval: &str,
) -> Result<Vec<HistoryBucket>, sqlx::Error> {
    let pool = ensure_pool(url).await?;
    let window_secs = window.as_secs() as f64;

    // 1m is the aggregate's native granularity: read straight from the
    // readable view. Anything coarser re-buckets it with `time_bucket`,
    // summing counts and re-averaging confidence weighted by event count;
    // percentiles are approximated as the (unweighted) average of the
    // per-minute approx-percentiles falling in the coarser bucket -- an
    // approximation of an approximation, acceptable for a chart strip,
    // never for anything scored.
    let rows = if bucket_interval == "1 minute" {
        tokio::time::timeout(
            QUERY_TIMEOUT,
            sqlx::query(
                r#"
                SELECT
                    bucket,
                    events::bigint AS count,
                    synthetic_events::bigint AS synthetic_count,
                    p50_latency_ms,
                    p95_latency_ms,
                    p99_latency_ms,
                    avg_confidence
                FROM detection_stats_1m_readable
                WHERE bucket >= now() - ($1 || ' seconds')::interval
                ORDER BY bucket ASC
                "#,
            )
            .bind(window_secs)
            .fetch_all(&pool),
        )
        .await
        .map_err(|_| sqlx::Error::PoolTimedOut)??
    } else {
        tokio::time::timeout(
            QUERY_TIMEOUT,
            sqlx::query(
                r#"
                SELECT
                    time_bucket($2::interval, bucket) AS bucket,
                    sum(events)::bigint AS count,
                    sum(synthetic_events)::bigint AS synthetic_count,
                    avg(p50_latency_ms) AS p50_latency_ms,
                    avg(p95_latency_ms) AS p95_latency_ms,
                    avg(p99_latency_ms) AS p99_latency_ms,
                    (sum(avg_confidence * events) / nullif(sum(events), 0)) AS avg_confidence
                FROM detection_stats_1m_readable
                WHERE bucket >= now() - ($1 || ' seconds')::interval
                GROUP BY time_bucket($2::interval, bucket)
                ORDER BY 1 ASC
                "#,
            )
            .bind(window_secs)
            .bind(bucket_interval)
            .fetch_all(&pool),
        )
        .await
        .map_err(|_| sqlx::Error::PoolTimedOut)??
    };

    let buckets = rows
        .into_iter()
        .map(|row| {
            let bucket: chrono::DateTime<chrono::Utc> = row.try_get("bucket")?;
            Ok(HistoryBucket {
                bucket: bucket.to_rfc3339(),
                count: row.try_get::<Option<i64>, _>("count")?.unwrap_or(0),
                synthetic_count: row.try_get::<Option<i64>, _>("synthetic_count")?.unwrap_or(0),
                p50_latency_ms: row.try_get("p50_latency_ms")?,
                p95_latency_ms: row.try_get("p95_latency_ms")?,
                p99_latency_ms: row.try_get("p99_latency_ms")?,
                avg_confidence: row.try_get("avg_confidence")?,
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()?;

    Ok(buckets)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_common_durations() {
        assert_eq!(parse_duration("24h"), Some(Duration::from_secs(86400)));
        assert_eq!(parse_duration("7d"), Some(Duration::from_secs(7 * 86400)));
        assert_eq!(parse_duration("90m"), Some(Duration::from_secs(90 * 60)));
        assert_eq!(parse_duration("garbage"), None);
    }

    #[test]
    fn parses_known_buckets_only() {
        assert_eq!(parse_bucket("1m"), Some("1 minute"));
        assert_eq!(parse_bucket("1h"), Some("1 hour"));
        assert_eq!(parse_bucket("2m"), None);
    }
}
