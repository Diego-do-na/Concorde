import { describe, it, expect } from 'vitest'
import { bucketCount, downsamplePeaks, MAX_AMPLITUDE, BAR_WIDTH_PX, BAR_GAP_PX } from '../peakEnvelope'

describe('bucketCount', () => {
  it('fits N bar slots across the plot width', () => {
    const plotWidth = 300
    const n = bucketCount(plotWidth)
    expect(n).toBe(Math.floor(plotWidth / (BAR_WIDTH_PX + BAR_GAP_PX)))
  })

  it('returns 0 for a non-positive width', () => {
    expect(bucketCount(0)).toBe(0)
    expect(bucketCount(-10)).toBe(0)
  })
})

describe('downsamplePeaks', () => {
  it('aggregates raw values into exactly `buckets` groups, normalized to [0,1]', () => {
    // Envelope values arrive already in [0, 1] — api/src/analysis/waveform.rs
    // divides by i16::MAX before bucketing. This fixture used to ramp 0..99,
    // raw int16 magnitudes, which is not a shape the service ever sends.
    const raw = Array.from({ length: 100 }, (_, i) => i / 100)
    const out = downsamplePeaks(raw, 10)
    expect(out).toHaveLength(10)
    for (const v of out) {
      expect(v).toBeGreaterThanOrEqual(0)
      expect(v).toBeLessThanOrEqual(1)
    }
    // last group holds the largest raw magnitudes -> highest normalized peak
    expect(out[9]).toBeGreaterThan(out[0])
  })

  it('normalizes against MAX_AMPLITUDE by default', () => {
    const out = downsamplePeaks([MAX_AMPLITUDE], 1)
    expect(out[0]).toBe(1)
  })

  it('treats full scale as 1.0, matching the service envelope contract', () => {
    // Regression: MAX_AMPLITUDE was 32767, so a real 0.8-amplitude bucket
    // normalised to 0.0000244 and every lane rendered flat.
    expect(MAX_AMPLITUDE).toBe(1)
    expect(downsamplePeaks([0.8], 1)[0]).toBeCloseTo(0.8, 5)
  })

  it('returns zero-filled buckets when values are empty, never blank', () => {
    expect(downsamplePeaks([], 5)).toEqual([0, 0, 0, 0, 0])
  })

  it('returns an empty array when buckets <= 0', () => {
    expect(downsamplePeaks([1, 2, 3], 0)).toEqual([])
  })
})
