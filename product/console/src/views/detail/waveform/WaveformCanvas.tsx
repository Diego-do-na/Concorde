import React, { useEffect, useRef } from 'react'
import type { TimeScale } from './TimeScale'
import { drawLane, type Interval } from './drawLane'

export type WaveformCanvasProps = {
  width: number
  height: number
  labelGutter: number
  scale: TimeScale
  peaks: number[] | null
  turns: Interval[]
  tint?: { wash: string } | null
}

/** One devicePixelRatio-aware canvas lane; redraws whenever its inputs change. */
export default function WaveformCanvas({
  width,
  height,
  labelGutter,
  scale,
  peaks,
  turns,
  tint
}: WaveformCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas || width <= 0 || height <= 0) return

    const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1
    canvas.width = Math.max(1, Math.round(width * dpr))
    canvas.height = Math.max(1, Math.round(height * dpr))
    canvas.style.width = `${width}px`
    canvas.style.height = `${height}px`

    const ctx = canvas.getContext('2d')
    if (!ctx) return
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0)

    drawLane(ctx, { width, height, labelGutter, scale, peaks, turns, tint })
  }, [width, height, labelGutter, scale, peaks, turns, tint])

  return <canvas ref={canvasRef} className="waveform-lane-canvas" />
}
