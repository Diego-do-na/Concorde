#!/usr/bin/env bash
# Downloads the T057-approved whisper.cpp ggml model (ggml-tiny.bin) for
# server deployment into /opt/concorde/artifacts/ (or $1 if provided).
#
# sha256 is pinned to prevent corrupted or tampered models from loading in
# production.
#
# Usage: ./product/deploy/get_whisper_model.sh [target_dir]

set -euo pipefail

TARGET_DIR="${1:-/opt/concorde/artifacts}"
MODEL_NAME="ggml-tiny.bin"
EXPECTED_SHA="be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"
BASE_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

mkdir -p "$TARGET_DIR"

DEST="$TARGET_DIR/$MODEL_NAME"

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

if [[ -f "$DEST" ]]; then
  HAVE="$(sha256_of "$DEST")"
  if [[ "$HAVE" == "$EXPECTED_SHA" ]]; then
    echo "[get_whisper_model] $MODEL_NAME already present at $DEST with verified sha256 ($EXPECTED_SHA) - skipping download."
    exit 0
  fi
  echo "[get_whisper_model] $MODEL_NAME present but sha256 mismatch (have $HAVE, want $EXPECTED_SHA) - re-downloading..." >&2
fi

TMP_DEST="$DEST.tmp"

echo "[get_whisper_model] Downloading $MODEL_NAME to $TARGET_DIR..."
curl -fL --progress-bar -o "$TMP_DEST" "$BASE_URL/$MODEL_NAME"

GOT="$(sha256_of "$TMP_DEST")"
if [[ "$GOT" != "$EXPECTED_SHA" ]]; then
  echo "[get_whisper_model] ERROR: sha256 mismatch after download (got $GOT, want $EXPECTED_SHA)" >&2
  rm -f "$TMP_DEST"
  exit 1
fi

mv "$TMP_DEST" "$DEST"
echo "[get_whisper_model] SUCCESS: $MODEL_NAME downloaded to $DEST and sha256 verified ($EXPECTED_SHA)."
