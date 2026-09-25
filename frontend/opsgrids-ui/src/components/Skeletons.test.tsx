/**
 * A loading preset is one status that says what is loading. The blocks are for the eye; a screen
 * reader hearing "image, image, image" learns nothing.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { CardSkeleton, ChartSkeleton, DashboardSkeleton, FormSkeleton, TableSkeleton } from "./Skeletons";

describe("skeleton presets", () => {
  it.each([
    ["dashboard", <DashboardSkeleton key="d" />, "Loading dashboard"],
    ["table", <TableSkeleton key="t" label="Loading accounts" />, "Loading accounts"],
    ["card", <CardSkeleton key="c" label="Loading recent activity" />, "Loading recent activity"],
    ["form", <FormSkeleton key="f" label="Loading the purchase order" />, "Loading the purchase order"],
    ["chart", <ChartSkeleton key="ch" variant="donut" label="Loading revenue by plan" />, "Loading revenue by plan"],
  ])("the %s preset is one busy status, named in words", (_, preset, label) => {
    render(preset);
    const status = screen.getByRole("status");
    expect(status.getAttribute("aria-busy")).toBe("true");
    expect(status.textContent).toBe(label);
    expect(status.querySelectorAll(".animate-pulse").length).toBeGreaterThan(0);
    for (const block of Array.from(status.querySelectorAll(".animate-pulse"))) {
      expect(block.getAttribute("aria-hidden")).toBe("true");
    }
  });

  it("draws as many rows and fields as it is asked for", () => {
    const { container, rerender } = render(<TableSkeleton rows={7} columns={3} />);
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength((7 + 1) * 3);
    rerender(<FormSkeleton fields={5} />);
    // A label and a control per field, and the two action buttons.
    expect(container.querySelectorAll(".animate-pulse")).toHaveLength(5 * 2 + 2);
  });
});
