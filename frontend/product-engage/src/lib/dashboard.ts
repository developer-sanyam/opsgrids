/**
 * Where an organization turns a product on, and where the CRM's contact book lives — both a
 * different app on a different host, so both are absolute URLs, never a router path.
 *
 * Read from `@opsgrids/client`'s configurable platform catalogue (`getPlatformHost`,
 * `getAuthOrigin`) rather than a literal, so dev and staging builds — which call
 * `configurePlatformHosts`/`configureAuthOrigin` before the app renders — link to their own hosts
 * instead of production. Functions, not constants: `configureAuthOrigin` runs in `main.tsx`
 * *after* this module's imports resolve, so reading it at call time is the only way to see the
 * configured value rather than the default this module loaded with.
 */
import { getAuthOrigin, getPlatformHost } from "@opsgrids/client";

const DASHBOARD_ORIGIN_FALLBACK = "https://app.opsgrids.com";

/**
 * The Products page of the OpsGrids dashboard — the marketplace, whose "Start 14-day trial" posts
 * the subscription that activation follows (ADR-0013 §1) — is that app's index route
 * (`frontend/opsgrids-dashboard/src/main.tsx`, labelled "Products" in its sidebar). If either
 * moves, this moves with it.
 */
export function dashboardProductsUrl(): string {
  return `${getAuthOrigin() ?? DASHBOARD_ORIGIN_FALLBACK}/`;
}

/**
 * The CRM's contact book. Points at the **list**, not a per-contact record: `product-crm`'s router
 * (`frontend/product-crm/src/main.tsx`) has no `contacts/:id` route today — only `companies/:id`
 * and `deals/:id` do — so a conversation's `contact_id` has nowhere to deep-link to yet. Linking
 * to a route that would 404 is worse than linking to the list a person can search from, and this
 * moves to the specific record the day that route exists.
 */
export function crmContactsUrl(): string {
  return `${getPlatformHost("crm")}/contacts`;
}
