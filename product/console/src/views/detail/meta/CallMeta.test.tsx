import React from "react";
import { render, screen } from "@testing-library/react";
import CallMeta from "./CallMeta";

describe("CallMeta", () => {
  const analysis = {
    rationale: "Because tests",
    timings_ms: { decode: 10, vad: 20, features: 30, semantic: null, inference: 40, total: 100 },
    meta: { model_version: "dev", git_sha: "local", feature_contract: "fc-1", duration_s: 12 },
  } as any;

  test("a shed stage reads as an em dash over a hatch, never as 0 ms", () => {
    render(<CallMeta analysis={analysis} />);
    const semantic = screen.getByTestId("timing-semantic");
    expect(semantic.textContent).toContain("—");
    expect(semantic.textContent).not.toContain("0 ms");
    expect(semantic.querySelector(".bar-track.is-degraded")).not.toBeNull();
  });

  test("stages that ran show their measured cost", () => {
    render(<CallMeta analysis={analysis} />);
    expect(screen.getByTestId("timing-decode")).toHaveTextContent("10 ms");
    expect(screen.getByTestId("timing-total")).toHaveTextContent("100 ms");
  });

  test("shows the build metadata the verdict came from", () => {
    render(<CallMeta analysis={analysis} />);
    expect(screen.getByText("fc-1")).toBeInTheDocument();
    expect(screen.getByText("dev")).toBeInTheDocument();
    expect(screen.getByText("local")).toBeInTheDocument();
  });
});
