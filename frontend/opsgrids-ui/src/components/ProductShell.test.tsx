import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { BoardIcon, HomeIcon, PlusIcon, SettingsIcon, TargetIcon, UsersIcon } from "../icons";
import { ProductShell } from "./ProductShell";
import { ThemeProvider } from "./ThemeProvider";

beforeEach(() => {
  window.scrollTo = vi.fn();
  localStorage.clear();
});

type Props = ComponentProps<typeof ProductShell>;

const BASE: Props = {
  product: { name: "OpsGrids Revenue", mark: <span>Logo</span> },
  nav: [
    {
      title: "Pipeline",
      items: [
        { to: "/", label: "Overview", icon: HomeIcon, end: true },
        { to: "/leads", label: "Leads", icon: TargetIcon },
        { to: "/deals", label: "Deals", icon: BoardIcon },
        { to: "/contacts", label: "Contacts", icon: UsersIcon },
      ],
    },
  ],
  pinned: [{ to: "/settings", label: "Settings", icon: SettingsIcon }],
  modules: [],
  adminLinks: [],
  user: { name: "Sofia Marchetti", email: "sofia@acme.example", roleLabel: "Sales Manager" },
  onSignOut: vi.fn(),
  searchPlaceholder: "Search deals",
  searchFallback: { label: "Search deals for", to: "/deals" },
};

function renderShell(props: Partial<Props> = {}, path = "/") {
  return render(
    <ThemeProvider storageKey="test.theme">
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route element={<ProductShell {...BASE} {...props} />}>
            <Route path="*" element={<p>screen</p>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

describe("ProductShell", () => {
  it("renders nothing until the signed-in user is known", () => {
    renderShell({ user: null });
    expect(screen.queryByText("screen")).toBeNull();
  });

  it("renders the routed screen inside the shell once it is", () => {
    renderShell();
    expect(screen.getByText("screen")).toBeTruthy();
  });

  describe("the permissions banner", () => {
    const copy = { title: "Permissions couldn't be checked", body: "Controls are hidden until this succeeds." };

    it("says so, once, with a retry, when the grants fetch failed", async () => {
      const reload = vi.fn();
      renderShell({ grants: { status: "error", reload }, bannerCopy: copy });
      const banner = screen.getByRole("status");
      expect(within(banner).getByText("Permissions couldn't be checked")).toBeTruthy();
      expect(within(banner).getByText("Controls are hidden until this succeeds.")).toBeTruthy();
      await userEvent.click(within(banner).getByRole("button", { name: "Try again" }));
      expect(reload).toHaveBeenCalledTimes(1);
    });

    it("is absent while grants are loading or ready", () => {
      renderShell({ grants: { status: "ready", reload: vi.fn() }, bannerCopy: copy });
      expect(screen.queryByText("Permissions couldn't be checked")).toBeNull();
    });

    it("is absent for an app that gives no copy, even when the fetch failed", () => {
      renderShell({ grants: { status: "error", reload: vi.fn() } });
      expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
    });

    it("sits beside a product's own extra banner", () => {
      renderShell({
        grants: { status: "error", reload: vi.fn() },
        bannerCopy: copy,
        extraBanners: <p>Directory is down</p>,
      });
      expect(screen.getByText("Directory is down")).toBeTruthy();
      expect(screen.getByText("Permissions couldn't be checked")).toBeTruthy();
    });
  });

  describe("the bottom navigation", () => {
    function bottomBar() {
      return screen.getAllByRole("navigation").find((nav) => within(nav).queryByText("More") !== null) as HTMLElement;
    }

    it("shows the named routes with the sidebar's own labels, between Home and More", () => {
      renderShell({ bottomNavExtra: ["/leads", "/deals", "/contacts"] });
      const bar = bottomBar();
      expect(within(bar).getByText("Home")).toBeTruthy();
      expect(within(bar).getByText("Leads")).toBeTruthy();
      expect(within(bar).getByText("Deals")).toBeTruthy();
      expect(within(bar).getByText("Contacts")).toBeTruthy();
      expect(within(bar).getByText("More")).toBeTruthy();
    });

    it("skips a route the nav does not offer instead of showing a dead tab", () => {
      renderShell({ bottomNavExtra: ["/leads", "/gone"] });
      const bar = bottomBar();
      expect(within(bar).getByText("Leads")).toBeTruthy();
      expect(within(bar).queryByText("gone")).toBeNull();
    });

    it("never holds more than five", () => {
      renderShell({ bottomNavExtra: ["/leads", "/deals", "/contacts", "/settings"] });
      expect(within(bottomBar()).getAllByRole("link").length + within(bottomBar()).getAllByRole("button").length).toBeLessThanOrEqual(5);
    });
  });

  describe("quick actions", () => {
    it("opens the route the action names", async () => {
      renderShell({ quickActions: [{ key: "new-deal", label: "New deal", icon: PlusIcon, to: "/deals/new" }] }, "/");
      await userEvent.click(screen.getByRole("button", { name: "New" }));
      await userEvent.click(await screen.findByRole("button", { name: "New deal" }));
      expect(await screen.findByText("screen")).toBeTruthy();
    });

    it("offers no raised button when there are none", () => {
      renderShell();
      expect(screen.queryByRole("button", { name: "New" })).toBeNull();
    });
  });
});
