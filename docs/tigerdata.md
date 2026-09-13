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

## T049 verification findings against the real Tiger Data instance

Pre-merge verification for T049 was done against the actual TimescaleDB
Cloud instance (`uft6h053bw.cc2f1ioxl1.tsdb.cloud.timescale.com:34524/tsdb`),
not just the schema on paper. Two real bugs surfaced and were fixed as
part of closing this out; a third is still open and blocks real storage
today — **flagging it here since it's outside T049's own scope
(`storage/mod.rs` belongs to T048/Paul, per AGENTS.md's scope-ownership
rule) and needs explicit follow-up before judging.**

### 1. Fixed — `storage::EventWriter::ensure_schema` (T048) had already
created `detection_events` with a different schema than `001_init.sql`

`storage/mod.rs`'s `CREATE TABLE IF NOT EXISTS` had already run against
this instance (from earlier `/detect` traffic against the real deployment)
using its own column set (`id, request_id, call_id, is_synthetic,
confidence, p_synthetic, duration_s, model_version, created_at`). Because
the table already existed, `001_init.sql`'s own `CREATE TABLE IF NOT
EXISTS` silently no-opped, and `create_hypertable('detection_events',
'ts', ...)` then failed outright with `column "ts" does not exist` — the
continuous aggregate and readable view were never created as a result.
**Resolved for this verification** (authorized explicitly, test data only:
5 rows from local `/detect` smoke calls, zero real loss) by `DROP TABLE
detection_events` on this instance and re-running `001_init.sql` clean, so
the hypertable, continuous aggregate, and readable view now exist with the
schema `001_init.sql`/this doc actually document.

### 2. Fixed — wrong `approx_percentile` argument order in `001_init.sql`

`001_init.sql`'s `detection_stats_1m_readable` view called
`approx_percentile(latency_pct, 0.50)` (sketch first, percentile second).
The real `timescaledb_toolkit` function on this instance (confirmed via
`\df *approx_percentile*`) is `approx_percentile(percentile double
precision, sketch uddsketch)` — **percentile first**. The view creation
failed with `function approx_percentile(uddsketch, numeric) does not
exist`. Fixed in `001_init.sql` (`approx_percentile(0.50, latency_pct)`
etc.) and re-applied to this instance; `GET /history` now queries this
view successfully against the real database — `curl .../history?window=24h&bucket=1m`
returns `{"degraded": false, "buckets": []}` at HTTP 200 (empty because,
per finding #3 below, no row has successfully inserted against the
official schema yet — this confirms the query path works against the real
instance, not that it errors, but it is *not* the same as a confirmed
non-empty bucket end to end). That last step still needs finding #3 fixed,
then a real `/detect` call, then one continuous-aggregate refresh cycle
(~1 minute) before `/history` will show an actual row.

### 3. OPEN — `storage::EventWriter`'s INSERT no longer matches the
official schema; real storage is broken until T048 is adjusted

After the schema was fixed to match `001_init.sql` (the columns in the
table at the top of this doc: `ts, request_id, is_synthetic, confidence,
p_synthetic, latency_ms, decode_ms, vad_ms, features_ms, inference_ms,
semantic_ms, semantic_available, acoustic_available, model_version,
outcome, duration_s`), a real `/detect` call against this instance
produces:

```
tigerdata event write failed; storage degraded, /detect unaffected
error returned from database: column "call_id" of relation "detection_events" does not exist
```

`/detect` itself was correctly unaffected (still HTTP 200 with a real
verdict — the "storage is observability, not a dependency" contract held),
but **`deps.tigerdata` went to `degraded` and no row was written**: T048's
`ensure_schema`/`insert_batch` (`product/api/src/storage/mod.rs`) still
target the old column set (`call_id`, `created_at`, no `ts`/`latency_ms`/
`outcome`/per-stage timing columns) and were never updated to match
`001_init.sql`'s schema. Until this is fixed, `/history` has nothing new
to read against once existing rows age out of the `24h` default window,
even though the read path (T049) and the aggregate/view (this doc's §1-2
fixes) are now correct.

**This is `storage/mod.rs` scope (T048/Paul), not `routes/history.rs`
scope (T049) — per AGENTS.md's scope-ownership rule, T049 does not touch
`storage/mod.rs` to fix this.** Needs a follow-up task (or an amendment to
T048 if still open) to align `ensure_schema`'s `CREATE TABLE` and
`insert_batch`'s `INSERT` column list with the schema documented at the
top of this file, before judging — otherwise Tiger Data logging silently
degrades to zero rows written on any fresh deployment that runs
`001_init.sql` first.
