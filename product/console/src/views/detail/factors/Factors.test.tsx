import React from "react";
import { render, screen } from "@testing-library/react";
import Factors from "./Factors";

/*
 * Split from the original single test, which also covered stage timings and
 * build metadata. Those panels moved to `CallMeta` (right rail) and are
 * covered by meta/CallMeta.test.tsx.
 */
describe("Factors table", () => {
  const analysis = {
    top_factors: [
      { feature: "F-01", value: 1, direction: "human", weight: 1 },
      { feature: "F-02", value: 2, direction: "human", weight: 2 },
      { feature: "F-03", value: 3, direction: "synthetic", weight: 3 },
      { feature: "F-04", value: 4, direction: "synthetic", weight: 4 },
      { feature: "F-05", value: 5, direction: "synthetic", weight: 5 },
    ],
  } as any;

  test("renders factors in order with weights scaled against the largest", () => {
    render(<Factors analysis={analysis} />);

    for (let i = 0; i < 5; i++) {
      const bar = screen.getByTestId(`weight-bar-${i}`) as HTMLElement;
      expect(bar.style.width).toBe(`${(i + 1) * 20}%`);
    }
  });

  test("resolves a contract id to the extractor's feature name and note", () => {
    render(<Factors analysis={analysis} />);
    // F-04 is resp_latency_cv in feature_contract_fc-1.json
    expect(screen.getByText("resp_latency_cv")).toBeInTheDocument();
    expect(screen.getByText(/F-04 ·/)).toBeInTheDocument();
  });

  test("says so explicitly when the retained analysis carries no importances", () => {
    render(<Factors analysis={{ top_factors: [] } as any} />);
    expect(screen.getByTestId("no-factors")).toHaveTextContent(/not stored with the retained analysis/i);
  });
});
