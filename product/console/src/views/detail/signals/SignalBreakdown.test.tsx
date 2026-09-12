import React from "react";
import { render, screen } from "@testing-library/react";
import SignalBreakdown from "./SignalBreakdown";
import { makeFixtures } from "../../../mocks/fixtures";

describe("SignalBreakdown", () => {
  test("semantic null shows hatch + DEGRADED and no 0.00 text; available signals show filled bars", () => {
    const fixtures = makeFixtures();
    const a = fixtures[0];

    // ensure behavioural and acoustic have numeric values for the test
    a.signals.behavioral = { value: 0.23 };
    a.signals.acoustic = { value: 0.78 };
    a.degraded = { semantic_available: false, acoustic_available: true };

    render(<SignalBreakdown analysis={a as any} />);

    // semantic row should display DEGRADED
    expect(screen.getByText("DEGRADED")).toBeTruthy();

    // there must be no "0.00" anywhere in the panel
    const zeroText = screen.queryByText("0.00");
    expect(zeroText).toBeNull();

    // behavioural fill should be visible and small (23%)
    const behFill = screen.getByTestId("behavioral-fill") as HTMLElement;
    expect(behFill.style.width).toBe("23%");

    // acoustic fill should be visible and large (78%)
    const acFill = screen.getByTestId("acoustic-fill") as HTMLElement;
    expect(acFill.style.width).toBe("78%");
  });
});

