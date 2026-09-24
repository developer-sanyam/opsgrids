/**
 * The console as a whole: the shell every screen sits in, the navigation an operator's roles allow,
 * and what happens at the edges — an unknown page, a session that ends under the operator's feet,
 * the light and dark themes.
 *
 * This is the parity check for the migration from the console's own design-system copy onto
 * `@opsgrids/ui`: the same seven screens, reached the same way, in the same shell.
 */

import { describe, expect, it, vi } from "vitest";
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { notifyUnauthorized } from "@opsgrids/client";

import * as client from "@/api/client";
import { OPERATOR_THEME_KEY } from "@/auth/keys";
import { renderConsole } from "@/testing-render";

/** The primary navigation's links, by name, as an operator sees them. */
function navLinks(): string[] {
  const nav = screen.getByRole("navigation", { name: "Primary" });
  return within(nav)
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

describe("the shell", () => {
  it("has a skip link, one main landmark and the console's own name", async () => {
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "Overview" });

    expect(screen.getByRole("link", { name: "Skip to main content" })).toBeDefined();
    expect(screen.getAllByRole("main")).toHaveLength(1);
    expect(screen.getAllByText("OpsGrids Internal").length).toBeGreaterThan(0);
  });

  it("says who is signed in and in what capacity, and offers to sign out", async () => {
    const user = userEvent.setup();
    renderConsole("/", ["owner", "support"]);
    await screen.findByRole("heading", { level: 1, name: "Overview" });

    const account = screen.getByRole("button", { name: /Priya Venkatesan/ });
    expect(account.textContent).toContain("Owner · Support");
    await user.click(account);
    expect(screen.getByText("Operator roles")).toBeDefined();
    expect(screen.getByText("priya.venkatesan@opsgrids.com")).toBeDefined();
    expect(screen.getByRole("menuitem", { name: "Sign out" })).toBeDefined();
  });

  it("signs out to the sign-in screen, and asks for a password again", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/accounts");
    await screen.findByRole("heading", { level: 1, name: "Accounts" });

    await user.click(screen.getByRole("button", { name: /Priya Venkatesan/ }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Operator sign-in" })).toBeDefined();
    expect(router.state.location.pathname).toBe("/login");
  });

  it("returns to the sign-in screen when a read is answered 401, remembering where the operator was", async () => {
    const { router } = renderConsole("/accounts?q=kaveri");
    await screen.findByRole("heading", { level: 1, name: "Accounts" });

    act(() => notifyUnauthorized());

    expect(await screen.findByRole("heading", { level: 1, name: "Operator sign-in" })).toBeDefined();
    expect(router.state.location.pathname).toBe("/login");
    expect(router.state.location.state).toEqual({ from: "/accounts?q=kaveri" });
  });

  it("flips between light and dark, and remembers the choice under the operator's own key", async () => {
    const user = userEvent.setup();
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");

    const toggle = screen.getAllByRole("button", { name: "Switch to dark theme" })[0] as HTMLElement;
    expect(toggle.getAttribute("aria-pressed")).toBe("false");
    await user.click(toggle);

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem(OPERATOR_THEME_KEY)).toBe("dark");
    expect(screen.getAllByRole("button", { name: "Switch to light theme" })[0]?.getAttribute("aria-pressed")).toBe("true");
  });

  it("moves focus to the main content when the screen changes, so a keyboard user lands on the new page", async () => {
    const user = userEvent.setup();
    renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "Overview" });

    await user.click(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Accounts" }));

    await screen.findByRole("heading", { level: 1, name: "Accounts" });
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById("main-content")));
  });

  it("jumps to the accounts list from the sidebar search, with the query", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/");
    await screen.findByRole("heading", { level: 1, name: "Overview" });

    await user.type(screen.getByRole("searchbox", { name: "Find an account" }), "kaveri{Enter}");

    expect(router.state.location.pathname).toBe("/accounts");
    expect(router.state.location.search).toBe("?q=kaveri");
    await screen.findByText("Kaveri Steel");
  });
});

describe("a screen that breaks", () => {
  it("is shown as one inside the shell, so the navigation is still there to leave by", async () => {
    const user = userEvent.setup();
    // An answer in a shape the screen was not built for: it throws while rendering.
    vi.spyOn(client, "getOverview").mockResolvedValue({} as never);
    const quiet = vi.spyOn(console, "error").mockImplementation(() => undefined);
    renderConsole("/");

    expect(await screen.findByText("This screen couldn't be shown")).toBeDefined();
    expect(screen.getByRole("button", { name: "Reload the page" })).toBeDefined();
    // The rail is intact, and another screen opens from it.
    await user.click(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name: "Accounts" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Accounts" })).toBeDefined();
    quiet.mockRestore();
  });
});

describe("navigation by role", () => {
  it("offers an owner everything", async () => {
    renderConsole("/", ["owner"]);
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(navLinks()).toEqual([
      "Overview",
      "Accounts",
      "Subscriptions",
      "Plans & Pricing",
      "Grants & Credits",
      "Audit log",
    ]);
  });

  it("offers billing_ops the commercial screens and not the audit log", async () => {
    renderConsole("/", ["billing_ops"]);
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(navLinks()).toEqual(["Overview", "Accounts", "Subscriptions", "Plans & Pricing", "Grants & Credits"]);
  });

  it("offers support accounts and the audit log only, and leaves out the group that would be empty", async () => {
    renderConsole("/", ["support"]);
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(navLinks()).toEqual(["Overview", "Accounts", "Audit log"]);
    expect(screen.queryByText("Commerce")).toBeNull();
  });

  it("offers an operator with no recognised role nothing to open", async () => {
    renderConsole("/", ["auditor"]);
    await screen.findByRole("heading", { level: 1, name: "Overview" });
    expect(screen.queryByRole("navigation", { name: "Primary" })?.querySelectorAll("a") ?? []).toHaveLength(0);
  });
});

describe("reaching every screen", () => {
  const SCREENS: [string, string][] = [
    ["Overview", "/"],
    ["Accounts", "/accounts"],
    ["Subscriptions", "/subscriptions"],
    ["Plans & Pricing", "/plans"],
    ["Grants & Credits", "/grants"],
    ["Audit log", "/audit"],
  ];

  for (const [name, path] of SCREENS) {
    it(`${name} opens from the navigation, with its own h1, at ${path}`, async () => {
      const user = userEvent.setup();
      const { router } = renderConsole(name === "Overview" ? "/accounts" : "/");
      await screen.findByRole("heading", { level: 1 });

      await user.click(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name }));

      expect(await screen.findByRole("heading", { level: 1, name })).toBeDefined();
      expect(router.state.location.pathname).toBe(path);
      // The link to the screen being shown says so.
      expect(within(screen.getByRole("navigation", { name: "Primary" })).getByRole("link", { name }).getAttribute("aria-current")).toBe("page");
    });
  }

  it("shows an unknown page as one, inside the shell, with a way back", async () => {
    const user = userEvent.setup();
    const { router } = renderConsole("/nowhere");

    expect(await screen.findByRole("heading", { level: 1, name: "Page not found" })).toBeDefined();
    expect(screen.getByText("That console page doesn't exist. Head back to the overview.")).toBeDefined();
    expect(navLinks()).toContain("Overview");
    await user.click(screen.getByRole("link", { name: "Back to overview" }));
    expect(router.state.location.pathname).toBe("/");
    expect(await screen.findByRole("heading", { level: 1, name: "Overview" })).toBeDefined();
  });
});
