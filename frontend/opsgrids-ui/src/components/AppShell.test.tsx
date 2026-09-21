/**
 * The shell's keyboard contract: the mobile drawer traps Tab and gives focus back to the toggle
 * that opened it, and a route change moves focus onto `<main>` instead of only scrolling — both
 * of which a mouse user never notices missing.
 */

import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";

import { AppShell } from "./AppShell";
import { ThemeProvider } from "./ThemeProvider";

beforeEach(() => {
  // jsdom doesn't implement scrolling; the shell resets it on every route change.
  window.scrollTo = vi.fn();
});

function renderShell(initialPath = "/one") {
  return render(
    <ThemeProvider storageKey="opsgrids.test.theme">
    <MemoryRouter initialEntries={[initialPath]}>
      <Routes>
        <Route
          element={
            <AppShell
              logo={<span>Logo</span>}
              productName="Engage"
              renderSidebar={({ onNavigate }) => (
                <nav>
                  <Link to="/two" onClick={onNavigate}>
                    Page two
                  </Link>
                </nav>
              )}
            />
          }
        >
          <Route path="/one" element={<h1>Page one</h1>} />
          <Route path="/two" element={<h1>Page two</h1>} />
        </Route>
      </Routes>
    </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("AppShell", () => {
  it("has a skip link that moves focus onto main content", async () => {
    const user = userEvent.setup();
    renderShell();
    // Tab once from the top of the document lands on the skip link.
    await user.tab();
    const link = screen.getByRole("link", { name: "Skip to main content" });
    expect(document.activeElement).toBe(link);
    await user.keyboard("{Enter}");
    const main = document.getElementById("main-content");
    expect(document.activeElement).toBe(main);
  });

  it("opens the mobile drawer with focus inside it and traps Tab", async () => {
    const user = userEvent.setup();
    renderShell();
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    await user.click(toggle);

    const dialog = screen.getByRole("dialog", { name: "Navigation" });
    const close = screen.getByRole("button", { name: "Close navigation" });
    expect(document.activeElement).toBe(close);

    // Only one other focusable control lives in the drawer (the nav link) — Tab from the close
    // button lands there, and Tab again wraps back to the close button rather than escaping.
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.tab();
    expect(document.activeElement).toBe(close);
  });

  it("moves focus onto main content on a route change", async () => {
    const user = userEvent.setup();
    renderShell();
    await screen.findByRole("heading", { name: "Page one" });
    await user.click(screen.getByRole("link", { name: "Page two" }));
    await screen.findByRole("heading", { name: "Page two" });
    expect(document.activeElement).toBe(document.getElementById("main-content"));
  });

  it("returns focus to the toggle when the drawer closes", async () => {
    const user = userEvent.setup();
    renderShell();
    const toggle = screen.getByRole("button", { name: "Open navigation" });
    await user.click(toggle);
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.activeElement).toBe(toggle);
  });
});
