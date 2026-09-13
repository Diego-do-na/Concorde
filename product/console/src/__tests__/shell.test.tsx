import React from "react";
import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor, act } from "@testing-library/react";
import App from "../App";

/*
 * These tests previously stubbed `/health` with `{ model, contract, p95_ms,
 * uptime_s }` — a shape the service has never sent. shellContext reads
 * `model_version` from /health, `feature_contract` from /version and
 * `routes.detect.p95_ms` from /metrics (three separate calls), so the header
 * rendered "MODEL —" and the assertions failed once the DOM environment was
 * fixed and they could actually run. The stub below answers each route with
 * the body its Rust handler really produces.
 */

const HEALTH = {
  status: "ok",
  model_version: "v1.2.3",
  git_sha: "abc1234",
  uptime_s: 3600,
  deps: { tigerdata: "ok", gemini: "ok" },
};
const VERSION = { feature_contract: "fc-1", model_version: "v1.2.3", git_sha: "abc1234" };
const METRICS = { routes: { detect: { count: 12, p50_ms: 80, p95_ms: 120, p99_ms: 200 } } };

/** Route a stubbed fetch by pathname; `failing` routes reject instead. */
function stubApi(failing: string[] = []) {
  return vi.fn(async (input: RequestInfo | URL) => {
    const path = String(typeof input === "string" ? input : (input as Request).url);
    if (failing.some((f) => path.includes(f))) throw new Error("network");
    const body = path.includes("/version") ? VERSION : path.includes("/metrics") ? METRICS : HEALTH;
    return { ok: true, json: async () => body } as unknown as Response;
  });
}

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("console shell", () => {
  it("renders header with chips and navigates tabs", async () => {
    vi.stubGlobal("fetch", stubApi());

    render(<App />);

    await waitFor(() => expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument());
    expect(screen.getByText(/CONTRACT fc-1/)).toBeInTheDocument();
    expect(screen.getByText(/p95 120ms/)).toBeInTheDocument();

    fireEvent.click(screen.getByText("Exec"));
    expect(await screen.findByText(/EVERY FIGURE BELOW IS A LABELLED ESTIMATE/)).toBeInTheDocument();
  });

  it("keeps last known model and shows DEGRADED when /health starts failing", async () => {
    // The poll interval is 10s, so the original real-timer version of this
    // test could never reach the second poll inside its own 3s budget and was
    // asserting against the first, still-healthy render.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = stubApi();
    vi.stubGlobal("fetch", fetchMock);

    render(<App />);
    await waitFor(() => expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument());

    // /health goes down; the next poll should mark the shell degraded.
    fetchMock.mockImplementation(async () => {
      throw new Error("network");
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });

    await waitFor(() => expect(screen.getByText("DEGRADED")).toBeInTheDocument());
    // last-known-good health survives the outage rather than blanking out
    expect(screen.getByText(/MODEL v1.2.3/)).toBeInTheDocument();
  });
});
