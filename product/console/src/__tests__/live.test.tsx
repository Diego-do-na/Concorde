import React from "react";
import { describe, it, expect, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import Live from "../views/live/Live";
import { makeFixtures } from "../mocks/fixtures";
import { MemoryRouter } from "react-router-dom";

beforeEach(() => {
  // no-op
});

describe("Live view", () => {
  it("renders 14 fixture rows with semantic degraded pip and reacts to new feed events", async () => {
    const fixtures = makeFixtures();

    // simple mock feed
    const subs: any[] = [];
    const mockFeed = {
      subscribe(cb: any) {
        subs.push(cb);
        // initial emit
        cb(fixtures);
        return () => {
          const i = subs.indexOf(cb);
          if (i >= 0) subs.splice(i, 1);
        };
      },
      close() {},
      online: true,
      lastItems: fixtures,
    } as any;

    render(
      <MemoryRouter>
        <Live feed={mockFeed} />
      </MemoryRouter>
    );

    // 14 rows from fixtures
    const rows = await screen.findAllByTestId("call-row");
    expect(rows.length).toBe(14);

    // semantic degraded pip title exists (fixtures use null semantic)
    expect(screen.getAllByTitle("semantic degraded").length).toBeGreaterThan(0);

    // simulate a new incoming feed event prepending a row
    const newItem = { ...fixtures[0], id: "call-new" };
    // call subscribers
    for (const s of subs) s([newItem, ...fixtures]);

    const updated = await screen.findAllByTestId("call-row");
    expect(updated[0].textContent).toContain("call-new");
  });

  it("renders empty state when no items", () => {
    const mockFeed = {
      subscribe(cb: any) {
        cb([]);
        return () => {};
      },
      close() {},
      online: true,
      lastItems: [],
    } as any;

    render(
      <MemoryRouter>
        <Live feed={mockFeed} />
      </MemoryRouter>
    );
    expect(screen.getByText(/No calls processed yet/)).toBeInTheDocument();
  });
});

