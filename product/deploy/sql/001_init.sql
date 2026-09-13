-- 001_init.sql
-- Tiger Data / TimescaleDB schema for CONCORDE events
-- Creates hypertable `detection_events`, a 1-minute continuous aggregate
-- `detection_stats_1m`, and a readable helper view with percentiles.
-- NOTE: Tiger Data SQL editor must have TimescaleDB and toolkit extensions enabled.

-- Create required extensions (no-op if already present)
CREATE EXTENSION IF NOT EXISTS timescaledb;
-- toolkit extension name may vary by service; try the common one
CREATE EXTENSION IF NOT EXISTS timescaledb_toolkit;

-- Raw events table (no audio/transcripts/PII)
CREATE TABLE IF NOT EXISTS detection_events (
  ts timestamptz NOT NULL,
  request_id text NOT NULL,
  is_synthetic boolean NOT NULL,
  confidence real NOT NULL,
  p_synthetic real NOT NULL,
  latency_ms integer,
  decode_ms integer,
  vad_ms integer,
  features_ms integer,
  inference_ms integer,
  semantic_ms integer NULL,
  semantic_available boolean NOT NULL,
  acoustic_available boolean NOT NULL,
  model_version text,
  outcome text,
  duration_s real
);

-- Convert to hypertable on time column `ts`
SELECT create_hypertable('detection_events', 'ts', if_not_exists => TRUE);

-- Continuous aggregate storing aggregated state (percentile_agg)
-- The exact percentile aggregate / toolkit functions are provided by Timescale extensions.
CREATE MATERIALIZED VIEW IF NOT EXISTS detection_stats_1m
WITH (timescaledb.continuous) AS
SELECT
  time_bucket('1 minute', ts) AS bucket,
  count(*) AS events,
  count(*) FILTER (WHERE is_synthetic) AS synthetic_events,
  -- store an aggregate state for approximate percentiles (toolkit-dependent)
  percentile_agg(latency_ms) AS latency_pct,
  avg(confidence) AS avg_confidence
FROM detection_events
GROUP BY bucket
WITH NO DATA;

-- Add a refresh policy to keep the 1m aggregate updated
SELECT add_continuous_aggregate_policy(
  'detection_stats_1m',
  start_offset => INTERVAL '1 hour',
  end_offset   => INTERVAL '1 minute',
  schedule_interval => INTERVAL '1 minute'
);

-- Readable helper view that expands stored percentile state into p50/p95/p99.
-- The helper below uses `approx_percentile(state, q)` which is commonly
-- available in Timescale toolkits; adapt if your service exposes a different name.
CREATE OR REPLACE VIEW detection_stats_1m_readable AS
SELECT
  bucket,
  events,
  synthetic_events,
  -- percentile_agg stored state -> approximate percentiles
  approx_percentile(latency_pct, 0.50) AS p50_latency_ms,
  approx_percentile(latency_pct, 0.95) AS p95_latency_ms,
  approx_percentile(latency_pct, 0.99) AS p99_latency_ms,
  avg_confidence
FROM detection_stats_1m;

-- Example convenience: function to refresh one window manually
-- CALL refresh_continuous_aggregate('detection_stats_1m', NULL, NULL);

