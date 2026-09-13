#!/usr/bin/env bash
# Replay the fault-injection suite's cases over HTTP against a live
# deployment (T039 (b) VERIFY; used by T055 against the public Vultr
# deployment). Unlike tests/fault_injection.rs (in-process, dummy model),
# this drives a real `POST /detect` over the network with curl, so it also
# exercises the real model, real timeouts, and the real network path.
#
# Usage: tests/fault_injection_remote.sh <base_url>
#   e.g. tests/fault_injection_remote.sh https://concorde.example.com
#
# Every case MUST return HTTP 200 with a JSON body whose key set is exactly
# {is_synthetic, confidence} (ADR-006, ADR-013) — this script never asserts
# a *value*, since the remote runs the real model over cases most of which
# have no principled "correct" verdict; it only asserts the wire contract:
# 200, exactly two keys, confidence in [0, 1]. Run this against a
# deployment with CONCORDE_STRICT unset (production default); the point of
# hitting the real, always-on deployment is exactly to confirm it never
# leaks a 5xx to a caller, which strict mode would defeat.
set -u -o pipefail

BASE_URL="${1:?usage: fault_injection_remote.sh <base_url>}"
BASE_URL="${BASE_URL%/}"
DETECT_URL="$BASE_URL/detect"

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

PASS=0
FAIL=0

# --- helpers ----------------------------------------------------------

# check_request <name> <curl-args...>
# Runs curl against $DETECT_URL, asserts HTTP 200 and the exact
# {is_synthetic, confidence} key set with confidence in [0,1].
check_request() {
  local name="$1"
  shift
  local resp_file="$TMP_DIR/resp"
  local status
  status=$(curl -sS -o "$resp_file" -w '%{http_code}' "$DETECT_URL" "$@") || {
    echo "FAIL  $name: curl itself failed"
    FAIL=$((FAIL + 1))
    return
  }

  if [ "$status" != "200" ]; then
    echo "FAIL  $name: expected HTTP 200, got $status (body: $(cat "$resp_file"))"
    FAIL=$((FAIL + 1))
    return
  fi

  python3 - "$name" "$resp_file" <<'PY'
import json, sys
name, path = sys.argv[1], sys.argv[2]
try:
    with open(path) as f:
        body = json.load(f)
except Exception as e:
    print(f"FAIL  {name}: response body is not valid JSON: {e}")
    sys.exit(1)

if not isinstance(body, dict) or set(body.keys()) != {"is_synthetic", "confidence"}:
    print(f"FAIL  {name}: expected exactly {{is_synthetic, confidence}}, got {body!r}")
    sys.exit(1)

if not isinstance(body["is_synthetic"], bool):
    print(f"FAIL  {name}: is_synthetic is not a bool: {body['is_synthetic']!r}")
    sys.exit(1)

conf = body["confidence"]
if not isinstance(conf, (int, float)) or not (0.0 <= conf <= 1.0):
    print(f"FAIL  {name}: confidence out of [0,1]: {conf!r}")
    sys.exit(1)

print(f"PASS  {name}: {body}")
sys.exit(0)
PY
  if [ $? -eq 0 ]; then
    PASS=$((PASS + 1))
  else
    FAIL=$((FAIL + 1))
  fi
}

# make_wav <path> <sample_rate> <channels> <bits> <duration_s>
# Silent PCM WAV; good enough for wire-contract checks (this script never
# asserts a verdict value, only the response shape).
make_wav() {
  python3 - "$1" "$2" "$3" "$4" "$5" <<'PY'
import struct, sys
path, sr, ch, bits, dur = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), float(sys.argv[5])
n_frames = int(sr * dur)
bytes_per_sample = bits // 8
block_align = ch * bytes_per_sample
data_size = n_frames * block_align
with open(path, "wb") as f:
    f.write(b"RIFF")
    f.write(struct.pack("<I", 36 + data_size))
    f.write(b"WAVE")
    f.write(b"fmt ")
    f.write(struct.pack("<IHHIIHH", 16, 1, ch, sr, sr * block_align, block_align, bits))
    f.write(b"data")
    f.write(struct.pack("<I", data_size))
    f.write(b"\x00" * data_size)
PY
}

b64() {
  python3 -c "import base64,sys; sys.stdout.write(base64.b64encode(open(sys.argv[1],'rb').read()).decode())" "$1"
}

# --- cases (mirrors tests/fault_injection.rs) --------------------------

VALID_WAV="$TMP_DIR/valid.wav"
make_wav "$VALID_WAV" 8000 2 16 3.0

TRUNCATED_WAV="$TMP_DIR/truncated.wav"
head -c 100 "$VALID_WAV" > "$TRUNCATED_WAV"

ZERO_WAV="$TMP_DIR/zero.bin"
: > "$ZERO_WAV"

MONO_WAV="$TMP_DIR/mono.wav"
make_wav "$MONO_WAV" 8000 1 16 3.0

WAV_44K="$TMP_DIR/44k.wav"
make_wav "$WAV_44K" 44100 2 16 1.0

BLOB="$TMP_DIR/blob.bin"
head -c 1048576 /dev/urandom > "$BLOB" 2>/dev/null || python3 -c "import os,sys; sys.stdout.buffer.write(os.urandom(1048576))" > "$BLOB"

HALF_SEC_WAV="$TMP_DIR/half_sec.wav"
make_wav "$HALF_SEC_WAV" 8000 2 16 0.5

LONG_WAV="$TMP_DIR/long.wav"
make_wav "$LONG_WAV" 8000 1 16 300.0

OVERSIZED="$TMP_DIR/oversized.bin"
python3 -c "open('$OVERSIZED','wb').write(b'\x00' * (20*1024*1024))"

check_request "canonical JSON, truncated WAV (header only; half data)" \
  -H 'Content-Type: application/json' \
  -d "{\"call_id\":\"trunc-1\",\"audio_base64\":\"$(b64 "$TRUNCATED_WAV")\",\"sample_rate\":8000,\"channels\":2}"

check_request "canonical JSON, zero-byte audio" \
  -H 'Content-Type: application/json' \
  -d '{"call_id":"zero-1","audio_base64":"","sample_rate":8000,"channels":2}'

check_request "mono WAV" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$MONO_WAV"

check_request "44.1kHz WAV" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$WAV_44K"

check_request "1MB non-audio blob" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$BLOB"

check_request "declared metadata contradicts WAV header" \
  -H 'Content-Type: application/json' \
  -d "{\"call_id\":\"mismatch-1\",\"audio_base64\":\"$(b64 "$VALID_WAV")\",\"sample_rate\":16000,\"channels\":1}"

check_request "JSON with unknown key" \
  -H 'Content-Type: application/json' \
  -d '{"some_unrelated_field":42}'

check_request "JSON with empty string audio field" \
  -H 'Content-Type: application/json' \
  -d '{"audio_base64":""}'

check_request "raw base64 of garbage" \
  --data-binary "$(python3 -c "import base64; print(base64.b64encode(bytes(range(256))*8).decode())")"

check_request "20MB oversized body" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$OVERSIZED"

check_request "valid 0.5s WAV" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$HALF_SEC_WAV"

check_request "valid long silent WAV" \
  -H 'Content-Type: application/octet-stream' --data-binary "@$LONG_WAV"

echo
echo "$PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
