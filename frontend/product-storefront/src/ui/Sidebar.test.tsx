/**
 * The navigation's one job that a test can hold it to: both ways of looking at the store are
 * reachable, and they are named apart.
 *
 * `/live` existed before this and was reachable only by typing the URL, which is the same as not
 * existing. `/store` frames the store inside the console; `/live` is what a buyer gets. Calling
 * both of them "Preview" would put the distinction the route was built for back out of sight.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { ThemeProvider } from "@opsgrids/ui";

import { AuthProvider } from "@/auth/AuthProvider";

import { Sidebar } from "./Sidebar";

/** The two providers the shell's own controls need — the session and the light/dark toggle. */
function renderSidebar() {
  return render(
    <MemoryRouter>
      <ThemeProvider storageKey="opsgrids.storefront.theme.test">
        <AuthProvider>
          <Sidebar />
        </AuthProvider>
      </ThemeProvider>
    </MemoryRouter>,
  );
}

/** The one link pointing at `href`, or a failure naming what was there instead. */
function linkTo(href: string): HTMLAnchorElement {
  const matches = screen
    .getAllByRole("link")
    .filter((link) => link.getAttribute("href") === href);
  const [first] = matches;
  if (!first) {
    const seen = screen
      .getAllByRole("link")
      .map((link) => `${link.getAttribute("href")} (${link.textContent?.trim()})`)
      .join(", ");
    throw new Error(`No nav link to ${href}. Links present: ${seen}`);
  }
  return first as HTMLAnchorElement;
}

describe("the storefront sidebar", () => {
  it("offers the live store, not only the console's framed preview", () => {
    renderSidebar();
    expect(linkTo("/live")).toBeTruthy();
    expect(linkTo("/store")).toBeTruthy();
  });

  it("names the two destinations apart", () => {
    renderSidebar();
    expect(linkTo("/live").textContent).toContain("Open the live store");
    expect(linkTo("/live").textContent).not.toContain("Preview");
    // The group heading is what says which kind of looking this is.
    expect(screen.getByText("Console preview")).toBeTruthy();
    expect(screen.getByText("Live store")).toBeTruthy();
  });

  /**
   * This replaces a test that asserted the three links `/store`, `/store/catalogue` and
   * `/store/product` — the three pages every theme shipped. A merchant can now add pages, so a
   * fixed list here is a navigation that cannot grow one, cannot lose a deleted one, and names
   * pages a merchant may have set to "Not in the navigation". The store's pages come from the
   * store document instead: the preview screen's own page switcher, and the store's nav inside
   * the frame. What the sidebar owes is the way in.
   */
  it("holds an entry into the preview, not a copy of the store's page list", () => {
    renderSidebar();

    const previewLinks = screen
      .getAllByRole("link")
      .filter((link) => (link.getAttribute("href") ?? "").startsWith("/store"));

    expect(previewLinks).toHaveLength(1);
    expect(previewLinks[0]?.getAttribute("href")).toBe("/store");
  });
});
