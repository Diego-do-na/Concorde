import React from "react";
import { MarkerEvent } from "./MarkerOverlay";
import "./markers.css";

function fmtTime(t: number) {
  const mm = Math.floor(t / 60);
  const ss = Math.floor(t % 60).toString().padStart(2, "0");
  return `${mm}:${ss}`;
}

/** Chronological list of dialogue events, matching the plot's markers. */
export default function EventLog({ events }: { events: MarkerEvent[] }) {
  if (!events || events.length === 0) {
    return (
      <div className="event-empty" data-testid="no-events">
        No events detected
      </div>
    );
  }

  return (
    <div className="event-log" data-testid="event-log">
      {events.map((e, i) => (
        <div className="event-row" key={`${e.type}-${e.t}-${i}`}>
          <div className="event-time">{fmtTime(e.t)}</div>
          <div className="event-kind">
            {/* Same glyph vocabulary as the legend and the plot overlay. */}
            <span className={`legend-glyph ${e.type}`} aria-hidden="true" />
            {e.type}
          </div>
          <div className="event-duration">{e.duration ? `${e.duration.toFixed(2)}s` : "—"}</div>
        </div>
      ))}
    </div>
  );
}
