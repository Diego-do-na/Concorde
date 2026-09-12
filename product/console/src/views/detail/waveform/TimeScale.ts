// Shared time <-> pixel mapping for every detail-view overlay (waveform lanes,
// markers T032, confidence trace T033). Pure/framework-agnostic so it can be
// unit-tested without a DOM or canvas.

export const LABEL_GUTTER_PX = 84;

export type TimeScale = {
  durationS: number;
  width: number;
  labelGutter: number;
  plotWidth: number;
  /** seconds -> px, clamped to the plot area (never runs past the canvas). */
  xFor(t: number): number;
  /** px -> seconds, clamped to [0, durationS]. Inverse of xFor. */
  tFor(x: number): number;
};

export function createTimeScale(
  durationS: number,
  width: number,
  labelGutter: number = LABEL_GUTTER_PX
): TimeScale {
  const safeDuration = durationS > 0 ? durationS : 1;
  const plotWidth = Math.max(0, width - labelGutter);

  const xFor = (t: number): number => {
    const clampedT = Math.min(Math.max(t, 0), safeDuration);
    if (plotWidth === 0) return labelGutter;
    return labelGutter + (clampedT / safeDuration) * plotWidth;
  };

  const tFor = (x: number): number => {
    if (plotWidth === 0) return 0;
    const clampedX = Math.min(Math.max(x, labelGutter), labelGutter + plotWidth);
    return ((clampedX - labelGutter) / plotWidth) * safeDuration;
  };

  return { durationS, width, labelGutter, plotWidth, xFor, tFor };
}
