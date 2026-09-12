import React from 'react'
import { mmss, pct, INK } from '../../../theme/tokens'

export type ScrubOverlayProps = {
  x: number
  t: number
  confidence: number | null
  panelHeight: number
}

/** Playhead line + floating box (t, interpolated confidence) tracking the hover position. */
export default function ScrubOverlay({ x, t, confidence, panelHeight }: ScrubOverlayProps) {
  return (
    <div
      className="waveform-scrub-overlay"
      style={{ position: 'absolute', top: 0, left: 0, width: '100%', height: panelHeight, pointerEvents: 'none' }}
    >
      <div
        className="waveform-playhead"
        style={{
          position: 'absolute',
          top: 0,
          left: x,
          width: 1,
          height: panelHeight,
          background: INK.PLAYHEAD
        }}
      />
      <div
        className="waveform-scrub-box"
        style={{
          position: 'absolute',
          top: 0,
          left: x,
          transform: 'translateX(6px)',
          fontFamily: 'IBM Plex Mono, monospace',
          fontSize: 11,
          whiteSpace: 'nowrap',
          color: INK.INK,
          background: 'oklch(0.28 0.016 252)',
          border: '1px solid oklch(0.4 0.016 252)',
          borderRadius: 2,
          padding: '2px 6px'
        }}
      >
        <span>{mmss(t)}</span>
        {confidence !== null && <span style={{ marginLeft: 8 }}>P(SYN) {pct(confidence)}</span>}
      </div>
    </div>
  )
}
