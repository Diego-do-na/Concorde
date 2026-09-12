import React, { useMemo } from 'react';
import type { Analysis } from '../../../lib/api';
import type { ScrubState } from '../waveform/DualChannelWaveform';
import { verdictOf, toneFor, SEMANTIC, INK, pct } from '../../../theme/tokens';
import { createTimeScale, LABEL_GUTTER_PX } from '../waveform/TimeScale';
import { useElementWidth } from '../waveform/useElementWidth';
import { mapTimelinePoints, yFor, TRACE_HEIGHT } from './traceGeometry';
import TraceCanvas from './TraceCanvas';
import './trace.css';

const FALLBACK_WIDTH = 640;

export type ConfidenceTraceProps = {
  analysis: Analysis;
  /** T031's hover callback output — this lane is a follower, not a producer, of scrub state. */
  scrub?: ScrubState | null;
};

/** Third detail-view lane: P(SYN) re-scored on truncated prefixes, aligned to the shared TimeScale. */
export default function ConfidenceTrace({ analysis, scrub }: ConfidenceTraceProps) {
  const [containerRef, measuredWidth] = useElementWidth<HTMLDivElement>();
  const width = measuredWidth || FALLBACK_WIDTH;

  const durationS = analysis.meta?.duration_s ?? 0;
  const scale = useMemo(() => createTimeScale(durationS, width, LABEL_GUTTER_PX), [durationS, width]);

  const tone = useMemo(
    () => toneFor(verdictOf(analysis.verdict.is_synthetic, analysis.verdict.confidence)),
    [analysis.verdict.is_synthetic, analysis.verdict.confidence]
  );

  const points = useMemo(() => mapTimelinePoints(analysis.timeline, scale, TRACE_HEIGHT), [analysis.timeline, scale]);
  const thresholdY = yFor(analysis.verdict.threshold, TRACE_HEIGHT);
  const finalPoint = points.length > 0 ? points[points.length - 1] : null;
  const finalConfidence = finalPoint ? finalPoint.confidence : analysis.verdict.confidence;

  const scrubX = scrub ? scale.xFor(scrub.t) : null;

  return (
    <div ref={containerRef} className="trace-lane-row">
      <TraceCanvas
        width={scale.width}
        height={TRACE_HEIGHT}
        labelGutter={scale.labelGutter}
        points={points}
        thresholdY={thresholdY}
        toneColor={tone.color}
        redWash={SEMANTIC.SYNTHETIC.WASH}
        greenWash={SEMANTIC.VERIFIED.WASH}
        mutedColor={INK.MUTED}
      />
      <div className="trace-lane-label">P(SYN) TRACE</div>
      <div className="trace-final-label" style={{ color: tone.color }}>
        {pct(finalConfidence)}
      </div>
      {scrub && scrubX !== null && (
        <div
          className="trace-scrub-playhead"
          style={{
            position: 'absolute',
            top: 0,
            left: scrubX,
            width: 1,
            height: TRACE_HEIGHT,
            background: INK.PLAYHEAD,
            pointerEvents: 'none'
          }}
        />
      )}
    </div>
  );
}
