import React from "react";
import { MarkerEvent } from "./MarkerOverlay";

function fmtTime(t: number) {
  const mm = Math.floor(t / 60);
  const ss = Math.floor(t % 60)
    .toString()
    .padStart(2, "0");
  return `${mm}:${ss}`;
}

export default function EventLog({ events }: { events: MarkerEvent[] }) {
  if (!events || events.length === 0) {
    return <div data-testid="no-events">No events detected</div>;
  }

  return (
    <div data-testid="event-log" style={{ fontFamily: "IBM Plex Mono, monospace", fontSize: 13 }}>
      <div style={{ display: "grid", gridTemplateColumns: "80px 1fr 80px", gap: "6px 10px" }}>
        {events.map((e, i) => (
          <div key={i} style={{ display: "contents" }}>
            <div style={{ padding: "6px 0" }}>{fmtTime(e.t)}</div>
            <div style={{ padding: "6px 0", color: "oklch(0.72 0.01 252)" }}>{e.type}</div>
            <div style={{ padding: "6px 0", textAlign: "right" }}>{e.duration ? `${e.duration.toFixed(2)}s` : "—"}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

