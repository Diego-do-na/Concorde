import React from "react";
import { render, screen } from "@testing-library/react";
import Factors from "./Factors";

describe("Factors view", () => {
  test("renders 5 factors in order with weights scaled and semantic null shows hatch; meta shows fc-1", () => {
    const analysis = {
      top_factors: [
        { feature: "F-01", value: 1, direction: "human", weight: 1 },
        { feature: "F-02", value: 2, direction: "human", weight: 2 },
        { feature: "F-03", value: 3, direction: "synthetic", weight: 3 },
        { feature: "F-04", value: 4, direction: "synthetic", weight: 4 },
        { feature: "F-05", value: 5, direction: "synthetic", weight: 5 },
      ],
      rationale: "Because tests",
      timings_ms: { decode: 10, vad: 20, features: 30, semantic: null, inference: 40, total: 100 },
      meta: { model_version: "dev", git_sha: "local", feature_contract: "fc-1", caller_turns: 3, duration_s: 12 },
    } as any;

    render(<Factors analysis={analysis} />);

    // Five weight bars in order
    for (let i = 0; i < 5; i++) {
      const bar = screen.getByTestId(`weight-bar-${i}`) as HTMLElement;
      // width should be (weight/max)*100 -> 20,40,60,80,100
      const expected = `${(i + 1) * 20}%`;
      expect(bar.style.width).toBe(expected);
    }

    // semantic timing should render hatch (we test that it displays "—" and has a degraded background)
    const semantic = screen.getByTestId("timing-semantic") as HTMLElement;
    // degraded cell shows the em-dash text
    expect(semantic.textContent?.includes("—")).toBeTruthy();

    // meta contains feature_contract fc-1
    expect(screen.getByText(/CONTRACT:/).textContent).toContain("fc-1");
  });
});

