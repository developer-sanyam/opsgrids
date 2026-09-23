/**
 * Where a page shows up in the store's chrome.
 *
 * `StoreRenderer` used to put every page in `store.pages` into the top bar, which is fine at the
 * three a theme ships and unusable at the thirty-five a real sitemap has. `PageDoc.nav` places
 * them instead — and the first thing pinned here is that it changes nothing for a store that never
 * sets it, on every theme this build ships, because that is the promise the optional field makes.
 */

import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { defaultSettings } from "../sections";
import type { NavPlacement, SectionInstance, StoreDoc } from "../schema";
import { adoptTheme, navPlacement, pagesInNav } from "../store";
import { foundry, suggestedBrand, THEMES } from "../themes";
import { StoreRenderer } from "./StoreRenderer";

const BRAND = suggestedBrand(foundry.key);

function renderStore(store: StoreDoc, route = "/") {
  return render(
    <MemoryRouter initialEntries={["/store"]}>
      <StoreRenderer
        store={store}
        brand={BRAND}
        route={route}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        width={1200}
      />
    </MemoryRouter>,
  );
}

function base(): StoreDoc {
  return adoptTheme(foundry);
}

/** The same store with one extra page, placed. */
function withExtraPage(nav: NavPlacement | undefined, sections: SectionInstance[] = []): StoreDoc {
  const store = base();
  return {
    ...store,
    pages: [
      ...store.pages,
      {
        id: "terms",
        route: "/terms",
        name: "Terms",
        ...(nav !== undefined ? { nav } : {}),
        sections,
      },
    ],
  };
}

function navNames(label: string): string[] {
  const nav = screen.queryByRole("navigation", { name: label });
  if (!nav) return [];
  return within(nav)
    .getAllByRole("link")
    .map((link) => link.textContent ?? "");
}

describe.each(THEMES.map((theme) => [theme.key, theme] as const))(
  "the %s theme, which says nothing about navigation",
  (_key, theme) => {
    it("puts every page it ships in the top bar, exactly as it did before the field existed", () => {
      const store = adoptTheme(theme);
      expect(store.pages.every((page) => page.nav === undefined)).toBe(true);
      expect(pagesInNav(store, "primary")).toHaveLength(store.pages.length);
      expect(pagesInNav(store, "footer")).toHaveLength(0);

      const view = renderStore(store);
      expect(navNames("Store")).toEqual(store.pages.map((page) => page.name));
      // No second nav appears out of nowhere on a store that placed nothing in the footer.
      expect(screen.queryByRole("navigation", { name: "More" })).toBeNull();
      view.unmount();
    });
  },
);

describe("a page the merchant placed", () => {
  it("stays out of the top bar and turns up in the footer", () => {
    renderStore(withExtraPage("footer"));

    expect(navNames("Store")).toEqual(["Home", "Catalogue", "Line detail"]);
    expect(navNames("More")).toEqual(["Terms"]);
    // Still one footer landmark, and on a page with no footer of its own the nav is inside it.
    const footers = screen.getAllByRole("contentinfo");
    expect(footers).toHaveLength(1);
    expect(within(footers[0] as HTMLElement).getByRole("link").textContent).toBe("Terms");
  });

  it("appears nowhere at all when it is set to none", () => {
    renderStore(withExtraPage("none"));

    expect(navNames("Store")).toEqual(["Home", "Catalogue", "Line detail"]);
    expect(screen.queryByRole("navigation", { name: "More" })).toBeNull();
    // The page is still in the document and still reachable by its address — hiding it from the
    // chrome is presentation, not deletion.
    expect(screen.queryByText("Terms")).toBeNull();
  });

  it("joins the top bar in page order when it is placed there", () => {
    const store = withExtraPage("primary");
    renderStore({ ...store, pages: [store.pages[3]!, ...store.pages.slice(0, 3)] });
    expect(navNames("Store")).toEqual(["Terms", "Home", "Catalogue", "Line detail"]);
  });

  it("is still reachable at its own address", () => {
    renderStore(withExtraPage("none"), "/terms");
    expect(screen.queryByText("This store has no page at that address")).toBeNull();
  });
});

/**
 * The one place the two footer mechanisms meet.
 *
 * `nav: "footer"` is the chrome's automatic list; `footer-columns` is a footer the merchant wrote.
 * The section owns the `contentinfo` — one page, one footer landmark — and the placed pages drop
 * below it as a legal strip. Suppressing them instead was the first shape of this, and it meant a
 * merchant on quickcart or atelier, whose every page ships `footer-columns`, could set "In the
 * footer" and watch nothing happen anywhere in their store.
 */
describe("a page that wrote its own footer", () => {
  const authored: SectionInstance = {
    id: "sec-footer",
    type: "footer-columns",
    settings: defaultSettings("footer-columns"),
    layout: { x: 0, y: 0, w: 12, h: 8 },
  };

  function withAuthoredFooter(nav: NavPlacement): StoreDoc {
    const store = withExtraPage(nav);
    const [home, ...rest] = store.pages;
    return { ...store, pages: [{ ...home!, sections: [authored] }, ...rest] };
  }

  it("keeps one footer landmark and still shows the pages placed in the footer", () => {
    renderStore(withAuthoredFooter("footer"));

    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(navNames("More")).toEqual(["Terms"]);
    // Outside the landmark: the merchant's footer is the footer, this is the strip under it.
    expect(document.querySelector('footer nav[aria-label="More"]')).toBeNull();
  });

  it("adds nothing at all when no page is placed in the footer", () => {
    renderStore(withAuthoredFooter("none"));

    expect(screen.getAllByRole("contentinfo")).toHaveLength(1);
    expect(screen.queryByRole("navigation", { name: "More" })).toBeNull();
  });
});

describe("reading a placement off a document that may predate the field", () => {
  it("treats anything but footer or none as the top bar", () => {
    expect(navPlacement({})).toBe("primary");
    expect(navPlacement({ nav: "footer" })).toBe("footer");
    expect(navPlacement({ nav: "none" })).toBe("none");
    expect(navPlacement({ nav: "sidebar" as NavPlacement })).toBe("primary");
  });
});
