import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { TrendingUpIcon } from "../icons";
import { HeroMetricCard } from "./HeroMetricCard";

describe("HeroMetricCard", () => {
  it("marks the headline figure for the containment check, and reads the delta in words", () => {
    render(
      <HeroMetricCard
        label="Total Revenue"
        value="₹12,48,320"
        icon={<TrendingUpIcon className="size-5" />}
        deltaPct={12}
        caption="from last month"
        bars={[9_00_000, 10_40_000, 12_48_320]}
      />,
    );
    const value = screen.getByText("₹12,48,320");
    expect(value.hasAttribute("data-kpi-value")).toBe(true);
    expect(screen.getByText(/Up/)).toBeTruthy();
    expect(screen.getByText("from last month")).toBeTruthy();
  });

  it("draws no bars and no delta when there is no history to show", () => {
    const { container } = render(
      <HeroMetricCard label="Total Revenue" value="₹0" icon={<TrendingUpIcon className="size-5" />} caption="This month" />,
    );
    expect(screen.queryByText(/Up|Down/)).toBeNull();
    expect(screen.getByText("This month")).toBeTruthy();
    expect(container.querySelectorAll("[aria-hidden] > div").length).toBe(0);
  });
});
