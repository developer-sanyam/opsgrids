import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { BoardIcon, HomeIcon, UsersIcon } from "../icons";
import { CommandMenu } from "./CommandMenu";
import type { CommandMenuItem } from "./CommandMenu";

const ITEMS: CommandMenuItem[] = [
  { key: "nav-overview", label: "Overview", group: "Go to", icon: HomeIcon, onSelect: vi.fn() },
  { key: "nav-deals", label: "Deals", group: "Go to", icon: BoardIcon, onSelect: vi.fn() },
  { key: "module-hrms", label: "HRMS", group: "Modules", icon: UsersIcon, onSelect: vi.fn() },
];

describe("CommandMenu", () => {
  it("renders nothing when closed", () => {
    render(<CommandMenu open={false} onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("puts focus on the input when opened", () => {
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    expect(document.activeElement).toBe(screen.getByRole("combobox"));
  });

  it("filters the list as the query changes", async () => {
    const user = userEvent.setup();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    await user.type(screen.getByRole("combobox"), "hrms");
    expect(screen.getByText("HRMS")).toBeTruthy();
    expect(screen.queryByText("Deals")).toBeNull();
  });

  it("runs the highlighted item and closes on Enter", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandMenu open onClose={onClose} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{Enter}");
    expect(ITEMS[0]?.onSelect).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("moves the highlight with the arrow keys before running the item", async () => {
    const user = userEvent.setup();
    render(<CommandMenu open onClose={vi.fn()} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{ArrowDown}{Enter}");
    expect(ITEMS[1]?.onSelect).toHaveBeenCalledTimes(1);
  });

  it("closes on Escape without running anything", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CommandMenu open onClose={onClose} items={ITEMS} placeholder="Search" />);
    await user.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(ITEMS[0]?.onSelect).not.toHaveBeenCalled();
  });
});
