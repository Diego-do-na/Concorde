import type { TimeScale } from './TimeScale'

// Fixed appearance values from the T031 spec — not yet promoted to the shared
// token table, so pinned here as the single source for this view.
export const BASELINE_COLOR = 'oklch(0.62 0.03 252)'
export const LANE_BORDER_COLOR = 'oklch(0.25 0.015 252)'

export type Interval = [number, number]

/** Minimal duck-typed surface so tests can pass a plain object instead of a real canvas context. */
export type CanvasLike = Pick<
  CanvasRenderingContext2D,
  'clearRect' | 'fillRect' | 'fillStyle' | 'globalAlpha'
>

export type DrawBarsOptions = {
  width: number
  height: number
  labelGutter: number
  /** Normalized [0,1] peak per bucket; length is the bucket count drawn. */
  peaks: number[]
  color?: string
}

/** Draws exactly `peaks.length` mirrored bars (above+below the mid baseline) across the plot area. */
export function drawWaveformBars(ctx: CanvasLike, opts: DrawBarsOptions): void {
  const { width, height, labelGutter, peaks, color = BASELINE_COLOR } = opts
  const plotWidth = Math.max(0, width - labelGutter)
  if (peaks.length === 0 || plotWidth === 0) return

  const midY = height / 2
  const barSlot = plotWidth / peaks.length
  const barWidth = Math.max(1, barSlot - 1)
  ctx.fillStyle = color

  for (let i = 0; i < peaks.length; i++) {
    const amp = Math.min(1, Math.max(0, peaks[i]))
    const barHalf = amp * (midY - 2)
    const x = labelGutter + i * barSlot
    ctx.fillRect(x, midY - barHalf, barWidth, Math.max(1, barHalf * 2))
  }
}

export type DrawTurnBlocksOptions = {
  height: number
  scale: TimeScale
  turns: Interval[]
  color?: string
  inset?: number
}

/** Fallback rendering when waveform samples are absent — draws one block per turn so the lane never blanks. */
export function drawTurnBlocks(ctx: CanvasLike, opts: DrawTurnBlocksOptions): void {
  const { height, scale, turns, color = BASELINE_COLOR, inset = 4 } = opts
  ctx.fillStyle = color
  for (const [start, end] of turns) {
    const x0 = scale.xFor(start)
    const x1 = scale.xFor(end)
    const w = Math.max(1, x1 - x0)
    ctx.fillRect(x0, inset, w, Math.max(1, height - inset * 2))
  }
}

export type DrawTurnTintOptions = {
  height: number
  scale: TimeScale
  turns: Interval[]
  wash: string
  alpha?: number
}

/** Overlays the verdict-tone wash on the caller lane's speaking turns. */
export function drawTurnTint(ctx: CanvasLike, opts: DrawTurnTintOptions): void {
  const { height, scale, turns, wash, alpha = 0.35 } = opts
  if (turns.length === 0) return
  const prevAlpha = ctx.globalAlpha
  ctx.fillStyle = wash
  ctx.globalAlpha = alpha
  for (const [start, end] of turns) {
    const x0 = scale.xFor(start)
    const x1 = scale.xFor(end)
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), height)
  }
  ctx.globalAlpha = prevAlpha
}

export type DrawLaneOptions = {
  width: number
  height: number
  labelGutter: number
  scale: TimeScale
  /** Downsampled+normalized peaks, or null/undefined when analysis.waveform is absent. */
  peaks: number[] | null | undefined
  turns: Interval[]
  /** Verdict-tone wash applied over turns; only meaningful on the caller lane. */
  tint?: { wash: string } | null
}

/**
 * Orchestrates one lane's draw: peak-envelope bars (+ optional turn tint) when
 * waveform samples exist, otherwise turn-interval blocks so the view never blanks.
 */
export function drawLane(ctx: CanvasLike, opts: DrawLaneOptions): void {
  const { width, height, labelGutter, scale, peaks, turns, tint } = opts
  ctx.clearRect(0, 0, width, height)

  if (peaks && peaks.length > 0) {
    drawWaveformBars(ctx, { width, height, labelGutter, peaks })
    if (tint) {
      drawTurnTint(ctx, { height, scale, turns, wash: tint.wash })
    }
  } else {
    drawTurnBlocks(ctx, { height, scale, turns })
  }
}
