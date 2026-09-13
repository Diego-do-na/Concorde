// Downsamples raw peak-envelope buckets (analysis.waveform.{caller,agent}, one
// value per bucket_ms) into exactly N pixel-buckets for a given plot width.
// Pure/no DOM so it is trivially unit-testable.

/**
 * Full-scale amplitude of `analysis.waveform.{caller,agent}`.
 *
 * This was 32767 — an int16 ceiling taken from the old fixture generator,
 * which filled the buckets with raw i16 magnitudes. The service does not:
 * `api/src/analysis/waveform.rs` divides every sample by `i16::MAX` (or
 * `i32::MAX`, or takes the float sample as-is) before bucketing, and its
 * doc comment says so — "peak absolute sample value in [0, 1]".
 *
 * Dividing an already-normalised envelope by 32767 again collapsed every
 * bar to zero, so against the real service both lanes silently fell back to
 * drawing turn blocks and no call ever showed a waveform.
 */
export const MAX_AMPLITUDE = 1;

export const BAR_WIDTH_PX = 2;
export const BAR_GAP_PX = 1;

/** How many bar slots fit across a plot of this width. */
export function bucketCount(
  plotWidth: number,
  barWidth: number = BAR_WIDTH_PX,
  gap: number = BAR_GAP_PX
): number {
  if (plotWidth <= 0) return 0;
  return Math.max(0, Math.floor(plotWidth / (barWidth + gap)));
}

/**
 * Aggregates `values` (raw peak magnitudes) into exactly `buckets` groups by
 * taking the max magnitude within each group, then normalizes to [0, 1].
 */
export function downsamplePeaks(
  values: number[],
  buckets: number,
  maxAmplitude: number = MAX_AMPLITUDE
): number[] {
  if (buckets <= 0) return [];
  if (!values || values.length === 0) return new Array(buckets).fill(0);

  const out = new Array(buckets).fill(0);
  const step = values.length / buckets;
  const safeMax = maxAmplitude > 0 ? maxAmplitude : 1;

  for (let i = 0; i < buckets; i++) {
    const start = Math.floor(i * step);
    const end = Math.max(start + 1, Math.floor((i + 1) * step));
    let peak = 0;
    for (let j = start; j < end && j < values.length; j++) {
      const mag = Math.abs(values[j]);
      if (mag > peak) peak = mag;
    }
    out[i] = Math.min(1, peak / safeMax);
  }
  return out;
}
