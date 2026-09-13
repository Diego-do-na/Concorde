//! Non-blocking Tiger Data event writer (T048).
//!
//! Storage is observability, not a dependency (§7.2, AGENTS.md rule 4):
//! `/detect` must never slow down, block, or fail because Tiger Data is
//! unreachable, slow, or unconfigured. [`EventWriter`] is the seam that
//! guarantees this —
//!
//! - [`EventWriter::spawn`] creates a bounded `tokio::mpsc` channel
//!   (capacity 1024) and hands ownership of the receiving end to a
//!   background task; the returned [`EventWriter`] only ever exposes
//!   [`EventWriter::record`], which is a synchronous, non-async `try_send`.
//!   `/detect`'s hot path never `.await`s storage.
//! - On backpressure (channel full) `record` drops the event and increments
//!   a counter instead of blocking — a burst of Tiger Data slowness turns
//!   into lost observability rows, never into slower `/detect` calls.
//! - The background task owns a lazily-connected `sqlx::PgPool` (connect
//!   timeout 3s, `sslmode=require`), batches incoming events (flushed every
//!   500ms or every 50 rows, whichever comes first), and updates
//!   `AppState::deps["tigerdata"]` so `/health` reflects reality without
//!   itself doing any network I/O.
//! - Any database error (initial connect, or a later insert) marks the dep
//!   `Degraded`, logs once per minute (never spammed), and the task keeps
//!   running — reconnecting on the next flush tick. It is never a reason to
//!   stop serving `/detect`.
//! - Disabled cleanly when `TIGERDATA_URL` is unset: [`writer_for`] (the
//!   process-wide singleton getter [`pipeline::analyze_bytes`] calls
//!   through [`record`]) returns `None` without ever touching the network,
//!   and `/health` (reading `AppState::deps`, already wired for this key)
//!   reports `tigerdata: disabled`.
//!
//! This task's scope is `storage/` and `pipeline.rs` only, so the writer is
//! a lazily-booted global singleton (mirroring `metrics::GLOBAL_METRICS`)
//! keyed off `Config::tigerdata_url` rather than a new `AppState` field --
//! [`record`] is the one call `pipeline.rs` makes, taking `&SharedState`
//! only to read `config.tigerdata_url` and to update the pre-existing
//! `deps` registry that `/health` already reads.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};

use serde::Serialize;
use sqlx::postgres::{PgPoolOptions, PgSslMode};
use sqlx::{ConnectOptions, Postgres};
use tokio::sync::mpsc;

use crate::metrics::DepStatus;
use crate::state::SharedState;

/// Bounded channel capacity (§ task (a)): sized so a burst of concurrent
/// `/detect` calls never blocks on storage even under sustained Tiger Data
/// slowness -- once full, `record` drops rather than waits.
const CHANNEL_CAPACITY: usize = 1024;
/// Flush a batch after this many rows even if the tick hasn't fired yet.
const BATCH_MAX_ROWS: usize = 50;
/// Flush whatever has accumulated at least this often.
const FLUSH_INTERVAL: Duration = Duration::from_millis(500);
/// Initial connect timeout (§ task (a)).
const CONNECT_TIMEOUT: Duration = Duration::from_secs(3);
/// Never log the same recurring failure more than once per minute.
const LOG_INTERVAL: Duration = Duration::from_secs(60);

/// One row's worth of data for the `detection_events` table -- deliberately
/// thin: no audio, no transcript text, nothing NFR-011 forbids (§13.4).
#[derive(Debug, Clone, Serialize)]
pub struct DetectionEvent {
    /// Both ids are `Option` because `pipeline::analyze_bytes` (this
    /// task's only caller, per its scope) doesn't itself see the
    /// request/call id -- those are assigned one layer up in
    /// `routes::detect`/`routes::analyze`. Left as a hook for a future
    /// task to thread them through rather than a schema change.
    pub request_id: Option<String>,
    pub call_id: Option<String>,
    pub is_synthetic: bool,
    pub confidence: f64,
    pub p_synthetic: f64,
    pub duration_s: f32,
    pub model_version: Option<String>,
}

/// Handle to the background writer (held by the process-wide [`WRITER`]
/// singleton below): cheap to clone, safe to call from any `/detect`
/// handler. The only public entry point is [`Self::record`].
pub struct EventWriter {
    tx: mpsc::Sender<DetectionEvent>,
    dropped: AtomicU64,
}

impl EventWriter {
    /// Boot the writer: spawns the background flusher task and returns the
    /// handle immediately. Never blocks on a real connection -- the pool is
    /// established lazily inside the background task on its first flush
    /// attempt, so a slow/unreachable database never delays process
    /// startup either.
    pub fn spawn(url: String, deps: Arc<Mutex<HashMap<String, DepStatus>>>) -> Arc<Self> {
        let (tx, rx) = mpsc::channel(CHANNEL_CAPACITY);
        let writer = Arc::new(Self { tx, dropped: AtomicU64::new(0) });
        tokio::spawn(run_background(url, rx, deps));
        writer
    }

    /// Non-blocking, non-async enqueue. Drops the event (and counts the
    /// drop) instead of waiting when the channel is full -- `/detect` never
    /// awaits storage (§ task (a)).
    pub fn record(&self, event: DetectionEvent) {
        if self.tx.try_send(event).is_err() {
            self.dropped.fetch_add(1, Ordering::Relaxed);
            tracing::debug!(event = "storage_dropped", "detection event dropped: channel full or writer closed");
        }
    }

    /// Total events dropped on backpressure since boot -- exposed for
    /// tests and for `/metrics`.
    pub fn dropped_count(&self) -> u64 {
        self.dropped.load(Ordering::Relaxed)
    }
}

/// Process-wide singleton, booted at most once on the first `/detect` call
/// that has a `tigerdata_url` configured (mirrors `metrics::GLOBAL_METRICS`
/// -- there is exactly one writer, one pool, one background task for the
/// process lifetime, never one per request).
static WRITER: OnceLock<Option<Arc<EventWriter>>> = OnceLock::new();

/// Get (booting on first call) the singleton writer for `state`'s
/// configured `tigerdata_url`, or `None` when it's unset -- the "disabled
/// cleanly" case. Marks `deps["tigerdata"] = Disabled` the first time
/// that's true, so `/health` reflects it even though nothing else ever
/// touches the registry in that case.
fn writer_for(state: &SharedState) -> Option<Arc<EventWriter>> {
    WRITER
        .get_or_init(|| match state.config.tigerdata_url.clone() {
            Some(url) => {
                tracing::info!(event = "storage_boot", enabled = true, "tigerdata event writer starting");
                Some(EventWriter::spawn(url, state.deps.clone()))
            }
            None => {
                set_status(&state.deps, DepStatus::Disabled);
                tracing::info!(event = "storage_boot", enabled = false, "tigerdata disabled: TIGERDATA_URL not set");
                None
            }
        })
        .clone()
}

/// Called once per successful `/detect`/`/analyze` pipeline run
/// ([`crate::pipeline::analyze_bytes_with_timeout`]): fire-and-forget,
/// never awaited. A no-op (not even a channel lookup) when storage is
/// disabled.
pub fn record(state: &SharedState, event: DetectionEvent) {
    if let Some(writer) = writer_for(state) {
        writer.record(event);
    }
}

fn set_status(deps: &Arc<Mutex<HashMap<String, DepStatus>>>, status: DepStatus) {
    deps.lock().unwrap().insert("tigerdata".to_string(), status);
}

/// The background flusher: owns the lazily-connected pool, batches
/// incoming events, and never lets a database error propagate anywhere
/// near `/detect`.
async fn run_background(
    url: String,
    mut rx: mpsc::Receiver<DetectionEvent>,
    deps: Arc<Mutex<HashMap<String, DepStatus>>>,
) {
    let mut pool: Option<sqlx::PgPool> = None;
    let mut last_error_logged: Option<Instant> = None;
    let mut batch: Vec<DetectionEvent> = Vec::with_capacity(BATCH_MAX_ROWS);
    let mut ticker = tokio::time::interval(FLUSH_INTERVAL);
    ticker.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);

    loop {
        tokio::select! {
            biased;
            maybe_event = rx.recv() => {
                match maybe_event {
                    Some(event) => {
                        batch.push(event);
                        if batch.len() >= BATCH_MAX_ROWS {
                            flush(&url, &mut pool, &mut batch, &deps, &mut last_error_logged).await;
                        }
                    }
                    None => {
                        // Sender (EventWriter) dropped -- flush whatever's
                        // left, then exit.
                        flush(&url, &mut pool, &mut batch, &deps, &mut last_error_logged).await;
                        return;
                    }
                }
            }
            _ = ticker.tick() => {
                if !batch.is_empty() {
                    flush(&url, &mut pool, &mut batch, &deps, &mut last_error_logged).await;
                }
            }
        }
    }
}

/// Ensure a pool exists (connecting lazily on first use), insert the whole
/// batch in one statement, and clear it regardless of outcome -- a batch
/// that fails to insert is logged and dropped, never retried indefinitely
/// (retrying would just grow unbounded memory instead of dropping cleanly).
async fn flush(
    url: &str,
    pool: &mut Option<sqlx::PgPool>,
    batch: &mut Vec<DetectionEvent>,
    deps: &Arc<Mutex<HashMap<String, DepStatus>>>,
    last_error_logged: &mut Option<Instant>,
) {
    if pool.is_none() {
        match connect(url).await {
            Ok(p) => {
                *pool = Some(p);
            }
            Err(e) => {
                log_error_throttled(last_error_logged, "connect", &e.to_string());
                set_status(deps, DepStatus::Degraded);
                batch.clear();
                return;
            }
        }
    }

    let p = pool.as_ref().expect("just ensured");
    match insert_batch(p, batch).await {
        Ok(()) => {
            set_status(deps, DepStatus::Ok);
        }
        Err(e) => {
            log_error_throttled(last_error_logged, "insert", &e.to_string());
            set_status(deps, DepStatus::Degraded);
            // Drop the pool too: a broken connection is more likely to
            // fail again than recover mid-batch; next flush reconnects.
            *pool = None;
        }
    }
    batch.clear();
}

fn log_error_throttled(last_logged: &mut Option<Instant>, stage: &str, detail: &str) {
    let now = Instant::now();
    let should_log = match last_logged {
        Some(t) if now.duration_since(*t) < LOG_INTERVAL => false,
        _ => true,
    };
    if should_log {
        tracing::error!(
            event = "incident",
            component = "tigerdata_writer",
            stage,
            detail,
            "tigerdata event write failed; storage degraded, /detect unaffected"
        );
        *last_logged = Some(now);
    }
}

async fn connect(url: &str) -> Result<sqlx::PgPool, sqlx::Error> {
    let mut opts: sqlx::postgres::PgConnectOptions = url.parse()?;
    // Require TLS unless the URL itself already asked for something more
    // specific (e.g. a local dev Postgres with `sslmode=disable`).
    if !url.contains("sslmode") {
        opts = opts.ssl_mode(PgSslMode::Require);
    }
    opts = opts.disable_statement_logging();

    let pool = tokio::time::timeout(
        CONNECT_TIMEOUT,
        PgPoolOptions::new().max_connections(4).acquire_timeout(CONNECT_TIMEOUT).connect_with(opts),
    )
    .await
    .map_err(|_| sqlx::Error::PoolTimedOut)??;

    ensure_schema(&pool).await?;
    Ok(pool)
}

/// Creates the table if it doesn't already exist -- keeps this task
/// self-contained (no separate migration runner needed to exercise it in
/// CI) without taking over ownership of a real migrations directory.
async fn ensure_schema(pool: &sqlx::Pool<Postgres>) -> Result<(), sqlx::Error> {
    sqlx::query(
        r#"
        CREATE TABLE IF NOT EXISTS detection_events (
            id BIGSERIAL PRIMARY KEY,
            request_id TEXT,
            call_id TEXT,
            is_synthetic BOOLEAN NOT NULL,
            confidence DOUBLE PRECISION NOT NULL,
            p_synthetic DOUBLE PRECISION NOT NULL,
            duration_s REAL NOT NULL,
            model_version TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        "#,
    )
    .execute(pool)
    .await?;
    Ok(())
}

async fn insert_batch(pool: &sqlx::Pool<Postgres>, batch: &[DetectionEvent]) -> Result<(), sqlx::Error> {
    if batch.is_empty() {
        return Ok(());
    }
    let mut tx = pool.begin().await?;
    for event in batch {
        sqlx::query(
            r#"
            INSERT INTO detection_events
                (request_id, call_id, is_synthetic, confidence, p_synthetic, duration_s, model_version)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            "#,
        )
        .bind(&event.request_id)
        .bind(&event.call_id)
        .bind(event.is_synthetic)
        .bind(event.confidence)
        .bind(event.p_synthetic)
        .bind(event.duration_s)
        .bind(&event.model_version)
        .execute(&mut *tx)
        .await?;
    }
    tx.commit().await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration as StdDuration;

    fn test_deps() -> Arc<Mutex<HashMap<String, DepStatus>>> {
        Arc::new(Mutex::new(HashMap::new()))
    }

    fn sample_event(n: usize) -> DetectionEvent {
        DetectionEvent {
            request_id: Some(format!("req-{n}")),
            call_id: Some(format!("call-{n}")),
            is_synthetic: n % 2 == 0,
            confidence: 0.5,
            p_synthetic: 0.5,
            duration_s: 1.0,
            model_version: Some("test".to_string()),
        }
    }

    /// With an unreachable URL, 100 `record` calls must all return
    /// immediately (never await storage) and the dep status must end up
    /// `Degraded` once the background task has had a chance to try (and
    /// fail) to connect.
    #[tokio::test]
    async fn unreachable_url_never_blocks_record_and_marks_degraded() {
        let deps = test_deps();
        // Port 1 is never a live Postgres in test environments. Depending
        // on the sandbox this either fails fast (connection refused) or
        // is silently dropped until the connect/acquire timeout fires --
        // either way it's the "unreachable URL" case this test covers.
        let writer = EventWriter::spawn(
            "postgres://user:pass@127.0.0.1:1/nonexistent".to_string(),
            deps.clone(),
        );

        let start = Instant::now();
        for i in 0..100 {
            writer.record(sample_event(i));
        }
        let elapsed = start.elapsed();
        assert!(elapsed < StdDuration::from_millis(200), "record() must never block on storage, took {elapsed:?}");

        // Give the background task a moment to attempt the (failing)
        // connect and update the dep registry.
        tokio::time::sleep(StdDuration::from_millis(3_700)).await;
        let status = deps.lock().unwrap().get("tigerdata").copied();
        assert!(matches!(status, Some(DepStatus::Degraded)), "expected Degraded, got {status:?}");
    }

    /// A full channel drops events and counts them instead of blocking.
    #[tokio::test]
    async fn full_channel_drops_and_counts_without_awaiting() {
        let deps = test_deps();
        // Hold the receiver ourselves (don't run the background task) so
        // the channel actually fills up.
        let (tx, mut rx) = mpsc::channel(4);
        let writer = Arc::new(EventWriter { tx, dropped: AtomicU64::new(0) });

        for i in 0..10 {
            writer.record(sample_event(i));
        }
        assert!(writer.dropped_count() >= 6, "expected at least 6 drops, got {}", writer.dropped_count());

        // Drain so the receiver doesn't get dropped mid-test (irrelevant
        // to the assertion, just avoids a channel-closed warning).
        while rx.try_recv().is_ok() {}
        let _ = deps; // unused here, kept for symmetry with the other test
    }

    /// Real-Postgres path (§ task (b) VERIFY): 10 events land in
    /// `detection_events`. Ignored by default -- run explicitly against a
    /// local/CI Postgres with `TIGERDATA_TEST_URL` set, e.g.:
    /// `TIGERDATA_TEST_URL=postgres://postgres:postgres@localhost:5432/postgres \
    ///   cargo test --lib storage::tests::ten_events_land_in_the_table -- --ignored`
    #[tokio::test]
    #[ignore]
    async fn ten_events_land_in_the_table() {
        let url = std::env::var("TIGERDATA_TEST_URL")
            .expect("set TIGERDATA_TEST_URL to a live Postgres to run this test");
        let deps = test_deps();
        let writer = EventWriter::spawn(url.clone(), deps.clone());

        for i in 0..10 {
            writer.record(sample_event(i));
        }

        // Give the background task time to connect, create the table and
        // flush (well under BATCH_MAX_ROWS, so this waits for a tick).
        tokio::time::sleep(StdDuration::from_millis(1_500)).await;

        let pool = connect(&url).await.expect("verify connection for assertion");
        let count: (i64,) = sqlx::query_as("SELECT count(*) FROM detection_events")
            .fetch_one(&pool)
            .await
            .expect("count query");
        assert!(count.0 >= 10, "expected at least 10 rows, found {}", count.0);

        let status = deps.lock().unwrap().get("tigerdata").copied();
        assert!(matches!(status, Some(DepStatus::Ok)), "expected Ok, got {status:?}");
    }
}
