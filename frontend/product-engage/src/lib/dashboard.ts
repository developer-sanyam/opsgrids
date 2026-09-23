/**
 * Where an organization turns a product on: the Products page of the OpsGrids dashboard.
 *
 * A different app on a different host, so this is an absolute URL, never a router path.
 * `app.opsgrids.com` is the dashboard's site in `infrastructure/edge/caddy/Caddyfile`, and
 * Products — the marketplace, whose "Start 14-day trial" posts the subscription that activation
 * follows (ADR-0013 §1) — is that app's index route (`frontend/opsgrids-dashboard/src/main.tsx`,
 * labelled "Products" in its sidebar). If either moves, this moves with it.
 */
export const DASHBOARD_PRODUCTS_URL = "https://app.opsgrids.com/";

/**
 * Where the CRM's contact book lives — `revenue.opsgrids.com`, `product-crm`'s edge host
 * (`infrastructure/edge/caddy/Caddyfile`, `make edge-web APP=crm`). A different app on a
 * different host, so this is an absolute URL, never a router path.
 *
 * Points at the **list**, not a per-contact record: `product-crm`'s router
 * (`frontend/product-crm/src/main.tsx`) has no `contacts/:id` route today — only `companies/:id`
 * and `deals/:id` do — so a conversation's `contact_id` has nowhere to deep-link to yet. Linking
 * to a route that would 404 is worse than linking to the list a person can search from, and this
 * moves to the specific record the day that route exists.
 */
export const CRM_CONTACTS_URL = "https://revenue.opsgrids.com/contacts";
