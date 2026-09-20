/**
 * The buyer-facing view: chrome from the brand, the right page for the route, a theme's tokens
 * landing as custom properties, and a route the store has no page for.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import { adoptTheme } from "../store";
import { circuit, foundry, suggestedBrand, THEMES } from "../themes";
import { StoreRenderer } from "./StoreRenderer";

function renderStore(themeKey: "foundry" | "circuit", route: string) {
  const theme = themeKey === "foundry" ? foundry : circuit;
  const store = adoptTheme(theme, suggestedBrand(theme.key));
  return render(
    <MemoryRouter>
      <StoreRenderer
        store={store}
        route={route}
        products={PRODUCTS}
        categories={CATEGORY_LIST}
        width={1200}
      />
    </MemoryRouter>,
  );
}

describe("the store's chrome", () => {
  it("shows the brand and a link to every page the store has", () => {
    renderStore("foundry", "/");

    expect(screen.getByText(suggestedBrand("foundry").name)).not.toBeNull();
    const nav = screen.getByRole("navigation", { name: "Store" });
    expect(nav.querySelectorAll("a")).toHaveLength(foundry.pages.length);
  });
});

describe("a theme's tokens", () => {
  it("land as custom properties on one wrapper, not on each section", () => {
    const { container } = renderStore("circuit", "/");
    const frame = container.querySelector<HTMLElement>("[data-storefront-frame]");
    if (!frame) throw new Error("the theme frame should render");

    expect(frame.style.getPropertyValue("--st-accent")).toBe(circuit.tokens.colorAccent);
    expect(frame.style.getPropertyValue("--st-bg")).toBe(circuit.tokens.colorBg);
    expect(frame.style.getPropertyValue("--st-radius")).toBe(`${circuit.tokens.radius}px`);
    expect(container.querySelectorAll("[data-storefront-frame]")).toHaveLength(1);
  });
});

describe("routing inside the store", () => {
  it("renders the page whose route matches", () => {
    renderStore("foundry", "/catalogue");
    expect(screen.getByRole("heading", { level: 1 })).toHaveProperty(
      "textContent",
      "Every line we hold, with the stock position",
    );
  });

  it("says so when the store has no page at that route", () => {
    renderStore("foundry", "/nowhere");
    expect(screen.getByText("This store has no page at that address")).not.toBeNull();
  });
});

describe.each(THEMES.map((theme) => [theme.key, theme] as const))(
  "the %s theme ships pages that render",
  (_key, theme) => {
    it("has a home, a catalogue and a product page", () => {
      expect(theme.pages.map((page) => page.route).sort()).toEqual([
        "/",
        "/catalogue",
        "/product",
      ]);
    });

    it("renders every page at desktop and stacked, with no section falling back", () => {
      const store = adoptTheme(theme, suggestedBrand(theme.key));
      for (const width of [1200, 390]) {
        for (const page of store.pages) {
          const view = render(
            <MemoryRouter>
              <StoreRenderer
                store={store}
                route={page.route}
                products={PRODUCTS}
                categories={CATEGORY_LIST}
                width={width}
              />
            </MemoryRouter>,
          );
          expect(view.queryByText("Section unavailable")).toBeNull();
          // Exactly one `h1` per page — the hero's — so the outline is right on every route.
          expect(view.getAllByRole("heading", { level: 1 })).toHaveLength(1);
          view.unmount();
        }
      }
    });
  },
);
