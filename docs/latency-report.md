# CONCORDE Latency Report

This document records client-side and server-side latency percentiles for public validation runs.

How to produce:
- Run Altur's client from a laptop on mobile data:
  python $CONCORDE_DATASET_DIR/scripts/check_endpoint.py --url https://HOSTNAME/detect --split val --n 0 --out val_check_public.json
- Server-side percentiles:
  product/deploy/measure_latency.sh val_check_public.json

Report table (this deploy)

Client-side (judge output)
| metric | value |
|--------|-------|
| answered | 71 |
| errors | 0 |
| max_latency_s | 0.556 |
| median_latency_s | 0.419 |

Server-side (from val_check_public.json / measure_latency.sh)
| percentile | ms |
|------------|----|
| p50 | 419 |
| p95 | 480 |
| p99 | 524 |

Link from README / exec view: ensure docs/latency-report.md is cited by `product/deploy/DEPLOY_LOG.md` and the console Exec view.

