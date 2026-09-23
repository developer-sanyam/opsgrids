import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Category, Page, Product, StoreResponse } from "@/api/types";
import type { Brand } from "@/theme/schema";
import { adoptTheme } from "@/theme/store";
import { THEMES } from "@/theme/themes";

import { EditorPage } from "./index";

/**
 * The screen's four states, and a smoke test that the real theme library, the real section
 * definitions and the real renderer compose into an editor without anything throwing.
 */

const BRAND: Brand = { name: "Verma Traders", tagline: "Since 1994", logoText: "VT" };

const getStore = vi.fn<() => Promise<StoreResponse>>();
const getStoreBrand = vi.fn<(themeKey: string) => Promise<Brand>>();
const listProducts = vi.fn<() => Promise<Page<Product>>>();
const listCategories = vi.fn<() => Promise<Category[]>>();

vi.mock("@/api/client", () => ({
  getStore: () => getStore(),
  getStoreBrand: (themeKey: string) => getStoreBrand(themeKey),
  listProducts: () => listProducts(),
  listCategories: () => listCategories(),
  saveStore: vi.fn(async (doc: unknown) => doc),
}));

function shipped() {
  const theme = THEMES[0];
  if (!theme) throw new Error("The theme library is empty");
  return adoptTheme(theme);
}

beforeEach(() => {
  listProducts.mockResolvedValue({ items: [], total: 0 });
  listCategories.mockResolvedValue([]);
  getStoreBrand.mockResolvedValue(BRAND);
});

function renderPage() {
  render(
    <MemoryRouter>
      <EditorPage />
    </MemoryRouter>,
  );
}

describe("the editor screen", () => {
  it("reserves the editor's shape while it loads, then shows the store", async () => {
    getStore.mockResolvedValue(shipped());
    renderPage();

    expect(screen.getByText("Design your store")).toBeDefined();

    await waitFor(() => expect(screen.getByLabelText("Page")).toBeDefined());
    expect(screen.getByRole("heading", { name: "Sections" })).toBeDefined();
    expect(screen.getByText("Nothing selected")).toBeDefined();
  });

  it("sends a merchant with no theme to the gallery", async () => {
    getStore.mockResolvedValue(null);
    renderPage();

    await waitFor(() => expect(screen.getByText("You haven't adopted a theme yet")).toBeDefined());
    expect(screen.getByRole("link", { name: "Browse themes" }).getAttribute("href")).toBe("/");
  });

  it("says what failed and offers to try again", async () => {
    getStore.mockRejectedValue(new Error("The store service is unreachable."));
    renderPage();

    await waitFor(() =>
      expect(screen.getByText("The store service is unreachable.")).toBeDefined(),
    );

    const user = userEvent.setup();
    getStore.mockResolvedValue(shipped());
    await user.click(screen.getByRole("button", { name: "Try again" }));

    await waitFor(() => expect(screen.getByLabelText("Page")).toBeDefined());
  });

  it("composes a real theme's first page onto the canvas", async () => {
    const store = shipped();
    getStore.mockResolvedValue(store);
    renderPage();

    await waitFor(() => expect(screen.getByLabelText("Page")).toBeDefined());

    const first = store.pages[0]?.sections[0];
    expect(first).toBeDefined();
    // Every section on the page is a selectable frame whose name states where it sits.
    const frames = screen.getAllByRole("button", { name: /, column \d+, row \d+,/ });
    expect(frames.length).toBe(store.pages[0]?.sections.length);
  });
});
