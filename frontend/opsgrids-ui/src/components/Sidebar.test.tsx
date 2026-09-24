/**
 * The rail's account switcher says who is signed in and in what capacity. Most apps act inside an
 * organization; the operator console acts in none and is known by its roles, so `detail` stands in
 * for the organization line — and nothing about the menu around it changes.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

import { ShieldIcon } from "../icons";
import { Sidebar } from "./Sidebar";
import type { SidebarAccount } from "./Sidebar";
import { ThemeProvider } from "./ThemeProvider";

function Where() {
  const location = useLocation();
  return <p data-testid="where">{`${location.pathname}${location.search}`}</p>;
}

function renderRail(account: SidebarAccount, onSignOut = vi.fn()) {
  render(
    <ThemeProvider storageKey="opsgrids.test.theme">
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route
            path="*"
            element={
              <>
                <Sidebar
                  groups={[{ title: "Console", items: [{ to: "/", label: "Overview", icon: ShieldIcon, end: true }] }]}
                  pinned={[]}
                  account={account}
                  onSignOut={onSignOut}
                  logo={<span>Mark</span>}
                  product={{ name: "OpsGrids Internal", tagline: "Operator console" }}
                  search={{ label: "Search accounts", placeholder: "Search accounts", to: "/accounts" }}
                />
                <Where />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
  return { onSignOut };
}

describe("Sidebar account switcher", () => {
  it("shows the organization the person is acting in, and labels it in the menu", async () => {
    const user = userEvent.setup();
    renderRail({ name: "Asha Rao", email: "asha@acme.example", organizationName: "Acme Ltd" });
    expect(screen.getByRole("button", { name: /Asha Rao/ }).textContent).toContain("Acme Ltd");
    await user.click(screen.getByRole("button", { name: /Asha Rao/ }));
    expect(screen.getByText("Organization")).toBeDefined();
  });

  it("shows a labelled detail in place of the organization, and uses its own label in the menu", async () => {
    const user = userEvent.setup();
    renderRail({
      name: "Priya Venkatesan",
      email: "priya@opsgrids.example",
      detail: { label: "Operator roles", value: "Owner · Support" },
    });
    const trigger = screen.getByRole("button", { name: /Priya Venkatesan/ });
    expect(trigger.textContent).toContain("Owner · Support");
    await user.click(trigger);
    expect(screen.getByText("Operator roles")).toBeDefined();
    expect(screen.queryByText("Organization")).toBeNull();
  });

  it("falls back to a neutral line, and no box, when there is neither", async () => {
    const user = userEvent.setup();
    renderRail({ name: "Solo User", email: "solo@example.test" });
    const trigger = screen.getByRole("button", { name: /Solo User/ });
    expect(trigger.textContent).toContain("Workspace");
    await user.click(trigger);
    expect(screen.queryByText("Organization")).toBeNull();
  });

  it("signs out from the menu and closes it", async () => {
    const user = userEvent.setup();
    const { onSignOut } = renderRail({ name: "Asha Rao", email: "asha@acme.example" });
    await user.click(screen.getByRole("button", { name: /Asha Rao/ }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("closes the menu on Escape without signing out", async () => {
    const user = userEvent.setup();
    const { onSignOut } = renderRail({ name: "Asha Rao", email: "asha@acme.example" });
    await user.click(screen.getByRole("button", { name: /Asha Rao/ }));
    expect(screen.getByRole("menu")).toBeDefined();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    expect(onSignOut).not.toHaveBeenCalled();
  });
});

describe("Sidebar search", () => {
  it("jumps to the product's list with the query", async () => {
    const user = userEvent.setup();
    renderRail({ name: "Asha Rao", email: "asha@acme.example" });
    await user.type(screen.getByRole("searchbox", { name: "Search accounts" }), "acme ltd{Enter}");
    expect(screen.getByTestId("where").textContent).toBe("/accounts?q=acme%20ltd");
  });

  it("focuses the search from the keyboard shortcut", async () => {
    const user = userEvent.setup();
    renderRail({ name: "Asha Rao", email: "asha@acme.example" });
    await user.keyboard("{Control>}k{/Control}");
    expect(document.activeElement).toBe(screen.getByRole("searchbox", { name: "Search accounts" }));
  });
});
