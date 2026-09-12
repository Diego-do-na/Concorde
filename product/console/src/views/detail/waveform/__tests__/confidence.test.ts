import { describe, it, expect } from 'vitest'
import { interpolateConfidence } from '../confidence'

describe('interpolateConfidence', () => {
  const timeline = [
    { t: 0, confidence: 0.2 },
    { t: 2, confidence: 0.4 },
    { t: 4, confidence: 0.9 }
  ]

  it('returns null for an empty/missing timeline', () => {
    expect(interpolateConfidence([], 1)).toBeNull()
    expect(interpolateConfidence(undefined, 1)).toBeNull()
    expect(interpolateConfidence(null, 1)).toBeNull()
  })

  it('linearly interpolates between the two bracketing points', () => {
    expect(interpolateConfidence(timeline, 1)).toBeCloseTo(0.3, 5)
    expect(interpolateConfidence(timeline, 3)).toBeCloseTo(0.65, 5)
  })

  it('returns the exact value at a known point', () => {
    expect(interpolateConfidence(timeline, 2)).toBeCloseTo(0.4, 5)
  })

  it('clamps to the first/last value outside the timeline range', () => {
    expect(interpolateConfidence(timeline, -5)).toBe(0.2)
    expect(interpolateConfidence(timeline, 100)).toBe(0.9)
  })
})
