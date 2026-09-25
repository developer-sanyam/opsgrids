/**
 * Tabs are one control to the keyboard: one Tab stop, arrows between tabs. What is pinned here is
 * that contract, and that the panel is tied to the tab that shows it.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Tabs } from "./Tabs";
import type { TabItem } from "./Tabs";

const ITEMS: TabItem[] = [
  { id: "overview", label: "Overview" },
  { id: "users", label: "Users", count: 24 },
  { id: "billing", label: "Billing", disabled: true },
  { id: "audit", label: "Audit log" },
];

function Harness({ activation }: { activation?: "automatic" | "manual" }) {
  const [value, setValue] = useState("overview");
  return (
    <Tabs label="Account sections" items={ITEMS} value={value} onChange={setValue} activation={activation}>
      <p>Showing {value}</p>
    </Tabs>
  );
}

describe("Tabs", () => {
  it("puts only the selected tab in the Tab order", () => {
    render(<Harness />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs.map((tab) => tab.tabIndex)).toEqual([0, -1, -1, -1]);
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-selected")).toBe("true");
  });

  it("reads a count with its label", () => {
    render(<Harness />);
    expect(screen.getByRole("tab", { name: "Users 24" })).toBeTruthy();
  });

  it("selects as the arrows move, skips a disabled tab and wraps at the ends", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Overview" }));

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Users 24" }));
    expect(screen.getByText("Showing users")).toBeTruthy();

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Audit log" }));

    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Overview" }));

    await user.keyboard("{ArrowLeft}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Audit log" }));
  });

  it("jumps to the ends with Home and End", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.tab();
    await user.keyboard("{End}");
    expect(screen.getByText("Showing audit")).toBeTruthy();
    await user.keyboard("{Home}");
    expect(screen.getByText("Showing overview")).toBeTruthy();
  });

  it("in manual mode moves focus with the arrows and selects on Enter", async () => {
    const user = userEvent.setup();
    render(<Harness activation="manual" />);
    await user.tab();
    await user.keyboard("{ArrowRight}");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Users 24" }));
    expect(screen.getByText("Showing overview")).toBeTruthy();
    await user.keyboard("{Enter}");
    expect(screen.getByText("Showing users")).toBeTruthy();
  });

  it("ties the panel to the tab that shows it, and makes it reachable", () => {
    render(<Harness />);
    const panel = screen.getByRole("tabpanel", { name: "Overview" });
    expect(panel.tabIndex).toBe(0);
    expect(screen.getByRole("tab", { name: "Overview" }).getAttribute("aria-controls")).toBe(panel.id);
  });

  it("does not report a click on the tab that is already selected", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<Tabs label="Range" items={ITEMS} value="overview" onChange={onChange} variant="segmented" />);
    await user.click(screen.getByRole("tab", { name: "Overview" }));
    expect(onChange).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: "Audit log" }));
    expect(onChange).toHaveBeenCalledWith("audit");
    // No children: no panel for the tabs to point at.
    expect(screen.queryByRole("tabpanel")).toBeNull();
  });
});
