# CONCORDE Latency Report

This document records client-side and server-side latency percentiles for public validation runs.

How to produce:
- Run Altur's client from a laptop on mobile data:
  python $CONCORDE_DATASET_DIR/scripts/check_endpoint.py --url https://HOSTNAME/detect --split val --n 0 --out val_check_public.json
- Server-side percentiles:
  product/deploy/measure_latency.sh val_check_public.json

Report table (example placeholders)

Client-side (judge output)
| metric | value |
|--------|-------|
| answered | 71 |
| errors | 0 |
| max_latency_s | <30 |
| median_latency_s | TBD |

Server-side (from measure_latency.sh)
| percentile | ms |
|------------|----|
| p50 | TBD |
| p95 | TBD |
| p99 | TBD |

Link from README / exec view: ensure docs/latency-report.md is cited by `product/deploy/DEPLOY_LOG.md` and the console Exec view.

