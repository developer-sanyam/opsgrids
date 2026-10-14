/**
 * The gallery as a destination someone was *sent* to.
 *
 * `/live` bounces a visitor here when no theme has been adopted, carrying the reason on the
 * navigation state. The reason was being dropped, so the visitor arrived at a theme picker with no
 * explanation — a redirect with no sentence attached reads as a bug. These pin that it is rendered
 * when it is there, that nothing appears when it is not, and that a state carrying something other
 * than a notice is ignored rather than stringified onto the screen.
 *
 * Also here: both ways of looking at the store, named apart. The gallery's own header used to
 * offer only the console's framed preview.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import type { Category, Page, Product, StoreResponse } from "@/api/types";
import type { Theme } from "@/theme/schema";
import { THEMES } from "@/theme/themes";

import { GalleryPage } from "./Gallery";

const listThemes = vi.fn<() => Promise<Theme[]>>();
const listProducts = vi.fn<() => Promise<Page<Product>>>();
const listCategories = vi.fn<() => Promise<Category[]>>();
const getStore = vi.fn<() => Promise<StoreResponse>>();
const removeStore = vi.fn<() => Promise<void>>();

vi.mock("@/api/client", () => ({
  listThemes: () => listThemes(),
  listProducts: () => listProducts(),
  listCategories: () => listCategories(),
  getStore: () => getStore(),
  adoptTheme: vi.fn(),
  removeStore: () => removeStore(),
  // Live mode: the storefront service and `/storefront/theme` both exist (R28), so this screen
  // must never claim otherwise. `false` is the production default — `USE_FIXTURES` is compiled
  // out entirely once `import.meta.env.DEV` is false.
  USE_FIXTURES: false,
}));

/** The sentence `/live` hands over when there is nothing to show a buyer. */
const NO_STORE_NOTICE =
  "There is no live store yet because no theme has been adopted. Pick one and its pages become your store.";

beforeEach(() => {
  // No themes: the picker's empty state is the cheapest render, and none of this is about cards.
  listThemes.mockResolvedValue([]);
  listProducts.mockResolvedValue({ items: [], total: 0 });
  listCategories.mockResolvedValue([]);
  getStore.mockResolvedValue(null);
  removeStore.mockReset();
  removeStore.mockResolvedValue(undefined);
});

function renderGallery(state?: unknown) {
  return render(
    <MemoryRouter initialEntries={[state === undefined ? "/" : { pathname: "/", state }]}>
      <GalleryPage />
    </MemoryRouter>,
  );
}

/** Settled means the async load has resolved — the header is present from the first paint. */
async function settled() {
  await waitFor(() => expect(listThemes).toHaveBeenCalled());
  await screen.findByRole("heading", { name: "Pick a theme" });
}

describe("the notice a redirect hands over", () => {
  it("renders it, word for word, as an informational alert", async () => {
    renderGallery({ notice: NO_STORE_NOTICE });
    await settled();

    const alert = await screen.findByText(NO_STORE_NOTICE);
    // Announced politely, because nothing the visitor did on this screen produced it — they
    // arrived already redirected, and the reason has to reach a screen reader too.
    expect(alert.closest('[role="status"]')).not.toBeNull();
  });

  it("says nothing at all when the visitor simply opened the gallery", async () => {
    renderGallery();
    await settled();

    expect(screen.queryByText(NO_STORE_NOTICE)).toBeNull();
    // The screen's own live region is `sr-only` and empty; no alert box is drawn.
    expect(screen.queryByText(/There is no live store/)).toBeNull();
  });

  it("ignores navigation state that carries no notice", async () => {
    renderGallery({ from: "somewhere", notice: 404 });
    await settled();

    expect(screen.queryByText("404")).toBeNull();
  });

  it("ignores a notice that is only whitespace", async () => {
    const { container } = renderGallery({ notice: "   " });
    await settled();

    expect(container.querySelectorAll('[role="status"]')).toHaveLength(1);
  });
});

describe("the two ways to look at the store", () => {
  it("offers the live store and the console preview, named apart", async () => {
    renderGallery();
    await settled();

    const live = screen.getByRole("link", { name: "Open the live store" });
    const console_ = screen.getByRole("link", { name: "Preview in the console" });
    expect(live.getAttribute("href")).toBe("/live");
    expect(console_.getAttribute("href")).toBe("/store");
  });

  it("names the live store once, in the header, even after a theme is adopted", async () => {
    const theme = THEMES[0];
    if (!theme) throw new Error("the theme library is empty");
    listThemes.mockResolvedValue([theme]);
    getStore.mockResolvedValue({
      themeKey: theme.key,
      mode: "trade",
      tokens: theme.tokens,
      pages: [],
    });

    renderGallery();
    await screen.findByText(`Your store is running the ${theme.name} theme`);

    const links = screen
      .getAllByRole("link", { name: "Open the live store" })
      .map((link) => link.getAttribute("href"));
    expect(links).toEqual(["/live"]);
  });
});

describe("removing the store", () => {
  async function renderAdopted() {
    const theme = THEMES[0];
    if (!theme) throw new Error("the theme library is empty");
    listThemes.mockResolvedValue([theme]);
    getStore.mockResolvedValue({ themeKey: theme.key, mode: "trade", tokens: theme.tokens, pages: [] });
    renderGallery();
    await screen.findByText(`Your store is running the ${theme.name} theme`);
  }

  it("keeps the destructive action out of the header row, in an overflow menu", async () => {
    await renderAdopted();
    expect(screen.queryByRole("button", { name: "Remove this store" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Store options" }));
    const item = screen.getByRole("menuitem", { name: "Remove this store" });
    expect(item.className).toContain("text-negative");
  });

  it("asks first, says what is lost, and removes nothing until it is confirmed", async () => {
    await renderAdopted();
    fireEvent.click(screen.getByRole("button", { name: "Store options" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove this store" }));

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Every page in your store is deleted/)).toBeTruthy();
    expect(removeStore).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(removeStore).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "Store options" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Remove this store" }));
    fireEvent.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Remove this store" }));
    await waitFor(() => expect(removeStore).toHaveBeenCalledOnce());
  });
});

describe("the sample-data chip (R28)", () => {
  it("shows no sample-data chip at all in live mode", async () => {
    renderGallery();
    await settled();

    expect(screen.queryByText("Sample data")).toBeNull();
  });

  it("never claims there is no storefront service, in live mode or otherwise", async () => {
    const { container } = renderGallery();
    await settled();

    // `services/storefront` and `GET/PUT /storefront/theme` both exist (R28) — nothing on this
    // screen, including a chip's hover title, may say otherwise.
    expect(container.innerHTML).not.toMatch(/no storefront service/i);
  });
});
