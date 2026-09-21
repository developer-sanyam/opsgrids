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
