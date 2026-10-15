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

/** One CRM contact's page (`product-crm`'s `contacts/:id`), where the contact's Messaging card lives. */
export function crmContactUrl(contactId: string): string {
  return `${getPlatformHost("crm")}/contacts/${encodeURIComponent(contactId)}`;
}

/** The CRM's contact book, for a section with no single contact to point at. */
export function crmContactsUrl(): string {
  return `${getPlatformHost("crm")}/contacts`;
}
