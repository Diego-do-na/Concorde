import React from "react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import App from "../App";

// polyfill fetch
import "whatwg-fetch";

const healthy = { model: "v1.2.3", contract: "fc-1", p95_ms: 120, uptime_s: 3600 };

beforeEach(() => {
  // reset fetch mock
  vi.restoreAllMocks();
});

describe("console shell", () => {
  it("renders header with chips and navigates tabs", async () => {
    // mock /health
    vi.stubGlobal("fetch", vi.fn(async () => ({
      ok: true,
      json: async () => healthy
    } as any)));

    render(<App />);

    // header shows model chip
    await waitFor(() => expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument());

    // navigate to Exec
    fireEvent.click(screen.getByText("Exec"));
    expect(await screen.findByText(/Exec view lands/)).toBeInTheDocument();
  });

  it("keeps last known model and shows OFFLINE when /health fails", async () => {
    const seq = [
      // first call returns healthy
      vi.fn().mockResolvedValueOnce({ ok: true, json: async () => healthy }),
      // second call fails
      vi.fn().mockRejectedValueOnce(new Error("network"))
    ];
    // stub fetch to use the sequence
    let call = 0;
    vi.stubGlobal("fetch", (..._args:any) => {
      const fn = seq[Math.min(call, seq.length-1)];
      call++;
      return fn();
    });

    render(<App />);
    await waitFor(() => expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument());

    // wait for second poll which will fail and mark OFFLINE/DEGRADED
    await waitFor(() => expect(screen.getByText(/DEGRADED|OFFLINE/)).toBeTruthy(), {timeout: 3000});
    // model still visible
    expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument();
  });
});

