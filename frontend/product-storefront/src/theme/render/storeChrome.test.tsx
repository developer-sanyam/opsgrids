/**
 * The store's chrome around the page: one footer, and links that point at the address the buyer is
 * actually on.
 *
 * Both were real defects. Two `<footer>`s rendered on every page carrying the tall footer section,
 * and every link the chrome wrote was hard-coded to the console's `/store` base — which on `/live`
 * meant `NavLink` compared a `/store` href against a `/live` location and marked nothing current,
 * so all three nav items painted the same grey and nobody could see which page they were on.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { defaultSettings } from "../sections";
import type { SectionInstance, StoreDoc } from "../schema";
import { adoptTheme } from "../store";
import { foundry, suggestedBrand } from "../themes";
import { StoreRenderer } from "./StoreRenderer";

const BRAND = suggestedBrand(foundry.key);

function instance(type: string): SectionInstance {
  return { id: `sec-${type}`, type, settings: defaultSettings(type), layout: { x: 0, y: 0, w: 12, h: 8 } };
}

/**
 * A store built by hand rather than borrowed from a theme: whether a particular theme's catalogue
 * page happens to carry a footer today is not what this is about, and a theme edit should not be
 * able to quietly delete the assertion.
 */
function storeWithSections(sections: SectionInstance[]): StoreDoc {
  const store = adoptTheme(foundry);
  const [home, ...rest] = store.pages;
  if (!home) throw new Error("a theme must ship a home page");
  return { ...store, pages: [{ ...home, sections }, ...rest] };
}

function renderStore(store: StoreDoc, { at = "/store", base }: { at?: string; base?: string } = {}) {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <StoreRenderer
        store={store}
        brand={BRAND}
        route="/"
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        width={1200}
        {...(base ? { basePath: base } : {})}
      />
    </MemoryRouter>,
  );
}

describe("the footer", () => {
  it("is the chrome's own when the page has none", () => {
    renderStore(storeWithSections([instance("rich-text")]));

    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(screen.getByRole("contentinfo").textContent).toContain(suggestedBrand(foundry.key).name);
  });

  it("is the page's own when it places one — exactly one landmark either way", () => {
    renderStore(storeWithSections([instance("footer-columns")]));

    const footers = screen.getAllByRole("contentinfo");
    expect(footers).toHaveLength(1);
    // The merchant's footer, not the chrome's one-line sign-off underneath it.
    expect(footers[0]?.querySelectorAll("h2, h3").length).toBeGreaterThan(0);
  });
});

describe("the navigation's base", () => {
  const store = storeWithSections([instance("rich-text")]);
  const names = () => Array.from(document.querySelectorAll("nav a"));

  it("is the console's preview by default, and marks the page being previewed", () => {
    renderStore(store, { at: "/store" });

    expect(names().map((a) => a.getAttribute("href"))).toContain("/store/catalogue");
    expect(screen.getByRole("link", { current: "page" }).getAttribute("href")).toBe("/store");
  });

  it("follows the buyer's own site when one is given", () => {
    renderStore(store, { at: "/live", base: "/live" });

    const hrefs = names().map((a) => a.getAttribute("href"));
    expect(hrefs).toContain("/live");
    expect(hrefs).toContain("/live/catalogue");
    expect(hrefs.some((href) => href?.startsWith("/store"))).toBe(false);
  });

  it("marks the current page on the buyer's site — the link that had no current state at all", () => {
    render(
      <MemoryRouter initialEntries={["/live/catalogue"]}>
        <StoreRenderer
          store={store}
          brand={BRAND}
          route="/catalogue"
          products={PRODUCTS}
          categories={CATEGORY_LIST}
          width={1200}
          basePath="/live"
        />
      </MemoryRouter>,
    );

    const current = screen.getByRole("link", { current: "page" });
    expect(current.getAttribute("href")).toBe("/live/catalogue");
    expect(current.textContent).toBe("Catalogue");
  });
});
