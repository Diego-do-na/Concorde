import { describe, it, expect } from 'vitest'
import { createTimeScale, LABEL_GUTTER_PX } from '../TimeScale'

describe('TimeScale', () => {
  it('xFor is linear across the plot area', () => {
    const scale = createTimeScale(10, 500)
    expect(scale.labelGutter).toBe(LABEL_GUTTER_PX)
    expect(scale.plotWidth).toBe(500 - LABEL_GUTTER_PX)

    expect(scale.xFor(0)).toBe(LABEL_GUTTER_PX)
    expect(scale.xFor(10)).toBe(500)
    expect(scale.xFor(5)).toBeCloseTo(LABEL_GUTTER_PX + scale.plotWidth / 2, 5)
    expect(scale.xFor(2.5)).toBeCloseTo(LABEL_GUTTER_PX + scale.plotWidth / 4, 5)
  })

  it('xFor clamps to the plot bounds for out-of-range t', () => {
    const scale = createTimeScale(10, 500)
    expect(scale.xFor(-5)).toBe(LABEL_GUTTER_PX)
    expect(scale.xFor(1000)).toBe(500)
  })

  it('tFor is the inverse of xFor and clamps to [0, duration]', () => {
    const scale = createTimeScale(10, 500)
    expect(scale.tFor(LABEL_GUTTER_PX)).toBe(0)
    expect(scale.tFor(500)).toBe(10)
    expect(scale.tFor(scale.xFor(3.3))).toBeCloseTo(3.3, 5)

    // clamped even for pixels outside the plot area
    expect(scale.tFor(0)).toBe(0)
    expect(scale.tFor(10_000)).toBe(10)
  })

  it('handles a zero-width plot without dividing by zero', () => {
    const scale = createTimeScale(10, LABEL_GUTTER_PX)
    expect(scale.plotWidth).toBe(0)
    expect(scale.xFor(5)).toBe(LABEL_GUTTER_PX)
    expect(scale.tFor(LABEL_GUTTER_PX)).toBe(0)
  })
})
