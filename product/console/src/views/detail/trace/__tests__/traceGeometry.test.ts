import { describe, it, expect } from 'vitest'
import { createTimeScale } from '../../waveform/TimeScale'
import { mapTimelinePoints, yFor, visiblePoints, TRACE_HEIGHT } from '../traceGeometry'

function makeTimeline(n: number, durationS: number) {
  return Array.from({ length: n }, (_, i) => ({
    t: (i / (n - 1)) * durationS,
    confidence: i / (n - 1)
  }))
}

describe('mapTimelinePoints', () => {
  it('maps 17 timeline points to 17 x positions matching scale.xFor(t)', () => {
    const scale = createTimeScale(10, 500)
    const timeline = makeTimeline(17, 10)
    const points = mapTimelinePoints(timeline, scale)
    expect(points).toHaveLength(17)
    points.forEach((p, i) => {
      expect(p.x).toBe(scale.xFor(timeline[i].t))
    })
  })

  it('the last mapped point carries the same confidence as the last timeline entry (verdict.confidence label)', () => {
    const scale = createTimeScale(10, 500)
    const timeline = makeTimeline(17, 10)
    const verdictConfidence = timeline[timeline.length - 1].confidence
    const points = mapTimelinePoints(timeline, scale)
    expect(points[points.length - 1].confidence).toBe(verdictConfidence)
  })

  it('returns an empty array when timeline is absent', () => {
    const scale = createTimeScale(10, 500)
    expect(mapTimelinePoints(null, scale)).toEqual([])
    expect(mapTimelinePoints(undefined, scale)).toEqual([])
  })
})

describe('yFor', () => {
  it('places the threshold line at the right y: higher confidence draws nearer the top', () => {
    const yHigh = yFor(1, TRACE_HEIGHT)
    const yLow = yFor(0, TRACE_HEIGHT)
    const yMid = yFor(0.5, TRACE_HEIGHT)
    expect(yHigh).toBeLessThan(yMid)
    expect(yMid).toBeLessThan(yLow)
  })

  it('clamps out-of-range confidence into [padding, height - padding]', () => {
    expect(yFor(-1, 84, 8)).toBe(84 - 8)
    expect(yFor(2, 84, 8)).toBe(8)
  })

  it('a threshold of 0.7 sits at the same y regardless of caller (deterministic mapping)', () => {
    expect(yFor(0.7, 84, 8)).toBe(yFor(0.7, 84, 8))
  })
})

describe('visiblePoints (reveal animation prefix)', () => {
  const scale = createTimeScale(10, 500)
  const points = mapTimelinePoints(makeTimeline(10, 10), scale)

  it('returns the full series at progress >= 1 (reduced-motion / animation complete)', () => {
    expect(visiblePoints(points, 1)).toHaveLength(10)
    expect(visiblePoints(points, 1.5)).toHaveLength(10)
  })

  it('returns only the first point at progress <= 0', () => {
    expect(visiblePoints(points, 0)).toHaveLength(1)
  })

  it('returns a growing prefix as progress increases', () => {
    const half = visiblePoints(points, 0.5)
    expect(half.length).toBeGreaterThan(1)
    expect(half.length).toBeLessThan(10)
  })
})
