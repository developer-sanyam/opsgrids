import { describe, expect, it, vi } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";

import { RETAIL_CATEGORY_LIST, RETAIL_PRODUCTS } from "@/api/fixtures";
import type { Category, Product } from "@/api/types";
import { SECTION_DEFS, defaultSettings } from "../sections";
import type { SectionInstance, SettingValue } from "../schema";
import { SectionRenderer } from "./SectionRenderer";

/**
 * **Adoption, swept rather than listed.**
 *
 * `resolveImageSrc` is only worth having if every renderer goes through it. A component that
 * builds its own `src` is not a visible bug — it renders a merchant's pasted URL perfectly well
 * and fails only for uploads, in one section, which is the confusing failure ADR-0009 §6 names.
 * So this does not name the components: it walks every section the library declares, sets every
 * `image` field to an uploaded path, and fails on any renderer that puts that path in a tag
 * unchanged. A section added tomorrow is covered the day it is added.
 *
 * The base is injected by mocking the module's default rather than the env, so the assertion is
 * "this value passed through the resolver" and not "this string happens to look right".
 */

const BASE = "https://cdn.example.test/opsgrids-public";
const KEY = "9a3f1c72-6b1e-4d0a-9a7f-2f5b8c1d4e60/3b7e9d01-4a2c-4f88-b5d1-77c0a9e2f331";
const PATH = `/a/${KEY}`;
const RESOLVED = `${BASE}/${KEY}`;

vi.mock("./assetSrc", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./assetSrc")>();
  return {
    ...actual,
    resolveImageSrc: (value: SettingValue) => actual.resolveImageSrc(value, BASE),
  };
});

function renderSection(
  type: string,
  settings: Record<string, SettingValue> = {},
  products: Product[] = RETAIL_PRODUCTS,
  categories: Category[] = RETAIL_CATEGORY_LIST,
) {
  const section: SectionInstance = {
    id: `asset-${type}`,
    type,
    settings: { ...defaultSettings(type), ...settings },
    layout: { x: 0, y: 0, w: 12, h: 8 },
  };
  return render(
    <MemoryRouter>
      <SectionRenderer
        section={section}
        products={products}
        categories={categories}
        stacked={false}
        width={1280}
        mode="retail"
      />
    </MemoryRouter>,
  );
}

function srcsIn(container: HTMLElement): string[] {
  return [...container.querySelectorAll("img")].map((img) => img.getAttribute("src") ?? "");
}

const IMAGE_SECTIONS = SECTION_DEFS.filter((def) =>
  def.fields.some((field) => field.type === "image"),
);

describe("every section with an image setting resolves it", () => {
  it("finds sections to sweep at all", () => {
    // A guard on the sweep itself: if the library ever stops declaring `image` fields — or this
    // filter stops matching them — the loop below would pass by being empty.
    expect(IMAGE_SECTIONS.length).toBeGreaterThan(5);
  });

  for (const def of IMAGE_SECTIONS) {
    it(`${def.type} sends an uploaded path through the resolver`, () => {
      const settings = Object.fromEntries(
        def.fields.filter((field) => field.type === "image").map((field) => [field.key, PATH]),
      );
      const { container } = renderSection(def.type, settings);
      const srcs = srcsIn(container);

      expect(srcs).not.toContain(PATH);
      expect(srcs).toContain(RESOLVED);
    });
  }
});

describe("catalogue imagery resolves too", () => {
  it("a product card resolves Product.image_url", () => {
    const first = RETAIL_PRODUCTS[0];
    if (first === undefined) throw new Error("the retail fixture catalogue is empty");
    const product: Product = { ...first, image_url: PATH };

    const { container } = renderSection("product-grid", {}, [product]);
    const srcs = srcsIn(container);

    expect(srcs).not.toContain(PATH);
    expect(srcs).toContain(RESOLVED);
  });

  it("a category tile resolves Category.image_url", () => {
    const first = RETAIL_CATEGORY_LIST[0];
    if (first === undefined) throw new Error("the retail fixture category list is empty");
    const category: Category = { ...first, image_url: PATH };

    // `photo` is the style that shows the merchant's own art; the default style draws type only.
    const { container } = renderSection("category-tiles", { style: "photo" }, RETAIL_PRODUCTS, [
      category,
    ]);
    const srcs = srcsIn(container);

    expect(srcs).not.toContain(PATH);
    expect(srcs).toContain(RESOLVED);
  });
});
