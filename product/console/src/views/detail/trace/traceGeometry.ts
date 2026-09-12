// Confidence-over-time trace geometry: maps analysis.timeline onto the shared
// TimeScale (from T031's waveform/TimeScale.ts) plus this lane's own y-axis
// (confidence [0,1] -> px row). Pure/no DOM so it is trivially unit-testable.

import type { TimeScale } from '../waveform/TimeScale';
import type { TimelinePoint } from '../waveform/confidence';

export const TRACE_HEIGHT = 84;
export const TRACE_PADDING_Y = 8;

export type TracePoint = { x: number; y: number; t: number; confidence: number };

/** Confidence [0,1] -> px row; 1 (fully synthetic) draws near the top of the lane. */
export function yFor(
  confidence: number,
  height: number = TRACE_HEIGHT,
  padding: number = TRACE_PADDING_Y
): number {
  const clamped = Math.min(1, Math.max(0, confidence));
  const usable = Math.max(0, height - padding * 2);
  return padding + (1 - clamped) * usable;
}

/** Maps every analysis.timeline point onto this lane's (x, y) using the shared TimeScale. */
export function mapTimelinePoints(
  timeline: TimelinePoint[] | undefined | null,
  scale: TimeScale,
  height: number = TRACE_HEIGHT,
  padding: number = TRACE_PADDING_Y
): TracePoint[] {
  if (!timeline) return [];
  return timeline.map((p) => ({
    x: scale.xFor(p.t),
    y: yFor(p.confidence, height, padding),
    t: p.t,
    confidence: p.confidence
  }));
}

/** Linear reveal: the prefix of `points` visible at animation `progress` in [0,1]. */
export function visiblePoints(points: TracePoint[], progress: number): TracePoint[] {
  if (points.length === 0) return points;
  if (progress >= 1) return points;
  if (progress <= 0) return points.slice(0, 1);
  const count = Math.max(1, Math.ceil(points.length * progress));
  return points.slice(0, count);
}
