import React from "react";
import { TimeScale } from "../waveform/TimeScale";

export type MarkerEvent = { type: "overlap" | "interruption" | "silence"; t: number; duration?: number };

export default function MarkerOverlay({ events, timeScale }: { events: MarkerEvent[]; timeScale: TimeScale }) {
  return (
    <div style={{ position: "relative", width: "100%", height: 0, pointerEvents: "none" }} data-testid="markers-root">
      {events.map((e, i) => {
        const left = timeScale.xFor(e.t);
        const common: React.CSSProperties = {
          position: "absolute",
          left: `${left}px`,
          top: 0,
          bottom: 0,
          width: 1,
        };

        const renderGlyph = () => {
          const base: React.CSSProperties = {
            position: "absolute",
            top: 6,
            left: -6,
            width: 12,
            height: 12,
            display: "inline-block",
          };
          if (e.type === "overlap") {
            return <div data-testid={`glyph-overlap-${i}`} style={{ ...base, transform: "rotate(45deg)", background: "oklch(0.95 0.006 252)" }} />;
          }
          if (e.type === "interruption") {
            return <div data-testid={`glyph-interruption-${i}`} style={{ ...base, borderRadius: 999, background: "oklch(0.86 0.01 252)" }} />;
          }
          // silence
          return <div data-testid={`glyph-silence-${i}`} style={{ ...base, border: "1px solid oklch(0.8 0.01 252)", background: "transparent" }} />;
        };

        if (e.type === "overlap") {
          return (
            <div key={i} data-testid="marker" data-type="overlap" style={{ ...common, background: "repeating-linear-gradient(to bottom, oklch(0.95 0.006 252) 0 6px, transparent 6px 9px)" }}>
              <div style={{ position: "absolute", top: 0, left: -6 }}>{renderGlyph()}</div>
            </div>
          );
        }

        if (e.type === "interruption") {
          return (
            <div key={i} data-testid="marker" data-type="interruption" style={{ ...common, background: "oklch(0.86 0.01 252)" }}>
              <div style={{ position: "absolute", top: 0, left: -6 }}>{renderGlyph()}</div>
            </div>
          );
        }

        // silence (>2s)
        return (
          <div key={i} data-testid="marker" data-type="silence" style={{ ...common, background: "repeating-linear-gradient(to bottom, oklch(0.72 0.012 252) 0 3px, transparent 3px 7px)" }}>
            <div style={{ position: "absolute", top: 0, left: -6 }}>{renderGlyph()}</div>
          </div>
        );
      })}
    </div>
  );
}

