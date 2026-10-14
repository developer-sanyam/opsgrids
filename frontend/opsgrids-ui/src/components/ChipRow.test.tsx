import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ChipRow } from "./ChipRow";
import { Pill } from "./Pill";

describe("ChipRow", () => {
  it("is a named group holding its chips", () => {
    render(
      <ChipRow label="Filter leads by status">
        <Pill active>All</Pill>
        <Pill>New</Pill>
      </ChipRow>,
    );
    const group = screen.getByRole("group", { name: "Filter leads by status" });
    expect(group.querySelectorAll("button")).toHaveLength(2);
  });

  it("carries the scroll-fade hook, with no fade while the row fits", () => {
    render(
      <ChipRow label="Filters">
        <Pill>All</Pill>
      </ChipRow>,
    );
    const group = screen.getByRole("group", { name: "Filters" });
    expect(group.className).toContain("scroll-fade");
    expect(group.getAttribute("data-fade")).toBe("none");
  });
});
