// Linear interpolation of P(SYN) over analysis.timeline, used by the hover
// scrub box here and shared with the T033 confidence trace lane.

export type TimelinePoint = { t: number; confidence: number };

export function interpolateConfidence(
  timeline: TimelinePoint[] | undefined | null,
  t: number
): number | null {
  if (!timeline || timeline.length === 0) return null;
  if (timeline.length === 1) return timeline[0].confidence;

  const sorted = timeline;
  if (t <= sorted[0].t) return sorted[0].confidence;
  const last = sorted[sorted.length - 1];
  if (t >= last.t) return last.confidence;

  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i];
    const b = sorted[i + 1];
    if (t >= a.t && t <= b.t) {
      const span = b.t - a.t;
      if (span <= 0) return a.confidence;
      const frac = (t - a.t) / span;
      return a.confidence + (b.confidence - a.confidence) * frac;
    }
  }
  return last.confidence;
}
