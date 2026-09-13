# Tiger Data SQL: CONCORDE events schema (001_init.sql)

This folder contains the SQL to create the `detection_events` hypertable,
a 1-minute continuous aggregate `detection_stats_1m`, and a readable helper
view that exposes p50/p95/p99 latencies.

Service / project (Tiger Data):
- Service: concorde-events
- Project: cc2f1ioxl1

How to run (Tiger Data SQL editor)
1. Open the Tiger Data console and select project `cc2f1ioxl1`.
2. Open the SQL editor for service `concorde-events`.
3. Copy the contents of `001_init.sql` and execute it.

Quick verification steps to run in the SQL editor
1. Show table/hypertable:
   \d+ detection_events

2. Insert three test rows (example):
   INSERT INTO detection_events (ts, request_id, is_synthetic, confidence, p_synthetic, latency_ms, decode_ms, vad_ms, features_ms, inference_ms, semantic_ms, semantic_available, acoustic_available, model_version, outcome, duration_s)
   VALUES
     (now() - INTERVAL '2 minutes', 'r1', false, 0.12, 0.12, 123, 5, 2, 10, 50, NULL, false, true, 'v1', 'ok', 1.2),
     (now() - INTERVAL '90 seconds', 'r2', true, 0.99, 0.99, 45, 3, 1, 5, 10, NULL, false, true, 'v1', 'synth', 0.5),
     (now() - INTERVAL '30 seconds', 'r3', false, 0.60, 0.60, 300, 10, 4, 20, 150, NULL, true, true, 'v1', 'ok', 2.1);

3. Manually refresh the continuous aggregate (if required by the service):
   CALL refresh_continuous_aggregate('detection_stats_1m', NULL, NULL);

4. Read the readable helper view:
   SELECT * FROM detection_stats_1m_readable ORDER BY bucket DESC LIMIT 10;

Paste the outputs of the commands above below (replace the placeholders):

---- Paste \d+ detection_events output here ----

---- Paste continuous-aggregate refresh output here ----

---- Paste SELECT from detection_stats_1m_readable here ----

Connection string and secrets
- The deployment/CI should keep the Tiger Data connection URL in `TIGERDATA_URL`.
  Do NOT commit any service credentials into git.

Destroying the service after the hackathon
- Use the Tiger Data console to delete the `concorde-events` service and project resources.
- Alternatively, drop objects manually:
  DROP MATERIALIZED VIEW IF EXISTS detection_stats_1m;
  DROP VIEW IF EXISTS detection_stats_1m_readable;
  DROP TABLE IF EXISTS detection_events;
  DROP EXTENSION IF EXISTS timescaledb_toolkit;
  DROP EXTENSION IF EXISTS timescaledb;

