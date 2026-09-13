import React from "react";
import { MarkerEvent } from "./MarkerOverlay";
import "./markers.css";

const TYPES = [
  { type: "overlap", label: "Overlap" },
  { type: "interruption", label: "Interruption" },
  { type: "silence", label: "Silence > 2s" },
] as const;

/** Counts per event type, shown above the plot the markers are drawn on. */
export default function Legend({ events }: { events: MarkerEvent[] }) {
  const counts = { overlap: 0, interruption: 0, silence: 0 };
  for (const e of events ?? []) {
    if (e.type in counts) counts[e.type as keyof typeof counts]++;
  }

  return (
    <div className="legend">
      {TYPES.map(({ type, label }) => (
        <div className="legend-item" data-testid={`legend-${type}`} key={type}>
          <span className={`legend-glyph ${type}`} aria-hidden="true" />
          <span>{label}</span>
          <span className="legend-count">{counts[type]}</span>
        </div>
      ))}
    </div>
  );
}
