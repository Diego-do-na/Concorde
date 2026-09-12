import React, { useEffect, useRef } from 'react';
import { drawTrace } from './drawTrace';
import { visiblePoints, type TracePoint } from './traceGeometry';
import { useRevealProgress } from './reveal';

export type TraceCanvasProps = {
  width: number;
  height: number;
  labelGutter: number;
  points: TracePoint[];
  thresholdY: number;
  toneColor: string;
  redWash: string;
  greenWash: string;
  mutedColor: string;
};

/** devicePixelRatio-aware canvas for the P(SYN) trace lane; animates its draw-in once per mount. */
export default function TraceCanvas({
  width,
  height,
  labelGutter,
  points,
  thresholdY,
  toneColor,
  redWash,
  greenWash,
  mutedColor
}: TraceCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const progress = useRevealProgress();
  const finalPoint = points.length > 0 ? points[points.length - 1] : null;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || width <= 0 || height <= 0) return;

    const dpr = typeof window !== 'undefined' ? window.devicePixelRatio || 1 : 1;
    canvas.width = Math.max(1, Math.round(width * dpr));
    canvas.height = Math.max(1, Math.round(height * dpr));
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;

    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    drawTrace(ctx, {
      width,
      height,
      labelGutter,
      points: visiblePoints(points, progress),
      finalPoint,
      thresholdY,
      toneColor,
      redWash,
      greenWash,
      mutedColor
    });
  }, [width, height, labelGutter, points, finalPoint, thresholdY, toneColor, redWash, greenWash, mutedColor, progress]);

  return <canvas ref={canvasRef} className="trace-lane-canvas" />;
}
