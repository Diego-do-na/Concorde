# CONCORDE Latency Report

This document records client-side and server-side latency percentiles for public validation runs.

How to produce:
- Run Altur's client from a laptop on mobile data:
  python $CONCORDE_DATASET_DIR/scripts/check_endpoint.py --url https://HOSTNAME/detect --split val --n 0 --out val_check_public.json
- Server-side percentiles:
  product/deploy/measure_latency.sh val_check_public.json

Report table (final deployment - concorde-b-2)

**Date**: 2026-09-13  
**Model**: concorde-b-1 → concorde-b-2 (column index fix)  
**Commit**: 46e1236

Client-side (judge output, 71 val calls)
| metric | value |
|--------|-------|
| answered | 71 |
| errors | 0 |
| balanced_accuracy | 0.864 |
| auc | 0.944 |
| accuracy | 0.859 |
| tpr_synthetic | 0.971 |
| tnr_human | 0.757 |
| brier | 0.103 |
| max_latency_s | 0.589 |
| median_latency_s | 0.475 |

Server-side (from val_check_final.json)
| percentile | ms |
|------------|----|
| p50 | 475 |
| p95 | 568 |
| p99 | 585 |

**Training comparison**:
- Train CV AUC: 0.9654, Val AUC: 0.944 (gap: 0.021)
- Train BA: 0.8466 (estimated from calibration), Val BA: 0.864 (within expected range)
- Latency budget: max 589ms << 30s hard limit ✓

Link from README / exec view: ensure docs/latency-report.md is cited by `product/deploy/DEPLOY_LOG.md` and the console Exec view.

