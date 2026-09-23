import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Category, Page, Product, StoreResponse } from "@/api/types";
import type { Brand } from "@/theme/schema";
import { adoptTheme } from "@/theme/store";
import { THEMES } from "@/theme/themes";

import { LiveStorePage } from "../LiveStore";

/**
 * The buyer's route. What is worth pinning here is what a jsdom test *can* see: that no console
 * chrome is in the tree, that every store page has a live address, that a store nobody adopted
 * sends the visitor to the gallery instead of rendering an empty shop, and that the tab and the
 * way back behave like a site rather than a preview pane.
 *
 * Layout — the store filling the viewport, the preview control clearing the content at 390px — is
 * not testable here and was checked in a browser.
 */

const getStore = vi.fn<() => Promise<StoreResponse>>();
const getStoreBrand = vi.fn<(themeKey: string) => Promise<Brand>>();
const listProducts = vi.fn<() => Promise<Page<Product>>>();
const listCategories = vi.fn<() => Promise<Category[]>>();

vi.mock("@/api/client", () => ({
  getStore: () => getStore(),
  getStoreBrand: (themeKey: string) => getStoreBrand(themeKey),
  listProducts: () => listProducts(),
  listCategories: () => listCategories(),
}));

const BRAND: Brand = { name: "Verma Traders", tagline: "Since 1994", logoText: "VT" };

function shipped() {
  const theme = THEMES[0];
  if (!theme) throw new Error("The theme library is empty");
  return adoptTheme(theme);
}

beforeEach(() => {
  listProducts.mockResolvedValue({ items: [], total: 0 });
  listCategories.mockResolvedValue([]);
  getStore.mockResolvedValue(shipped());
  getStoreBrand.mockResolvedValue(BRAND);
  // jsdom has no scroll box; the route only needs the call to be observable.
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
  document.head.querySelector('link[rel="icon"]')?.remove();
  const icon = document.createElement("link");
  icon.setAttribute("rel", "icon");
  icon.setAttribute("href", "/favicon.svg");
  document.head.append(icon);
  document.title = "OpsGrids Storefront";
});

function renderLive(path = "/live") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/" element={<h1>Theme gallery</h1>} />
        <Route path="/editor" element={<h1>Design your store</h1>} />
        <Route path="/live/*" element={<LiveStorePage />} />
      </Routes>
    </MemoryRouter>,
  );
}

/** A store link as the renderer writes it — against the console's `/store` base. */
function storeLinkTo(consoleHref: string): HTMLElement {
  const link = screen
    .getAllByRole("link")
    .find((candidate) => candidate.getAttribute("href") === consoleHref);
  if (!link) throw new Error(`No store link to ${consoleHref}`);
  return link;
}

describe("the live store", () => {
  it("renders the adopted store with none of the console around it", async () => {
    renderLive();

    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));

    // The store's own chrome is the only chrome: the navigation landmarks are the preview control
    // and the store's nav, and nothing from the console's shell or its framed preview is present.
    const navNames = screen
      .getAllByRole("navigation")
      .map((nav) => nav.getAttribute("aria-label"));
    expect(navNames).toContain("Preview controls");
    expect(navNames).not.toContain("Main");
    expect(screen.queryByRole("link", { name: "Help & Getting Started" })).toBeNull();
    expect(screen.queryByRole("group", { name: "Preview width" })).toBeNull();
    expect(screen.queryByText(/Sample data/i)).toBeNull();
    expect(screen.queryByText(/Your storefront/i)).toBeNull();
  });

  it("gives every store page an address under /live", async () => {
    renderLive("/live/catalogue");
    await waitFor(() => expect(document.title).toBe(`Catalogue · ${BRAND.name}`));

    renderLive("/live/product");
    await waitFor(() => expect(document.title).toBe(`Line detail · ${BRAND.name}`));

    renderLive("/live");
    await waitFor(() => expect(document.title).toBe(BRAND.name));
  });

  it("keeps the store's own links inside the live site", async () => {
    const user = userEvent.setup();
    renderLive();
    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));

    await user.click(storeLinkTo("/store/catalogue"));

    // Still on the live site, now on its catalogue page — not dropped into the console's preview.
    await waitFor(() => expect(document.title).toBe(`Catalogue · ${BRAND.name}`));
    expect(screen.queryByRole("heading", { name: "Theme gallery" })).toBeNull();
  });

  it("dresses the browser tab as the store, and puts it back on the way out", async () => {
    const { unmount } = renderLive();
    await waitFor(() => expect(document.title).toBe(BRAND.name));

    const icon = document.head.querySelector('link[rel="icon"]');
    expect(icon?.getAttribute("href")?.startsWith("data:image/svg+xml,")).toBe(true);

    unmount();
    expect(document.title).toBe("OpsGrids Storefront");
    expect(document.head.querySelector('link[rel="icon"]')?.getAttribute("href")).toBe(
      "/favicon.svg",
    );
  });

  it("offers a way back that a keyboard reaches first", async () => {
    const user = userEvent.setup();
    renderLive();
    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));

    const controls = screen.getByRole("navigation", { name: "Preview controls" });
    expect(controls).toBeDefined();

    await user.tab();
    const editor = screen.getByRole("link", { name: "Editor" });
    expect(document.activeElement).toBe(editor);
    expect(editor.getAttribute("href")).toBe("/editor");
    expect(screen.getByRole("link", { name: "Gallery" }).getAttribute("href")).toBe("/");

    await user.keyboard("{Enter}");
    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Design your store" })).toBeDefined(),
    );
  });

  it("puts a visitor at the top of a page they have just opened", async () => {
    const user = userEvent.setup();
    renderLive();
    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));

    await user.click(storeLinkTo("/store/catalogue"));

    await waitFor(() =>
      expect(window.scrollTo).toHaveBeenCalledWith(
        expect.objectContaining({ top: 0, left: 0 }),
      ),
    );
  });

  it("sends a visitor to the gallery when no theme has been adopted", async () => {
    getStore.mockResolvedValue(null);
    renderLive();

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: "Theme gallery" })).toBeDefined(),
    );
    // Not an empty shop front: no store chrome was rendered on the way past.
    expect(screen.queryByText(BRAND.name)).toBeNull();
  });

  it("says what failed and retries without a reload", async () => {
    const user = userEvent.setup();
    getStore.mockRejectedValueOnce(new Error("unreachable"));
    renderLive();

    await waitFor(() => expect(screen.getByText("This store didn't load")).toBeDefined());

    await user.click(screen.getByRole("button", { name: "Try again" }));
    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));
  });

  it("reserves the page while the store is in flight", () => {
    renderLive();
    expect(screen.getByRole("status").textContent).toBe("Loading the store");
  });
});
