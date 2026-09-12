import type { TracePoint } from './traceGeometry';

// Fixed appearance values, following the same "pinned here, not yet promoted
// to the shared token table" convention as waveform/drawLane.ts.
export const THRESHOLD_DASH: [number, number] = [6, 3];
export const LINE_WIDTH = 1.5;
export const FINAL_DOT_RADIUS = 3;

/** Minimal duck-typed surface so tests can pass a plain object instead of a real canvas context. */
export type CanvasLike = Pick<
  CanvasRenderingContext2D,
  | 'clearRect'
  | 'fillRect'
  | 'fillStyle'
  | 'strokeStyle'
  | 'lineWidth'
  | 'globalAlpha'
  | 'beginPath'
  | 'moveTo'
  | 'lineTo'
  | 'stroke'
  | 'setLineDash'
  | 'arc'
  | 'fill'
>;

export type DrawTraceOptions = {
  width: number;
  height: number;
  labelGutter: number;
  /** Already mapped to (x, y) via mapTimelinePoints; may be a reveal-animation prefix. */
  points: TracePoint[];
  /** The true final point of the full (unanimated) series — its marker is always static. */
  finalPoint: TracePoint | null;
  thresholdY: number;
  toneColor: string;
  redWash: string;
  greenWash: string;
  mutedColor: string;
};

/**
 * Draws the P(SYN) trace lane: a red/green background split at the threshold
 * row, a dashed threshold line, the piecewise-linear confidence path (which
 * may be a reveal-animation prefix of the full series), and a static
 * verdict-tone dot at the series' true final point.
 */
export function drawTrace(ctx: CanvasLike, opts: DrawTraceOptions): void {
  const { width, height, labelGutter, points, finalPoint, thresholdY, toneColor, redWash, greenWash, mutedColor } =
    opts;
  const plotWidth = Math.max(0, width - labelGutter);

  ctx.clearRect(0, 0, width, height);
  if (plotWidth === 0) return;

  ctx.fillStyle = redWash;
  ctx.fillRect(labelGutter, 0, plotWidth, thresholdY);
  ctx.fillStyle = greenWash;
  ctx.fillRect(labelGutter, thresholdY, plotWidth, Math.max(0, height - thresholdY));

  ctx.strokeStyle = mutedColor;
  ctx.lineWidth = 1;
  ctx.setLineDash(THRESHOLD_DASH);
  ctx.beginPath();
  ctx.moveTo(labelGutter, thresholdY);
  ctx.lineTo(width, thresholdY);
  ctx.stroke();
  ctx.setLineDash([]);

  if (points.length > 0) {
    ctx.strokeStyle = toneColor;
    ctx.lineWidth = LINE_WIDTH;
    ctx.beginPath();
    ctx.moveTo(points[0].x, points[0].y);
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(points[i].x, points[i].y);
    }
    ctx.stroke();
  }

  if (finalPoint) {
    ctx.fillStyle = toneColor;
    ctx.beginPath();
    ctx.arc(finalPoint.x, finalPoint.y, FINAL_DOT_RADIUS, 0, Math.PI * 2);
    ctx.fill();
  }
}
