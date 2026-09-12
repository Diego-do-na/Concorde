import React, { useMemo, useState, useCallback } from 'react'
import type { Analysis } from '../../../lib/api'
import { verdictOf, toneFor, SPACING } from '../../../theme/tokens'
import { createTimeScale, LABEL_GUTTER_PX } from './TimeScale'
import { bucketCount, downsamplePeaks } from './peakEnvelope'
import { interpolateConfidence } from './confidence'
import { useElementWidth } from './useElementWidth'
import WaveformCanvas from './WaveformCanvas'
import TimeAxis from './TimeAxis'
import ScrubOverlay from './ScrubOverlay'
import './waveform.css'

const LANE_HEIGHT = parseInt(SPACING.WAVEFORM_LANE, 10) || 66
const FALLBACK_WIDTH = 640

export type ScrubState = { t: number; confidence: number | null }

export type DualChannelWaveformProps = {
  analysis: Analysis
  /** T033 wires up here to keep its own playhead in sync with this panel's hover. */
  onScrub?: (state: ScrubState | null) => void
}

/** Dual-channel synchronized waveform: CH0 CALLER / CH1 AGENT lanes on one shared TimeScale. */
export default function DualChannelWaveform({ analysis, onScrub }: DualChannelWaveformProps) {
  const [containerRef, measuredWidth] = useElementWidth<HTMLDivElement>()
  const width = measuredWidth || FALLBACK_WIDTH

  const durationS = analysis.meta?.duration_s ?? 0
  const scale = useMemo(() => createTimeScale(durationS, width, LABEL_GUTTER_PX), [durationS, width])

  const tone = useMemo(
    () => toneFor(verdictOf(analysis.verdict.is_synthetic, analysis.verdict.confidence)),
    [analysis.verdict.is_synthetic, analysis.verdict.confidence]
  )

  const buckets = bucketCount(scale.plotWidth)
  const callerPeaks = analysis.waveform ? downsamplePeaks(analysis.waveform.caller, buckets) : null
  const agentPeaks = analysis.waveform ? downsamplePeaks(analysis.waveform.agent, buckets) : null

  const [hover, setHover] = useState<{ x: number; t: number } | null>(null)

  const handleMove = useCallback(
    (e: React.MouseEvent<HTMLDivElement>) => {
      const rect = e.currentTarget.getBoundingClientRect()
      const localX = e.clientX - rect.left
      const t = scale.tFor(localX)
      const x = scale.xFor(t)
      setHover({ x, t })
      onScrub?.({ t, confidence: interpolateConfidence(analysis.timeline, t) })
    },
    [scale, analysis.timeline, onScrub]
  )

  const handleLeave = useCallback(() => {
    setHover(null)
    onScrub?.(null)
  }, [onScrub])

  const panelHeight = LANE_HEIGHT * 2
  const hoverConfidence = hover ? interpolateConfidence(analysis.timeline, hover.t) : null

  return (
    <div ref={containerRef} className="waveform-panel" onMouseMove={handleMove} onMouseLeave={handleLeave}>
      <div className="waveform-lane-row">
        <WaveformCanvas
          width={scale.width}
          height={LANE_HEIGHT}
          labelGutter={scale.labelGutter}
          scale={scale}
          peaks={callerPeaks}
          turns={analysis.turns.caller}
          tint={tone}
        />
        <div className="waveform-lane-label">CH0 CALLER</div>
      </div>
      <div className="waveform-lane-row">
        <WaveformCanvas
          width={scale.width}
          height={LANE_HEIGHT}
          labelGutter={scale.labelGutter}
          scale={scale}
          peaks={agentPeaks}
          turns={analysis.turns.agent}
          tint={null}
        />
        <div className="waveform-lane-label">CH1 AGENT</div>
      </div>
      <TimeAxis scale={scale} />
      {hover && <ScrubOverlay x={hover.x} t={hover.t} confidence={hoverConfidence} panelHeight={panelHeight} />}
    </div>
  )
}
