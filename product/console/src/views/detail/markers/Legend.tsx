import React from "react";
import { MarkerEvent } from "./MarkerOverlay";

export default function Legend({ events }: { events: MarkerEvent[] }) {
  const counts = { overlap: 0, interruption: 0, silence: 0 };
  events.forEach((e) => {
    if (e.type === "overlap") counts.overlap++;
    if (e.type === "interruption") counts.interruption++;
    if (e.type === "silence") counts.silence++;
  });

  const row = (label: string, type: string, glyph: React.ReactNode, count: number) => (
    <div style={{ display: "flex", gap: 8, alignItems: "center", fontFamily: "IBM Plex Mono, monospace" }} data-testid={`legend-${type}`}>
      <div style={{ width: 18, height: 18 }}>{glyph}</div>
      <div style={{ flex: 1 }}>{label}</div>
      <div style={{ fontFamily: "IBM Plex Mono, monospace" }}>{count}</div>
    </div>
  );

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr", gap: 6 }}>
      {row("Overlap", "overlap", <div style={{ width: 12, height: 12, transform: "rotate(45deg)", background: "oklch(0.95 0.006 252)" }} />, counts.overlap)}
      {row("Interruption", "interruption", <div style={{ width: 12, height: 12, borderRadius: 999, background: "oklch(0.86 0.01 252)" }} />, counts.interruption)}
      {row("Silence > 2s", "silence", <div style={{ width: 12, height: 12, border: "1px solid oklch(0.8 0.01 252)" }} />, counts.silence)}
    </div>
  );
}

