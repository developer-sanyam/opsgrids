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

import { useNavGroups } from "./useNavGroups";

function NavProbe() {
  const { groups } = useNavGroups();
  return (
    <nav>
      {groups.map((group) => (
        <div key={group.title}>
          <p>{group.title}</p>
          {group.items.map((item) => (
            <a key={item.to} href={item.to}>
              {item.label}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}

function renderNav() {
  return render(
    <MemoryRouter>
      <NavProbe />
    </MemoryRouter>,
  );
}

/** The one link pointing at `href`, or a failure naming what was there instead. */
function linkTo(href: string): HTMLAnchorElement {
  const matches = screen.getAllByRole("link").filter((link) => link.getAttribute("href") === href);
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

describe("the storefront navigation", () => {
  it("offers the live store, not only the console's framed preview", () => {
    renderNav();
    expect(linkTo("/live")).toBeTruthy();
    expect(linkTo("/store")).toBeTruthy();
  });

  it("names the two destinations apart", () => {
    renderNav();
    expect(linkTo("/live").textContent).toContain("Open the live store");
    expect(linkTo("/live").textContent).not.toContain("Preview");
    // The group heading is what says which kind of looking this is.
    expect(screen.getByText("Console preview")).toBeTruthy();
    expect(screen.getByText("Live store")).toBeTruthy();
  });

  /**
   * The store's pages come from the store document, not a fixed list here: the preview screen's
   * own page switcher, and the store's nav inside the frame. What the navigation owes is the way
   * in.
   */
  it("offers the delivery admin, which shipped reachable only by typing its URL", () => {
    renderNav();
    expect(linkTo("/delivery").textContent).toContain("Delivery");
  });

  it("holds an entry into the preview, not a copy of the store's page list", () => {
    renderNav();
    const previewLinks = screen
      .getAllByRole("link")
      .filter((link) => (link.getAttribute("href") ?? "").startsWith("/store"));
    expect(previewLinks).toHaveLength(1);
    expect(previewLinks[0]?.getAttribute("href")).toBe("/store");
  });
});
