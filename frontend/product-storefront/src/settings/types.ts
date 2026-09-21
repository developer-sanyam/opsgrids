/**
 * The store's identity — the wire contract for `GET/PUT /storefront/store` and the two publish
 * transitions, off `services/storefront/app/schemas.py` `StoreOut` / `StorePut`.
 *
 * This is the **canonical** `StoreOut` for the whole app: `src/delivery/types.ts` re-exports it
 * rather than declaring its own narrower copy, because the delivery admin, the settings screen and
 * the theme document all need the same identity row and must not drift into two shapes for it.
 */

export interface StoreOut {
  id: string;
  /** Immutable once set. Changing it on a `PUT` is a 409 `slug_immutable`. */
  slug: string;
  name: string;
  tagline: string | null;
  /** The header wordmark. `null` falls back to `name` — the renderer's decision, not stored here. */
  logo_text: string | null;
  status: "draft" | "published" | "suspended";
  /** ISO-4217. */
  currency: string;
  /** IANA. */
  timezone: string;
  custom_domain: string | null;
  /** `unverified` whenever `custom_domain` is set, `null` when it is not. Nothing can move it off
   *  `unverified` today — see `docs/adr/0011-store-identity-brand-domain-and-publishing.md` §7. */
  custom_domain_status: "unverified" | "verified" | null;
  created_at: string;
  updated_at: string;
}

/**
 * `StorePut` — a full-replacement form. `status` is deliberately absent: publishing is its own
 * named transition (`publishStore` / `unpublishStore`), never a side effect of a settings save.
 */
export interface StorePut {
  slug: string;
  name: string;
  tagline: string | null;
  logo_text: string | null;
  currency: string;
  timezone: string;
  custom_domain: string | null;
}
