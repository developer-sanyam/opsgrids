/**
 * The store's identity — `GET/PUT /storefront/store`, plus the two publish transitions.
 *
 * **Fixtures by default**, as everywhere else in this app: `VITE_USE_FIXTURES=false` points every
 * call below at the gateway. Seeded from `delivery/fixtures.ts`'s `FIXTURE_STORE` so a save made
 * here is the same fixture the delivery admin already reads — one mutable identity in fixture
 * mode, not two that can drift.
 */

import { ApiError, apiRequest, settle } from "@opsgrids/client";

import { USE_FIXTURES } from "@/api/mode";
import { FIXTURE_STORE } from "@/delivery/fixtures";
import type { StoreOut, StorePut } from "./types";

let fixtureStore: StoreOut | null = { ...FIXTURE_STORE };

/** `GET /storefront/store` → `StoreOut`, 404 when the organization has no store yet. */
export async function getStoreIdentity(): Promise<StoreOut | null> {
  if (USE_FIXTURES) return settle(fixtureStore ? { ...fixtureStore } : null, 160);
  try {
    return await apiRequest<StoreOut>("/storefront/store");
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

/**
 * `PUT /storefront/store` — create or update. **The slug is required and, once the store exists,
 * must match the stored value** — the server refuses a change with 409 `slug_immutable`, and the
 * form never offers to edit it after creation.
 */
export async function putStoreIdentity(input: StorePut): Promise<StoreOut> {
  if (USE_FIXTURES) {
    const now = new Date().toISOString();
    if (fixtureStore && fixtureStore.slug !== input.slug) {
      throw new ApiError(409, "The store's address can't be changed once it exists.", {
        detail: "The store's address can't be changed once it exists.",
        errors: [{ field: "slug", code: "slug_immutable", message: "The store's address can't be changed once it exists." }],
      });
    }
    const next: StoreOut = {
      id: fixtureStore?.id ?? `store-${Date.now().toString(36)}`,
      slug: input.slug,
      name: input.name,
      tagline: input.tagline,
      logo_text: input.logo_text,
      status: fixtureStore?.status ?? "draft",
      currency: input.currency,
      timezone: input.timezone,
      // `custom_domain` is not a field on `StorePut` any more (ADR-0012 review finding 2b) — the
      // domain screen (`settings/domain.ts`) is the only writer, so this form carries the
      // existing claim through unchanged rather than reading it off the input.
      custom_domain: fixtureStore?.custom_domain ?? null,
      custom_domain_status: fixtureStore?.custom_domain_status ?? null,
      created_at: fixtureStore?.created_at ?? now,
      updated_at: now,
    };
    fixtureStore = next;
    return settle({ ...next }, 320);
  }
  return apiRequest<StoreOut>("/storefront/store", { method: "PUT", body: input });
}

async function transition(target: "published" | "draft"): Promise<StoreOut> {
  if (USE_FIXTURES) {
    if (fixtureStore === null) {
      throw new ApiError(404, "This organization has no storefront yet. Create the store first.", null);
    }
    if (fixtureStore.status === "suspended") {
      throw new ApiError(409, "A suspended store can't be published or unpublished from here.", null);
    }
    fixtureStore = { ...fixtureStore, status: target, updated_at: new Date().toISOString() };
    return settle({ ...fixtureStore }, 260);
  }
  return apiRequest<StoreOut>(`/storefront/store/${target === "published" ? "publish" : "unpublish"}`, {
    method: "POST",
  });
}

/** `POST /storefront/store/publish`. Idempotent; 409 `store_suspended` on a platform-suspended store. */
export async function publishStore(): Promise<StoreOut> {
  return transition("published");
}

/** `POST /storefront/store/unpublish`. Idempotent. */
export async function unpublishStore(): Promise<StoreOut> {
  return transition("draft");
}

/** Put the fixture identity back to its seed. Test-only; live mode holds no state to reset. */
export function resetStoreIdentityFixture(): void {
  fixtureStore = { ...FIXTURE_STORE };
}
