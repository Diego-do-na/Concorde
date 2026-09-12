import React from "react";
import { render, screen } from "@testing-library/react";
import { createTimeScale } from "../waveform/TimeScale";
import { MarkerOverlay, Legend, EventLog, MarkerEvent } from ".";

describe("Markers overlay + legend + log", () => {
  const events: MarkerEvent[] = [
    { type: "overlap", t: 0.5 },
    { type: "interruption", t: 1.2, duration: 0.3 },
    { type: "silence", t: 2.6, duration: 3.2 },
    { type: "overlap", t: 3.1 },
    { type: "interruption", t: 4.4 },
    { type: "silence", t: 5.9, duration: 2.5 },
  ];

  it("renders 6 markers at xFor(t) px and legend counts match; each type has glyph", () => {
    const scale = createTimeScale(10, 500);
    render(<MarkerOverlay events={events} timeScale={scale} />);

    const markers = screen.getAllByTestId("marker");
    expect(markers.length).toBe(6);

    markers.forEach((m, i) => {
      const left = parseFloat((m as HTMLElement).style.left || "0");
      expect(left).toBeCloseTo(scale.xFor(events[i].t), 5);
    });

    // Glyph existence (check while overlay is mounted)
    events.forEach((e, i) => {
      if (e.type === "overlap") expect(screen.getByTestId(`glyph-overlap-${i}`)).toBeTruthy();
      if (e.type === "interruption") expect(screen.getByTestId(`glyph-interruption-${i}`)).toBeTruthy();
      if (e.type === "silence") expect(screen.getByTestId(`glyph-silence-${i}`)).toBeTruthy();
    });

    // Legend counts (render separately)
    render(<Legend events={events} />);
    expect(screen.getByTestId("legend-overlap")).toHaveTextContent("2");
    expect(screen.getByTestId("legend-interruption")).toHaveTextContent("2");
    expect(screen.getByTestId("legend-silence")).toHaveTextContent("2");
  });

  it("renders no-events message when none", () => {
    render(<EventLog events={[]} />);
    expect(screen.getByTestId("no-events")).toHaveTextContent("No events detected");
  });
});

