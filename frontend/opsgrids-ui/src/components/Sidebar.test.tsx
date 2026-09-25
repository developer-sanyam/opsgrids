import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";

import { HomeIcon, UsersIcon } from "../icons";
import { Sidebar } from "./Sidebar";
import type { NavGroup } from "./Sidebar";

const GROUPS: NavGroup[] = [
  { title: "Pipeline", items: [{ to: "/", label: "Overview", icon: HomeIcon, end: true }] },
];

function renderSidebar(props: Partial<React.ComponentProps<typeof Sidebar>> = {}) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <Sidebar
        groups={GROUPS}
        user={{ name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" }}
        logo={<span>Mark</span>}
        productName="OpsGrids Revenue"
        {...props}
      />
    </MemoryRouter>,
  );
}

describe("Sidebar", () => {
  it("hides the Modules and Admin groups when the app supplies none", () => {
    renderSidebar();
    expect(screen.queryByText("Modules")).toBeNull();
    expect(screen.queryByText("Admin")).toBeNull();
  });

  it("shows Modules and Admin once the app supplies them", () => {
    renderSidebar({
      modules: [{ key: "hrms", label: "HRMS", href: "https://people.opsgrids.com", icon: UsersIcon }],
      adminLinks: [{ key: "users", label: "Users", href: "/admin/members", icon: UsersIcon }],
    });
    expect(screen.getByText("Modules")).toBeTruthy();
    expect(screen.getByText("HRMS").closest("a")?.getAttribute("href")).toBe("https://people.opsgrids.com");
    expect(screen.getByText("Admin")).toBeTruthy();
    expect(screen.getByText("Users").closest("a")?.getAttribute("href")).toBe("/admin/members");
  });

  it("marks the active route current, the way react-router's NavLink does by default", () => {
    renderSidebar();
    expect(screen.getByRole("link", { name: /Overview/ }).getAttribute("aria-current")).toBe("page");
  });

  it("keeps every label in the accessible tree when collapsed, moving it off-screen visually", () => {
    renderSidebar({ collapsed: true });
    // sr-only text is still found by role/name — it never leaves the accessibility tree.
    expect(screen.getByRole("link", { name: "Overview" })).toBeTruthy();
    expect(screen.getByText(/Sofia Marchetti/)).toBeTruthy();
  });
});
