// Draw-in reveal for the confidence trace path. Disabled entirely under
// prefers-reduced-motion (the lane just renders at progress=1, no animation).

import { useEffect, useRef, useState } from 'react';

export const REVEAL_DURATION_MS = 300;

export function prefersReducedMotion(): boolean {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

/** The animation's starting progress: 1 (fully drawn, no reveal) when motion is reduced, else 0. */
export function initialProgress(reducedMotion: boolean): number {
  return reducedMotion ? 1 : 0;
}

/** 0 -> 1 linear reveal over durationMs; starts (and stays) at 1 under prefers-reduced-motion. */
export function useRevealProgress(durationMs: number = REVEAL_DURATION_MS): number {
  const reduced = prefersReducedMotion();
  const [progress, setProgress] = useState(() => initialProgress(reduced));
  const rafRef = useRef<number | null>(null);

  useEffect(() => {
    if (reduced) {
      setProgress(1);
      return;
    }
    setProgress(0);
    const start = performance.now();
    const tick = (now: number) => {
      const elapsed = now - start;
      const p = Math.min(1, elapsed / durationMs);
      setProgress(p);
      if (p < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) cancelAnimationFrame(rafRef.current);
    };
  }, [durationMs, reduced]);

  return progress;
}
