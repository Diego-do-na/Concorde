# Tiger Data (Timescale) schema - CONCORDE

This doc is a short reference for the schema created by `product/deploy/sql/001_init.sql`.

Table: detection_events

| column | type |
|---|---:|
| ts | timestamptz |
| request_id | text |
| is_synthetic | boolean |
| confidence | real |
| p_synthetic | real |
| latency_ms | integer |
| decode_ms | integer |
| vad_ms | integer |
| features_ms | integer |
| inference_ms | integer |
| semantic_ms | integer (nullable) |
| semantic_available | boolean |
| acoustic_available | boolean |
| model_version | text |
| outcome | text |
| duration_s | real |

Materialized view: detection_stats_1m (continuous aggregate)
- bucket (time_bucket 1 minute)
- events (count)
- synthetic_events (count filter is_synthetic)
- latency_pct (percentile_agg state)
- avg_confidence (avg(confidence))

Readable view: detection_stats_1m_readable
- Expands `latency_pct` into approximate percentiles: p50/p95/p99 via toolkit functions.

