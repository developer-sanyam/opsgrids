/**
 * The merchant catalogue — `/storefront/categories` and `/storefront/products`.
 *
 * **Fixtures by default in dev**, as everywhere else in this app, seeded from the console's own
 * sample trade catalogue (`@/api/fixtures`) so a Products/Categories screen opened offline shows
 * the same lines the theme preview does. Every write in fixture mode mutates an in-memory copy —
 * the same shape `delivery/api.ts` uses for zones — so create/edit/delete are real state changes
 * a merchant can see reflected across the screen, not a promise the network never received.
 */

import { ApiError, apiRequest, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/client";
import { CATEGORY_LIST, PRODUCTS } from "@/api/fixtures";
import type {
  CategoryInput,
  CategoryOut,
  CategoryPatch,
  ListCategoriesParams,
  ListProductsParams,
  PagedResult,
  ProductInput,
  ProductOut,
  ProductPatchInput,
  ProductVariantOut,
} from "./types";

/* ------------------------------ fixture seed ------------------------------ */

const SEED_TIME = "2026-09-01T00:00:00Z";

function seedCategories(): CategoryOut[] {
  return CATEGORY_LIST.map((category, index) => ({
    id: `cat-${category.key}`,
    key: category.key,
    label: category.label,
    blurb: category.blurb,
    image_url: category.image_url,
    sort_order: index,
    is_active: true,
    line_count: PRODUCTS.filter((p) => p.category === category.key).length,
    created_at: SEED_TIME,
    updated_at: SEED_TIME,
  }));
}

function seedVariant(v: { id: string; label: string; price_min_minor: number; price_max_minor: number; mrp_minor: number | null; in_stock: boolean }): ProductVariantOut {
  return { ...v };
}

function seedProducts(): ProductOut[] {
  return PRODUCTS.map((product) => ({
    id: product.id,
    code: product.code,
    name: product.name,
    brand: product.brand,
    category_id: `cat-${product.category}`,
    category: product.category,
    spec: product.spec,
    unit: product.unit,
    description: null,
    price_min_minor: product.price_min_minor,
    price_max_minor: product.price_max_minor,
    mrp_minor: product.mrp_minor,
    currency: product.currency,
    gst_inclusive: product.gst_inclusive,
    moq: product.moq,
    moq_unit: product.moq_unit,
    in_stock: product.in_stock,
    lead_time_days: product.lead_time_days,
    delivery_eta_minutes: product.delivery_eta_minutes,
    featured: product.featured,
    image_url: product.image_url,
    status: "active",
    is_active: true,
    variants: product.variants.map(seedVariant),
    created_at: SEED_TIME,
    updated_at: SEED_TIME,
  }));
}

let fixtureCategories: CategoryOut[] = seedCategories();
let fixtureProducts: ProductOut[] = seedProducts();

function now(): string {
  return new Date().toISOString();
}

function paginate<T>(items: T[], page: number, size: number): PagedResult<T> {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / size));
  const start = (page - 1) * size;
  return { items: items.slice(start, start + size), total, page, pages, size };
}

/* -------------------------------- categories ------------------------------- */

export async function listCategories(params: ListCategoriesParams = {}): Promise<PagedResult<CategoryOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  if (USE_FIXTURES) {
    let rows = [...fixtureCategories];
    if (params.q) {
      const q = params.q.toLowerCase();
      rows = rows.filter((c) => c.key.includes(q) || c.label.toLowerCase().includes(q));
    }
    if (params.is_active !== undefined) rows = rows.filter((c) => c.is_active === params.is_active);
    rows.sort((a, b) => a.sort_order - b.sort_order);
    return settle(paginate(rows, page, size), 180);
  }
  const qs = new URLSearchParams();
  qs.set("page", String(page));
  qs.set("size", String(size));
  if (params.q) qs.set("q", params.q);
  if (params.is_active !== undefined) qs.set("is_active", String(params.is_active));
  return apiRequest<PagedResult<CategoryOut>>(`/storefront/categories?${qs.toString()}`);
}

export async function createCategory(input: CategoryInput): Promise<CategoryOut> {
  if (USE_FIXTURES) {
    if (fixtureCategories.some((c) => c.key === input.key)) {
      throw new ApiError(409, `A category with the key "${input.key}" already exists.`, null);
    }
    const created: CategoryOut = {
      id: `cat-${Date.now().toString(36)}`,
      ...input,
      line_count: 0,
      created_at: now(),
      updated_at: now(),
    };
    fixtureCategories = [...fixtureCategories, created];
    return settle(created, 260);
  }
  return apiRequest<CategoryOut>("/storefront/categories", { method: "POST", body: input });
}

export async function updateCategory(id: string, patch: CategoryPatch): Promise<CategoryOut> {
  if (USE_FIXTURES) {
    const existing = fixtureCategories.find((c) => c.id === id);
    if (!existing) throw new ApiError(404, "That category no longer exists.", null);
    const updated: CategoryOut = { ...existing, ...patch, updated_at: now() };
    fixtureCategories = fixtureCategories.map((c) => (c.id === id ? updated : c));
    return settle(updated, 260);
  }
  return apiRequest<CategoryOut>(`/storefront/categories/${id}`, { method: "PATCH", body: patch });
}

export async function deleteCategory(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const existing = fixtureCategories.find((c) => c.id === id);
    if (!existing) throw new ApiError(404, "That category no longer exists.", null);
    const count = fixtureProducts.filter((p) => p.category_id === id && p.status !== "archived").length;
    if (count > 0) {
      throw new ApiError(
        422,
        `${count} ${count === 1 ? "product" : "products"} still ${count === 1 ? "carries" : "carry"} this category. Move or archive ${count === 1 ? "it" : "them"} first.`,
        null,
      );
    }
    fixtureCategories = fixtureCategories.filter((c) => c.id !== id);
    return settle(undefined, 220);
  }
  return apiRequest<void>(`/storefront/categories/${id}`, { method: "DELETE" });
}

/* --------------------------------- products -------------------------------- */

function matchesProduct(product: ProductOut, params: ListProductsParams): boolean {
  if (params.q) {
    const q = params.q.toLowerCase();
    if (
      !product.code.toLowerCase().includes(q) &&
      !product.name.toLowerCase().includes(q) &&
      !product.brand.toLowerCase().includes(q)
    ) {
      return false;
    }
  }
  if (params.category_id && product.category_id !== params.category_id) return false;
  if (params.status && product.status !== params.status) return false;
  if (params.in_stock !== undefined && product.in_stock !== params.in_stock) return false;
  if (params.featured !== undefined && product.featured !== params.featured) return false;
  return true;
}

function sortProducts(rows: ProductOut[], sort: ListProductsParams["sort"]): ProductOut[] {
  const key = (sort ?? "-updated_at").replace(/^-/, "") as "name" | "code" | "price_min_minor" | "updated_at";
  const desc = (sort ?? "-updated_at").startsWith("-");
  const sorted = [...rows].sort((a, b) => {
    const av = a[key];
    const bv = b[key];
    if (av < bv) return desc ? 1 : -1;
    if (av > bv) return desc ? -1 : 1;
    return 0;
  });
  return sorted;
}

export async function listProducts(params: ListProductsParams = {}): Promise<PagedResult<ProductOut>> {
  const page = params.page ?? 1;
  const size = params.size ?? 25;
  if (USE_FIXTURES) {
    const rows = sortProducts(fixtureProducts.filter((p) => matchesProduct(p, params)), params.sort);
    return settle(paginate(rows, page, size), 220);
  }
  const qs = new URLSearchParams();
  qs.set("page", String(page));
  qs.set("size", String(size));
  if (params.q) qs.set("q", params.q);
  if (params.category_id) qs.set("category_id", params.category_id);
  if (params.status) qs.set("status", params.status);
  if (params.in_stock !== undefined) qs.set("in_stock", String(params.in_stock));
  if (params.featured !== undefined) qs.set("featured", String(params.featured));
  if (params.sort) qs.set("sort", params.sort);
  return apiRequest<PagedResult<ProductOut>>(`/storefront/products?${qs.toString()}`);
}

export async function getProduct(id: string): Promise<ProductOut> {
  if (USE_FIXTURES) {
    const found = fixtureProducts.find((p) => p.id === id);
    if (!found) throw new ApiError(404, "That product no longer exists.", null);
    return settle(found, 160);
  }
  return apiRequest<ProductOut>(`/storefront/products/${id}`);
}

/** The invariant `services.py` enforces server-side: when variants exist, the product's own price
 *  band and MRP mirror `variants[0]`'s. Mirrored here so fixture mode enforces the same rule the
 *  server would refuse a mismatch against. */
function withVariantInvariant(input: ProductInput | (ProductPatchInput & { variants: ProductInput["variants"] })): Partial<ProductInput> {
  const variants = input.variants;
  if (!variants || variants.length === 0) return input;
  const first = variants[0];
  if (!first) return input;
  return { ...input, price_min_minor: first.price_min_minor, price_max_minor: first.price_max_minor, mrp_minor: first.mrp_minor };
}

function validatePriceBand(price_min_minor: number, price_max_minor: number, mrp_minor: number | null): void {
  if (price_min_minor > price_max_minor) {
    throw new ApiError(422, "The maximum price must be at or above the minimum.", {
      detail: "The maximum price must be at or above the minimum.",
      errors: [{ field: "price_max_minor", code: "price_band_invalid", message: "The maximum price must be at or above the minimum." }],
    });
  }
  if (mrp_minor !== null && mrp_minor < price_min_minor) {
    throw new ApiError(422, "The MRP can't be below the selling price.", {
      detail: "The MRP can't be below the selling price.",
      errors: [{ field: "mrp_minor", code: "mrp_below_price", message: "The MRP can't be below the selling price." }],
    });
  }
}

export async function createProduct(input: ProductInput): Promise<ProductOut> {
  if (USE_FIXTURES) {
    if (fixtureProducts.some((p) => p.code === input.code)) {
      throw new ApiError(409, `A product with the code "${input.code}" already exists.`, null);
    }
    const category = fixtureCategories.find((c) => c.id === input.category_id);
    if (!category) {
      throw new ApiError(422, "Pick a category.", {
        detail: "Pick a category.",
        errors: [{ field: "category_id", code: "category_not_found", message: "Pick a category." }],
      });
    }
    const merged = withVariantInvariant(input);
    validatePriceBand(merged.price_min_minor ?? input.price_min_minor, merged.price_max_minor ?? input.price_max_minor, merged.mrp_minor ?? input.mrp_minor);
    const created: ProductOut = {
      id: `prod-${Date.now().toString(36)}`,
      ...input,
      ...merged,
      category: category.key,
      variants: input.variants.map((v, i) => ({ ...v, id: v.id ?? `var-${Date.now().toString(36)}-${i}` })),
      created_at: now(),
      updated_at: now(),
    } as ProductOut;
    fixtureProducts = [...fixtureProducts, created];
    fixtureCategories = fixtureCategories.map((c) =>
      c.id === category.id ? { ...c, line_count: c.line_count + 1 } : c,
    );
    return settle(created, 300);
  }
  return apiRequest<ProductOut>("/storefront/products", { method: "POST", body: input });
}

export async function updateProduct(id: string, patch: ProductPatchInput): Promise<ProductOut> {
  if (USE_FIXTURES) {
    const existing = fixtureProducts.find((p) => p.id === id);
    if (!existing) throw new ApiError(404, "That product no longer exists.", null);
    const merged = patch.variants ? withVariantInvariant({ ...existing, ...patch, variants: patch.variants }) : patch;
    const priceMin = merged.price_min_minor ?? existing.price_min_minor;
    const priceMax = merged.price_max_minor ?? existing.price_max_minor;
    const mrp = "mrp_minor" in merged ? (merged.mrp_minor ?? null) : existing.mrp_minor;
    validatePriceBand(priceMin, priceMax, mrp);
    let category = existing.category;
    let categoryId = existing.category_id;
    if (patch.category_id && patch.category_id !== existing.category_id) {
      const found = fixtureCategories.find((c) => c.id === patch.category_id);
      if (!found) {
        throw new ApiError(422, "Pick a category.", {
          detail: "Pick a category.",
          errors: [{ field: "category_id", code: "category_not_found", message: "Pick a category." }],
        });
      }
      category = found.key;
      categoryId = found.id;
    }
    const updated: ProductOut = {
      ...existing,
      ...merged,
      category_id: categoryId,
      category,
      variants: patch.variants
        ? patch.variants.map((v, i) => ({ ...v, id: v.id ?? `var-${Date.now().toString(36)}-${i}` }))
        : existing.variants,
      updated_at: now(),
    } as ProductOut;
    fixtureProducts = fixtureProducts.map((p) => (p.id === id ? updated : p));
    return settle(updated, 280);
  }
  return apiRequest<ProductOut>(`/storefront/products/${id}`, { method: "PATCH", body: patch });
}

export async function deleteProduct(id: string): Promise<void> {
  if (USE_FIXTURES) {
    const existing = fixtureProducts.find((p) => p.id === id);
    if (!existing) throw new ApiError(404, "That product no longer exists.", null);
    fixtureProducts = fixtureProducts.filter((p) => p.id !== id);
    fixtureCategories = fixtureCategories.map((c) =>
      c.id === existing.category_id ? { ...c, line_count: Math.max(0, c.line_count - 1) } : c,
    );
    return settle(undefined, 220);
  }
  return apiRequest<void>(`/storefront/products/${id}`, { method: "DELETE" });
}

/** Put the fixture catalogue back to its seed. Test-only; live mode holds no state to reset. */
export function resetCatalogueFixtures(): void {
  fixtureCategories = seedCategories();
  fixtureProducts = seedProducts();
}
