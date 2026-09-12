import React from 'react'
import type { TimeScale } from './TimeScale'
import { mmss } from '../../../theme/tokens'

const FRACTIONS = [0, 0.25, 0.5, 0.75, 1] as const

export type TimeAxisProps = {
  scale: TimeScale
}

/** 0 / ¼ / ½ / ¾ / end labels in m:ss under the plot, aligned to the shared TimeScale. */
export default function TimeAxis({ scale }: TimeAxisProps) {
  return (
    <div className="waveform-time-axis" style={{ position: 'relative', height: 18, width: scale.width }}>
      {FRACTIONS.map((frac) => {
        const t = scale.durationS * frac
        const x = scale.xFor(t)
        const align = frac === 0 ? 'left' : frac === 1 ? 'right' : 'center'
        const translate = frac === 0 ? '0' : frac === 1 ? '-100%' : '-50%'
        return (
          <span
            key={frac}
            className="waveform-time-axis-label"
            style={{
              position: 'absolute',
              left: x,
              transform: `translateX(${translate})`,
              textAlign: align,
              fontFamily: 'IBM Plex Mono, monospace',
              fontSize: 11
            }}
          >
            {mmss(t)}
          </span>
        )
      })}
    </div>
  )
}
