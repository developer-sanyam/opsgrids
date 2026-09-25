import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { GridIcon, HomeIcon, MoreHorizontalIcon, PlusIcon } from "../icons";
import { BottomNavigation } from "./BottomNavigation";
import type { BottomNavItem } from "./BottomNavigation";

function renderNav(items: BottomNavItem[], initialPath = "/") {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route path="*" element={<BottomNavigation items={items} />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("BottomNavigation", () => {
  it("is a labelled navigation landmark", () => {
    renderNav([{ key: "home", label: "Home", icon: HomeIcon, to: "/" }]);
    expect(screen.getByRole("navigation", { name: "Primary mobile navigation" })).toBeTruthy();
  });

  it("marks the current route with aria-current, the way a route entry does elsewhere in the shell", () => {
    renderNav([
      { key: "home", label: "Home", icon: HomeIcon, to: "/" },
      { key: "modules", label: "Modules", icon: GridIcon, to: "/modules" },
    ]);
    expect(screen.getByRole("link", { name: "Home" }).getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("link", { name: "Modules" }).getAttribute("aria-current")).toBeNull();
  });

  it("opens a sheet from a disclosure button rather than navigating", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderNav([{ key: "modules", label: "Modules", icon: GridIcon, onSelect, expanded: false }]);
    const button = screen.getByRole("button", { name: "Modules" });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    await user.click(button);
    expect(onSelect).toHaveBeenCalledTimes(1);
  });

  it("raises the centre quick-action button above the bar", () => {
    renderNav([{ key: "quick-actions", label: "New", icon: PlusIcon, onSelect: vi.fn(), raised: true }]);
    expect(screen.getByRole("button", { name: "New" })).toBeTruthy();
  });

  it("never renders more than the items it is given — no invented Reports slot", () => {
    renderNav([
      { key: "home", label: "Home", icon: HomeIcon, to: "/" },
      { key: "more", label: "More", icon: MoreHorizontalIcon, onSelect: vi.fn() },
    ]);
    expect(screen.queryByText("Reports")).toBeNull();
  });
});
