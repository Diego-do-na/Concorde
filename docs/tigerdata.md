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

## `GET /history` (FR-015, T049)

Reads `detection_stats_1m_readable` and serves the console exec view's
historical chart strip. Storage is observability, not a dependency (§7.2,
AGENTS.md rule 4): this route is never a reason `/detect` slows down (it
keeps its own lazily-connected read pool, separate from
`storage::EventWriter`'s write-side pool), and it always answers HTTP 200,
even on a query/connect failure.

Request: `GET /history?window=24h&bucket=1m`
- `window` (default `24h`): lookback, `<n><unit>` with unit in `s|m|h|d`
  (e.g. `90m`, `7d`).
- `bucket` (default `1m`): one of `1m|5m|15m|1h|1d`. `1m` reads
  `detection_stats_1m_readable` directly (the aggregate's native
  granularity); coarser buckets re-bucket it on read with `time_bucket`
  (counts summed, confidence re-averaged weighted by event count,
  percentiles averaged across the constituent minutes — an approximation
  of an approximation, fine for a chart strip, never for anything scored).
  An unrecognized `bucket` value returns the degraded response below
  rather than guessing.

Response, always HTTP 200:
```json
{
  "degraded": false,
  "buckets": [
    {
      "bucket": "2026-09-13T12:34:00+00:00",
      "count": 42,
      "synthetic_count": 9,
      "p50_latency_ms": 812.0,
      "p95_latency_ms": 2140.5,
      "p99_latency_ms": 3980.2,
      "avg_confidence": 0.71
    }
  ]
}
```

When `TIGERDATA_URL` is unset (storage disabled) or a connect/query error
occurs (storage degraded), the route never errors or 5xxs — it returns
`{"degraded": true, "buckets": []}` and logs the incident internally
(`event = "incident", component = "history_route"`), matching `/detect`'s
own "never emit an unmarked degradation, always log it" contract (ADR-008).

### Populating the table for a demo

`product/deploy/replay_val.sh DATASET_DIR HOST [N]` POSTs `val` clips to a
live `/detect` deployment. Each successful call already writes a row via
`storage::EventWriter` (T048) when that deployment has `TIGERDATA_URL`
configured — no separate ingestion step is needed. Point it at the
exec-facing deployment before a demo (`./replay_val.sh ~/datasets/concorde/val
getconcorde.tech 20`) to populate `detection_stats_1m` with a fresh
historical strip; give the continuous-aggregate refresh policy (`schedule_interval
=> INTERVAL '1 minute'` in `001_init.sql`) a minute or two to catch up
before checking `/history`, or call `CALL refresh_continuous_aggregate(...)`
manually. A `--label`-style tag on individual replay runs (e.g. to mark a
batch as a rehearsal vs. the real judging run) isn't supported by the
script yet — `request_id`/`call_id` are the only per-row identifiers
`storage::EventWriter` writes today (see its own doc comment), so labeling
a replay batch means correlating by timestamp/window rather than a stored
tag until a future task threads one through.
