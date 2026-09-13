#!/usr/bin/env bash
set -euo pipefail

# measure_latency.sh
# Usage:
#   product/deploy/measure_latency.sh <check_endpoint_output.json>
# Reads a JSON output produced by Altur's check_endpoint.py (e.g. --out val_check_public.json)
# and prints server-side p50/p95/p99 for the detect pipeline's total timing.
FILE="${1:-}"
if [ -z "$FILE" ]; then
  echo "Usage: $0 <check_endpoint_output.json>"
  exit 2
fi

if [ ! -f "$FILE" ]; then
  echo "File not found: $FILE"
  exit 3
fi

# Extract candidate timing values from several possible shapes.
# The judge output may be an array of objects, or a single object per-line.
TIMES=$(jq -r '
  (if type=="array" then .[] else . end) |
  ( .timings.total? // .timings_ms.total? // .timings? // .timings_ms? // .timings_total? // .timings_total_ms? ) as $t |
  if ($t|type)=="number" then $t else empty end
' "$FILE" || true)

if [ -z "$TIMES" ]; then
  echo "No timing values found in $FILE"
  exit 4
fi

# Compute percentiles in Python to avoid external deps.
printf "%s\n" "$TIMES" | python3 - "$FILE" <<'PY'
import sys, math
vals = [float(line.strip()) for line in sys.stdin if line.strip()]
if not vals:
    print("no data"); sys.exit(1)
# Heuristic: if values look like seconds (max < 100), convert to ms.
if max(vals) < 100:
    vals = [v * 1000.0 for v in vals]

vals.sort()
def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals)-1) * (p/100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return sorted_vals[int(k)]
    d0 = sorted_vals[int(f)] * (c - k)
    d1 = sorted_vals[int(c)] * (k - f)
    return d0 + d1

p50 = pct(vals, 50)
p95 = pct(vals, 95)
p99 = pct(vals, 99)
print("server-side timings (ms)")
print("------------------------")
print(f"count: {len(vals)}")
print(f"p50: {p50:.1f} ms")
print(f"p95: {p95:.1f} ms")
print(f"p99: {p99:.1f} ms")
PY

exit 0

