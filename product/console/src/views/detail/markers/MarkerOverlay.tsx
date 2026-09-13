import React from "react";
import { TimeScale } from "../waveform/TimeScale";
import "./markers.css";

export type MarkerEvent = { type: "overlap" | "interruption" | "silence"; t: number; duration?: number };

/**
 * Vertical event markers, drawn over the waveform lanes on the shared
 * `TimeScale` so a marker lands on the same pixel as the audio that caused
 * it.
 *
 * This is an absolutely-positioned layer and must be mounted inside a
 * `position: relative` plot container. The root used to be
 * `position: relative; height: 0` with children stretched `top: 0; bottom: 0`
 * — a zero-height box, so every marker had zero height and nothing was ever
 * visible on screen despite the tests passing on `style.left`.
 */
export default function MarkerOverlay({ events, timeScale }: { events: MarkerEvent[]; timeScale: TimeScale }) {
  return (
    <div className="marker-overlay" data-testid="markers-root">
      {events.map((e, i) => (
        <div
          key={`${e.type}-${e.t}-${i}`}
          data-testid="marker"
          data-type={e.type}
          className={`marker ${e.type}`}
          style={{ left: `${timeScale.xFor(e.t)}px` }}
        >
          <span
            data-testid={`glyph-${e.type}-${i}`}
            className={`legend-glyph ${e.type} marker-glyph`}
            aria-hidden="true"
          />
        </div>
      ))}
    </div>
  );
}
