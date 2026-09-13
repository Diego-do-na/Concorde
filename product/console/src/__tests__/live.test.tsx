import React from "react";
import { describe, it, expect } from "vitest";
import { render, screen, act } from "@testing-library/react";
import Live from "../views/live/Live";
import { makeFixtures } from "../mocks/fixtures";
import { MemoryRouter } from "react-router-dom";
import { ShellProvider } from "../app/shellContext";
import { makeStubFeed } from "../test/stubFeed";

describe("Live view", () => {
  it("renders 14 fixture rows with semantic degraded pip and reacts to new feed events", async () => {
    const fixtures = makeFixtures();
    const feed = makeStubFeed(fixtures);

    render(
      <MemoryRouter>
        <ShellProvider feed={feed}>
          <Live feed={feed} />
        </ShellProvider>
      </MemoryRouter>
    );

    const rows = await screen.findAllByTestId("call-row");
    expect(rows.length).toBe(14);

    // semantic degraded pip title exists (fixtures use null semantic)
    expect(screen.getAllByTitle("semantic degraded").length).toBeGreaterThan(0);

    // simulate a new incoming feed event prepending a row. Emitting outside
    // act() leaves the re-render pending and findAllByTestId resolves against
    // the rows that are already on screen, so the assertion read a stale row.
    const newItem = { ...fixtures[0], id: "call-new" };
    await act(async () => {
      feed.emit([newItem, ...fixtures]);
    });

    const updated = await screen.findAllByTestId("call-row");
    expect(updated[0].textContent).toContain("call-new");
  });

  it("renders empty state when no items", () => {
    const feed = makeStubFeed([]);

    render(
      <MemoryRouter>
        <ShellProvider feed={feed}>
          <Live feed={feed} />
        </ShellProvider>
      </MemoryRouter>
    );
    expect(screen.getByText(/No calls processed yet/)).toBeInTheDocument();
  });
});
