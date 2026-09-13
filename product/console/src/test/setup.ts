/*
 * Vitest setup — runs once per test file, before any test.
 *
 * Every component test in this package used to fail with `document is not
 * defined`: vitest defaults to the `node` environment and vite.config carried
 * no `test` block, so @testing-library had no DOM to render into. The
 * environment is jsdom now (vite.config.mts); this file supplies the pieces
 * jsdom does not implement but the console depends on.
 */

// jest-dom matchers (`toBeInTheDocument`, `toHaveTextContent`, …). Tests
// already call these; without this import they resolve to undefined and the
// assertion throws something unrelated to what it was checking.
import '@testing-library/jest-dom';

// Note on fetch: nothing is polyfilled here. Node's native fetch rejects the
// relative URLs lib/api.ts uses by design ("Failed to parse URL from
// /analyze"), and msw's interceptor hits the same wall before any polyfill
// could help. The fix lives in vite.config.mts instead: the jsdom origin and
// VITE_API_BASE are pinned to the same value, so api.ts builds absolute URLs
// under test while staying relative in the browser.

// ── canvas ─────────────────────────────────────────────────────────────────
// jsdom has no 2D context and logs a loud "Not implemented" for every call.
// A no-op stub is the right answer rather than pulling in the native `canvas`
// package: the drawing code is already covered by unit tests over the pure
// functions in waveform/drawLane.ts and trace/drawTrace.ts, and no rendering
// test here inspects pixels.
const noopContext = new Proxy(
  {
    canvas: null,
    // the handful of properties drawing code reads back rather than calls
    measureText: () => ({ width: 0 }),
    getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    createLinearGradient: () => ({ addColorStop() {} }),
  },
  {
    get(target, prop) {
      if (prop in target) return (target as never)[prop];
      return () => undefined;
    },
    set() {
      return true;
    },
  },
);

HTMLCanvasElement.prototype.getContext = (() => noopContext) as never;

// ── ResizeObserver ─────────────────────────────────────────────────────────
// useElementWidth observes the plot container on mount.
if (!('ResizeObserver' in globalThis)) {
  (globalThis as never as { ResizeObserver: unknown }).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}
