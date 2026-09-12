import { describe, it, expect } from 'vitest'
import { createTimeScale } from '../TimeScale'
import { drawLane, drawWaveformBars, drawTurnBlocks, drawTurnTint, type CanvasLike } from '../drawLane'

function createMockCtx(): CanvasLike & { calls: { fillRect: unknown[][]; clearRect: unknown[][] } } {
  return {
    fillStyle: '',
    globalAlpha: 1,
    calls: { fillRect: [], clearRect: [] },
    fillRect(...args: unknown[]) {
      this.calls.fillRect.push(args)
    },
    clearRect(...args: unknown[]) {
      this.calls.clearRect.push(args)
    }
  } as any
}

describe('drawWaveformBars', () => {
  it('draws exactly N buckets (one fillRect per peak) across the width', () => {
    const ctx = createMockCtx()
    const peaks = new Array(37).fill(0.5)
    drawWaveformBars(ctx, { width: 500, height: 66, labelGutter: 84, peaks })
    expect(ctx.calls.fillRect).toHaveLength(37)
  })

  it('keeps every bar within the plot area (never draws left of labelGutter)', () => {
    const ctx = createMockCtx()
    const peaks = new Array(10).fill(1)
    drawWaveformBars(ctx, { width: 500, height: 66, labelGutter: 84, peaks })
    for (const [x] of ctx.calls.fillRect as [number, number, number, number][]) {
      expect(x).toBeGreaterThanOrEqual(84)
      expect(x).toBeLessThanOrEqual(500)
    }
  })

  it('draws nothing for an empty peaks array', () => {
    const ctx = createMockCtx()
    drawWaveformBars(ctx, { width: 500, height: 66, labelGutter: 84, peaks: [] })
    expect(ctx.calls.fillRect).toHaveLength(0)
  })
})

describe('drawTurnBlocks (waveform-absent fallback)', () => {
  it('renders one block per turn so the lane never blanks', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const turns: [number, number][] = [
      [0, 1],
      [2, 3],
      [4, 5]
    ]
    drawTurnBlocks(ctx, { height: 66, scale, turns })
    expect(ctx.calls.fillRect).toHaveLength(3)
  })

  it('draws zero blocks (but does not throw) when there are no turns', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    expect(() => drawTurnBlocks(ctx, { height: 66, scale, turns: [] })).not.toThrow()
    expect(ctx.calls.fillRect).toHaveLength(0)
  })
})

describe('drawLane orchestration', () => {
  it('renders bars when peaks are present', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const peaks = new Array(20).fill(0.3)
    drawLane(ctx, { width: 500, height: 66, labelGutter: 84, scale, peaks, turns: [[0, 1]] })
    expect(ctx.calls.clearRect).toHaveLength(1)
    expect(ctx.calls.fillRect).toHaveLength(20)
  })

  it('falls back to turn blocks when waveform is absent (peaks null)', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const turns: [number, number][] = [
      [0, 1],
      [2, 3]
    ]
    drawLane(ctx, { width: 500, height: 66, labelGutter: 84, scale, peaks: null, turns })
    expect(ctx.calls.fillRect).toHaveLength(2)
  })

  it('falls back to turn blocks when waveform is an empty array', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    drawLane(ctx, { width: 500, height: 66, labelGutter: 84, scale, peaks: [], turns: [[0, 1]] })
    expect(ctx.calls.fillRect).toHaveLength(1)
  })

  it('tints caller turns with the verdict wash on top of the bars', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    const peaks = new Array(5).fill(0.5)
    const turns: [number, number][] = [[0, 1], [2, 3]]
    drawLane(ctx, { width: 500, height: 66, labelGutter: 84, scale, peaks, turns, tint: { wash: 'oklch(0.27 0.045 24)' } })
    // 5 bars + 2 tint overlays
    expect(ctx.calls.fillRect).toHaveLength(7)
  })
})

describe('drawTurnTint', () => {
  it('resets globalAlpha after drawing', () => {
    const ctx = createMockCtx()
    ctx.globalAlpha = 1
    const scale = createTimeScale(10, 500)
    drawTurnTint(ctx, { height: 66, scale, turns: [[0, 1]], wash: 'oklch(0.27 0.045 24)' })
    expect(ctx.globalAlpha).toBe(1)
  })

  it('is a no-op for an empty turns list', () => {
    const ctx = createMockCtx()
    const scale = createTimeScale(10, 500)
    drawTurnTint(ctx, { height: 66, scale, turns: [], wash: 'oklch(0.27 0.045 24)' })
    expect(ctx.calls.fillRect).toHaveLength(0)
  })
})
