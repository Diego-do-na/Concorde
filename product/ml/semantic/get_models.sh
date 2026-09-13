#!/usr/bin/env bash
# Downloads the two ggml whisper.cpp models used by transcribe.py (tiny for
# a fast first pass, base for the model the probe ground truth is measured
# against) into ml/semantic/models/ (gitignored, product/.gitignore).
#
# sha256 is pinned below so a corrupted or substituted download is caught
# before it silently feeds garbage into transcription — this script never
# leaves a partial or unverified file at the final path.
#
# Usage: ./get_models.sh   (idempotent — safe to re-run; skips files whose
# sha256 already matches)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="$SCRIPT_DIR/models"
BASE_URL="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"

mkdir -p "$MODELS_DIR"

sha256_of() {
  shasum -a 256 "$1" | awk '{print $1}'
}

download() {
  local name="$1" expected_sha="$2"
  local dest="$MODELS_DIR/$name"

  if [[ -f "$dest" ]]; then
    local have
    have="$(sha256_of "$dest")"
    if [[ "$have" == "$expected_sha" ]]; then
      echo "[get_models] $name already present, sha256 verified — skipping"
      return
    fi
    echo "[get_models] $name present but sha256 mismatch (have $have, want $expected_sha) — re-downloading" >&2
  fi

  echo "[get_models] downloading $name ..."
  curl -fL --progress-bar -o "$dest.tmp" "$BASE_URL/$name"

  local got
  got="$(sha256_of "$dest.tmp")"
  if [[ "$got" != "$expected_sha" ]]; then
    echo "[get_models] ERROR: $name sha256 mismatch after download (got $got, want $expected_sha)" >&2
    rm -f "$dest.tmp"
    exit 1
  fi

  mv "$dest.tmp" "$dest"
  echo "[get_models] $name OK (sha256 $expected_sha)"
}

# Pinned against https://huggingface.co/ggerganov/whisper.cpp — verify with
# `shasum -a 256 ml/semantic/models/*.bin` if these ever need to be rotated.
download "ggml-tiny.bin" "be07e048e1e599ad46341c8d2a135645097a538221678b7acdd1b1919c6e1b21"
download "ggml-base.bin" "60ed5bc3dd14eea856493d334349b405782ddcaf0028d4b5df4088345fba2efe"

echo "[get_models] done — models in $MODELS_DIR"
echo "[get_models] whisper.cpp itself is not built by this script — see"
echo "[get_models] product/ml/README.md's semantic section for the one-time build."
