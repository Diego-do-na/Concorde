import React from "react";
import { describe, it, expect, beforeEach, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import DetailView from "../DetailView";
import { makeFixtures } from "../../../mocks/fixtures";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import * as api from "../../../lib/api";

beforeEach(() => {
  vi.restoreAllMocks();
});

describe("Detail view", () => {
  it("renders with fixture analysis and responds to sidebar clicks", async () => {
    const fixtures = makeFixtures();

    const subs: any[] = [];
    const mockFeed = {
      subscribe(cb: any) {
        subs.push(cb);
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

    // mock API: return full analysis for call-1, null for others
    vi.spyOn(api, "getFeedAnalysis").mockImplementation(async (id: string) => {
      return fixtures.find((f) => f.id === id) || null;
    });

    render(
      <MemoryRouter initialEntries={["/calls/call-1"]}>
        <Routes>
          <Route path="/calls/:id" element={<DetailView feed={mockFeed} />} />
        </Routes>
      </MemoryRouter>
    );

    // header shows call id
    expect(await screen.findByText("call-1")).toBeInTheDocument();

    // sidebar rows present
    const rows = await screen.findAllByTestId("recent-row");
    expect(rows.length).toBe(14);

    // click another row -> header should update
    fireEvent.click(rows[2]);
    // after navigation the component will fetch; our mock returns analysis for that id
    expect(await screen.findByText(fixtures[2].id)).toBeInTheDocument();
  });

  it("shows retained-only state when analysis not retained", async () => {
    const fixtures = makeFixtures();
    const mockFeed = {
      subscribe(cb: any) {
        cb(fixtures);
        return () => {};
      },
      close() {},
      online: true,
      lastItems: fixtures,
    } as any;

    // mock API to always return null (not retained)
    vi.spyOn(api, "getFeedAnalysis").mockResolvedValue(null);

    render(
      <MemoryRouter initialEntries={["/calls/call-2"]}>
        <Routes>
          <Route path="/calls/:id" element={<DetailView feed={mockFeed} />} />
        </Routes>
      </MemoryRouter>
    );

    expect(await screen.findByTestId("retained-only")).toBeInTheDocument();
    expect(screen.getByText(/Analysis not retained/)).toBeInTheDocument();
  });
});

