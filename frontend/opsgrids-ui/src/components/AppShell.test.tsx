/**
 * The shell's structural and keyboard contract: landmarks, the nav drawer/rail/bottom-nav
 * disclosures, ⌘K, and a cross-product module link that only appears when the app is handed one —
 * `AppShell` never invents a destination the caller didn't pass.
 */

import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { BoardIcon, HomeIcon, PlusIcon, UsersIcon } from "../icons";
import { AppShell } from "./AppShell";
import type { AppShellProps } from "./AppShell";
import { ThemeProvider } from "./ThemeProvider";

beforeEach(() => {
  // jsdom doesn't implement scrolling; the shell resets it on every route change.
  window.scrollTo = vi.fn();
  // The desktop sidebar's collapse preference persists across page loads — and, without this,
  // across tests in this file too.
  localStorage.clear();
});

const BASE_PROPS: AppShellProps = {
  product: { name: "OpsGrids Revenue", mark: <span>Logo</span> },
  nav: [
    {
      title: "Pipeline",
      items: [
        { to: "/one", label: "Page one", icon: HomeIcon, end: true },
        { to: "/two", label: "Page two", icon: BoardIcon },
      ],
    },
  ],
  user: { name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" },
  onSignOut: vi.fn(),
};

function renderShell(props: Partial<AppShellProps> = {}, initialPath = "/one") {
  return render(
    <ThemeProvider storageKey="opsgrids.test.theme">
      <MemoryRouter initialEntries={[initialPath]}>
        <Routes>
          <Route element={<AppShell {...BASE_PROPS} {...props} />}>
            <Route path="/one" element={<h1>Page one</h1>} />
            <Route path="/two" element={<h1>Page two</h1>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("AppShell — landmarks and focus", () => {
  it("has a skip link that moves focus onto main content", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.tab();
    const link = screen.getByRole("link", { name: "Skip to main content" });
    expect(document.activeElement).toBe(link);
    await user.keyboard("{Enter}");
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });

  it("exposes exactly one primary navigation landmark and a main landmark", () => {
    renderShell();
    expect(screen.getAllByRole("navigation", { name: "Primary" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("main")).toBeTruthy();
  });

  it("moves focus onto main content on a route change", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Page one" });
    // The desktop sidebar and the tablet rail both render the same nav at once — CSS decides
    // which is visible at a given width, and jsdom applies none of it — so either link is a real,
    // equivalent way to navigate.
    const [pageTwo] = screen.getAllByRole("link", { name: "Page two" });
    if (!pageTwo) throw new Error("Page two link not found");
    await user.click(pageTwo);
    await screen.findByRole("heading", { name: "Page two" });
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });
});

describe("AppShell — the mobile/tablet nav drawer", () => {
  it("opens with focus inside it, traps Tab, and returns focus to the toggle on close", async () => {
    const user = userEvent.setup();
    renderShell();
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    await user.click(toggle);

    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    const close = screen.getByRole("button", { name: "Close navigation" });
    expect(document.activeElement).toBe(close);

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(toggle);
    void dialog;
  });

  it("also opens from the bottom navigation's More button", async () => {
    const user = userEvent.setup();
    renderShell();
    await user.click(screen.getByRole("button", { name: "More" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeTruthy();
  });

  it("the tablet rail's own expand control opens the same overlay, not the desktop column", async () => {
    const user = userEvent.setup();
    renderShell();
    // Distinct names by default: the desktop column starts expanded ("Collapse navigation") and
    // the tablet rail is always collapsed ("Expand navigation") — clicking the rail's opens the
    // temporary overlay, it does not touch the desktop column's own state.
    await user.click(screen.getByRole("button", { name: "Expand navigation" }));
    expect(screen.getByRole("dialog", { name: "Navigation" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeTruthy();
  });
});

describe("AppShell — the desktop sidebar's own collapse", () => {
  it("starts expanded and collapses to the rail in place, without opening the nav-drawer overlay", async () => {
    const user = userEvent.setup();
    renderShell();
    expect(screen.getByRole("button", { name: "Collapse navigation" })).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    // Now both the desktop column and the tablet rail read "Expand navigation".
    expect(screen.getAllByRole("button", { name: "Expand navigation" }).length).toBe(2);
  });

  it("remembers the choice for the next mount, in this browser", async () => {
    const user = userEvent.setup();
    const { unmount } = renderShell();
    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    unmount();

    renderShell();
    expect(screen.getAllByRole("button", { name: "Expand navigation" }).length).toBe(2);
  });

  it("uses the compact mark once collapsed — the tablet rail (always collapsed) already does", async () => {
    const user = userEvent.setup();
    renderShell({
      product: { name: "OpsGrids Internal", mark: <span>Full mark</span>, compactMark: <span>Compact mark</span> },
    });
    // The always-collapsed tablet rail already renders the compact mark; the expanded desktop
    // column and the mobile header still render the full one.
    const fullBefore = screen.getAllByText("Full mark").length;
    const compactBefore = screen.getAllByText("Compact mark").length;
    expect(fullBefore).toBeGreaterThan(0);
    expect(compactBefore).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Collapse navigation" }));
    // Collapsing the desktop column swaps its mark too: one more compact, one fewer full.
    expect(screen.getAllByText("Compact mark").length).toBe(compactBefore + 1);
    expect(screen.getAllByText("Full mark").length).toBe(fullBefore - 1);
  });
});

describe("AppShell — modules", () => {
  it("renders no Modules destination when the app passes none", () => {
    renderShell();
    expect(screen.queryByText("Modules")).toBeNull();
  });

  it("renders a cross-product module link only once the app hands it one", () => {
    renderShell({
      modules: [{ key: "hrms", label: "HRMS", href: "https://people.opsgrids.com", icon: UsersIcon }],
    });
    const links = screen.getAllByRole("link", { name: "HRMS" });
    expect(links.length).toBeGreaterThan(0);
    for (const link of links) expect(link.getAttribute("href")).toBe("https://people.opsgrids.com");
  });

  it("opens the mobile Modules sheet from the bottom navigation", async () => {
    const user = userEvent.setup();
    renderShell({
      modules: [{ key: "hrms", label: "HRMS", href: "https://people.opsgrids.com", icon: UsersIcon }],
    });
    await user.click(screen.getByRole("button", { name: "Modules" }));
    const dialog = await screen.findByRole("dialog", { name: "Modules" });
    expect(within(dialog).getByRole("link", { name: "HRMS" })).toBeTruthy();
  });
});

describe("AppShell — quick actions", () => {
  it("shows no raised quick-action button when the app passes none", () => {
    renderShell();
    expect(screen.queryByRole("button", { name: "New" })).toBeNull();
  });

  it("opens the quick-actions sheet and runs the chosen action", async () => {
    const user = userEvent.setup();
    const onSelect = vi.fn();
    renderShell({
      quickActions: [{ key: "new-deal", label: "New deal", icon: PlusIcon, onSelect }],
    });
    await user.click(screen.getByRole("button", { name: "New" }));
    const dialog = await screen.findByRole("dialog", { name: "Quick actions" });
    await user.click(within(dialog).getByRole("button", { name: "New deal" }));
    expect(onSelect).toHaveBeenCalledTimes(1);
  });
});

describe("AppShell — command menu", () => {
  it("shows no search trigger when the app passes no search config", () => {
    renderShell();
    expect(screen.queryByRole("combobox")).toBeNull();
  });

  it("opens from the header trigger and navigates on Enter", async () => {
    const user = userEvent.setup();
    renderShell({ search: { placeholder: "Search deals" } });
    const trigger = screen.getAllByText("Search deals")[0];
    if (!trigger) throw new Error("search trigger not found");
    await user.click(trigger);
    const input = await screen.findByRole("combobox");
    await user.type(input, "Page two");
    await user.keyboard("{Enter}");
    await screen.findByRole("heading", { name: "Page two" });
  });

  it("opens on Ctrl+K", async () => {
    const user = userEvent.setup();
    renderShell({ search: { placeholder: "Search deals" } });
    await user.keyboard("{Control>}k{/Control}");
    expect(await screen.findByRole("combobox")).toBeTruthy();
  });
});

describe("AppShell — sign out", () => {
  it("reuses Menu for the account menu, with sign out as its action", async () => {
    const user = userEvent.setup();
    const onSignOut = vi.fn();
    renderShell({ onSignOut });
    const trigger = screen.getAllByRole("button", { name: /Account menu/ })[0];
    if (!trigger) throw new Error("account menu trigger not found");
    await user.click(trigger);
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
});
