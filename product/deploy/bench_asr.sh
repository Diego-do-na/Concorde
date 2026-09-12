#!/bin/bash

# CONCORDE ASR Server Benchmark — whisper.cpp on Vultr
# Measures per-turn latency (encode + inference) on real Vultr hardware
# Usage: ./bench_asr.sh <audio_dir> <models_dir> [threads]
# Example: ./bench_asr.sh /opt/concorde/bench/audio /opt/concorde/artifacts 2

set -euo pipefail

AUDIO_DIR="${1:-.}"
MODELS_DIR="${2:-.}"
THREADS="${3:-2}"

# Validate inputs
if [[ ! -d "$AUDIO_DIR" ]]; then
    echo "ERROR: Audio directory not found: $AUDIO_DIR" >&2
    exit 1
fi

if [[ ! -d "$MODELS_DIR" ]]; then
    echo "ERROR: Models directory not found: $MODELS_DIR" >&2
    exit 1
fi

# Model files to test
MODELS=("ggml-tiny.bin" "ggml-base.bin")
SEGMENTS=("1s" "3s" "6s" "10s" "30s")

# Audio codec settings per segment
declare -A CODEC_FLAGS
CODEC_FLAGS["1s"]="-ac 512"
CODEC_FLAGS["3s"]="-ac 512"
CODEC_FLAGS["6s"]="-ac 512"
CODEC_FLAGS["10s"]="-ac 768"
CODEC_FLAGS["30s"]=""  # No -ac for 30s

REPETITIONS=3

echo "============================================"
echo "CONCORDE ASR Benchmark"
echo "============================================"
echo "Threads: $THREADS"
echo "Repetitions: $REPETITIONS"
echo "Models: ${MODELS[@]}"
echo "Segments: ${SEGMENTS[@]}"
echo "Audio directory: $AUDIO_DIR"
echo "Models directory: $MODELS_DIR"
echo ""

# Check for audio files
for segment in "${SEGMENTS[@]}"; do
    audio_file="$AUDIO_DIR/spanish_${segment}.wav"
    if [[ ! -f "$audio_file" ]]; then
        echo "WARNING: Missing audio file: $audio_file" >&2
    fi
done

# Run benchmarks
for model in "${MODELS[@]}"; do
    model_path="$MODELS_DIR/$model"

    if [[ ! -f "$model_path" ]]; then
        echo "WARNING: Model file not found: $model_path" >&2
        continue
    fi

    echo "Testing $model..."
    echo "---"

    for segment in "${SEGMENTS[@]}"; do
        audio_file="$AUDIO_DIR/spanish_${segment}.wav"

        if [[ ! -f "$audio_file" ]]; then
            echo "  $segment: MISSING AUDIO FILE"
            continue
        fi

        codec_flag="${CODEC_FLAGS[$segment]}"

        echo "  $segment (flags: -t $THREADS -l es -nt $codec_flag):"

        for rep in $(seq 1 $REPETITIONS); do
            # Run whisper.cpp with timing and RSS measurement
            # Note: This requires /usr/bin/time or similar timing utility
            # Output format: encode:%e warm_total:%e rss:%M

            result=$(/usr/bin/time -f "encode:%e warm_total:%e rss:%M" \
                /opt/concorde/bench/build/bin/whisper-cli \
                -m "$model_path" \
                -t "$THREADS" \
                -l es \
                -nt \
                $codec_flag \
                "$audio_file" 2>&1 | tail -1)

            echo "    rep$rep: $result"
        done
    done

    echo ""
done

echo "============================================"
echo "Benchmark complete"
echo "============================================"
