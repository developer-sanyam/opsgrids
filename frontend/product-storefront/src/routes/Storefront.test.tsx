/**
 * The console's framed preview of the store.
 *
 * Mostly a regression test for R28: the screen used to render a "no storefront service" chip
 * unconditionally, which was false the moment `services/storefront` and `GET/PUT
 * /storefront/theme` shipped. It must say nothing of the kind in live mode.
 */

import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { Category, Page, Product, StoreResponse } from "@/api/types";
import type { Brand } from "@/theme/schema";
import { adoptTheme } from "@/theme/store";
import { THEMES } from "@/theme/themes";

import { StorefrontPage } from "./Storefront";

const getStore = vi.fn<() => Promise<StoreResponse>>();
const getStoreBrand = vi.fn<(themeKey: string) => Promise<Brand>>();
const listProducts = vi.fn<() => Promise<Page<Product>>>();
const listCategories = vi.fn<() => Promise<Category[]>>();

vi.mock("@/api/client", () => ({
  getStore: () => getStore(),
  getStoreBrand: (themeKey: string) => getStoreBrand(themeKey),
  listProducts: () => listProducts(),
  listCategories: () => listCategories(),
  // Live mode: the storefront service and `/storefront/theme` both exist (R28), so this screen
  // must never claim otherwise. `false` is the production default.
  USE_FIXTURES: false,
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
  getStoreBrand.mockResolvedValue(BRAND);
});

function renderStorefront(path = "/store") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="store/*" element={<StorefrontPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("the sample-data chip (R28)", () => {
  it("shows no sample-data chip at all in live mode", async () => {
    getStore.mockResolvedValue(shipped());
    renderStorefront();

    await waitFor(() => expect(screen.getAllByText(BRAND.name).length).toBeGreaterThan(0));
    expect(screen.queryByText("Sample data")).toBeNull();
  });

  it("never claims there is no storefront service, whatever state the store is in", async () => {
    getStore.mockResolvedValue(null);
    const { container } = renderStorefront();

    await waitFor(() => expect(screen.getByText("No theme adopted yet")).toBeDefined());

    // `services/storefront` and `GET/PUT /storefront/theme` both exist — nothing on this screen,
    // including a chip's hover title, may say otherwise.
    expect(container.innerHTML).not.toMatch(/no storefront service/i);
  });
});
