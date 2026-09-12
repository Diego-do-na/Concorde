import { describe, it, expect } from 'vitest'
import { createTimeScale } from '../../waveform/TimeScale'
import { mapTimelinePoints, yFor } from '../traceGeometry'
import { drawTrace, type CanvasLike } from '../drawTrace'

function createMockCtx(): CanvasLike & {
  calls: { fillRect: unknown[][]; moveTo: unknown[][]; lineTo: unknown[][]; arc: unknown[][]; clearRect: unknown[][] }
} {
  return {
    fillStyle: '',
    strokeStyle: '',
    lineWidth: 1,
    globalAlpha: 1,
    calls: { fillRect: [], moveTo: [], lineTo: [], arc: [], clearRect: [] },
    clearRect(...args: unknown[]) {
      this.calls.clearRect.push(args)
    },
    fillRect(...args: unknown[]) {
      this.calls.fillRect.push(args)
    },
    beginPath() {},
    moveTo(...args: unknown[]) {
      this.calls.moveTo.push(args)
    },
    lineTo(...args: unknown[]) {
      this.calls.lineTo.push(args)
    },
    stroke() {},
    setLineDash() {},
    arc(...args: unknown[]) {
      this.calls.arc.push(args)
    },
    fill() {}
  } as any
}

const TONE = 'oklch(0.66 0.17 24)'
const RED = 'oklch(0.27 0.045 24)'
const GREEN = 'oklch(0.27 0.035 168)'
const MUTED = 'oklch(0.6 0.012 252)'

describe('drawTrace', () => {
  it('draws the threshold line at the right y (moveTo/lineTo share thresholdY)', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const thresholdY = yFor(0.7, 84)
    drawTrace(ctx, {
      width: scale.width,
      height: 84,
      labelGutter: scale.labelGutter,
      points: [],
      finalPoint: null,
      thresholdY,
      toneColor: TONE,
      redWash: RED,
      greenWash: GREEN,
      mutedColor: MUTED
    })
    const [thresholdMove] = ctx.calls.moveTo
    const [thresholdLine] = ctx.calls.lineTo
    expect(thresholdMove[1]).toBe(thresholdY)
    expect(thresholdLine[1]).toBe(thresholdY)
  })

  it('splits the background: red wash above the threshold row, green wash below', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const thresholdY = yFor(0.7, 84)
    drawTrace(ctx, {
      width: scale.width,
      height: 84,
      labelGutter: scale.labelGutter,
      points: [],
      finalPoint: null,
      thresholdY,
      toneColor: TONE,
      redWash: RED,
      greenWash: GREEN,
      mutedColor: MUTED
    })
    const [redRect, greenRect] = ctx.calls.fillRect as [number, number, number, number][]
    expect(redRect[1]).toBe(0)
    expect(redRect[3]).toBe(thresholdY)
    expect(greenRect[1]).toBe(thresholdY)
    expect(greenRect[3]).toBe(84 - thresholdY)
  })

  it('draws one lineTo per extra point and marks the final point with an arc', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const timeline = Array.from({ length: 5 }, (_, i) => ({ t: (i / 4) * 10, confidence: i / 4 }))
    const points = mapTimelinePoints(timeline, scale)
    const finalPoint = points[points.length - 1]
    drawTrace(ctx, {
      width: scale.width,
      height: 84,
      labelGutter: scale.labelGutter,
      points,
      finalPoint,
      thresholdY: yFor(0.7, 84),
      toneColor: TONE,
      redWash: RED,
      greenWash: GREEN,
      mutedColor: MUTED
    })
    // 1 lineTo for the threshold line + 4 for the path (5 points => 1 moveTo + 4 lineTo)
    expect(ctx.calls.lineTo).toHaveLength(1 + 4)
    expect(ctx.calls.arc).toHaveLength(1)
    expect(ctx.calls.arc[0][0]).toBe(finalPoint.x)
    expect(ctx.calls.arc[0][1]).toBe(finalPoint.y)
  })

  it('draws nothing on the empty-width plot beyond the initial clear', () => {
    const ctx = createMockCtx()
    drawTrace(ctx, {
      width: 84,
      height: 84,
      labelGutter: 84,
      points: [],
      finalPoint: null,
      thresholdY: 40,
      toneColor: TONE,
      redWash: RED,
      greenWash: GREEN,
      mutedColor: MUTED
    })
    expect(ctx.calls.clearRect).toHaveLength(1)
    expect(ctx.calls.fillRect).toHaveLength(0)
  })
})
