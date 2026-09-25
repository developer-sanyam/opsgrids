/**
 * The real app screens the harness photographs, each rendered from its production build against
 * the contract-shaped mocks in `fixtures/api/`. A handful, chosen to cover what the redesign
 * touches first: the ecosystem home, a product dashboard, a data table, an open form dialog, and
 * the operator console the Super Admin mock-up targets.
 */

import type { Locator, Page } from "@playwright/test";

import { CRM_ROUTES, DASHBOARD_ROUTES, INTERNAL_ROUTES } from "../fixtures/api/routes";
import type { MockRoute } from "./api";
import { ORIGIN } from "./env";
import type { SessionSlot } from "./session";
import { unsignedToken } from "./session";

export type App = "dashboard" | "crm" | "internal";

export interface PageSpec {
  /** `page/<app>/<name>` — the baseline directory and the a11y key's prefix. */
  surface: string;
  app: App;
  origin: string;
  path: string;
  session: SessionSlot;
  token: string;
  routes: MockRoute[];
  /** Visible once the screen has rendered its data — not a loader, not an error. */
  ready: (page: Page) => Locator;
  /** A user's step before the screenshot: switch a view, open a dialog. */
  act?: ((page: Page) => Promise<void>) | undefined;
  /**
   * `viewport` for a screen whose subject is an overlay (the dialog is what is under test, and it
   * is only ever seen in the viewport); `fullpage` for everything else, below the fold included.
   */
  capture: "fullpage" | "viewport";
}

const CUSTOMER_CLAIMS = {
  sub: "5b1f4c2a-7d3e-4a8b-9c6d-1e2f3a4b5c6d",
  act_org: "8a7b6c5d-4e3f-4a1b-8c9d-0e1f2a3b4c5d",
  ent: ["crm", "hrms"],
  pv: 7,
};

const CRM = {
  app: "crm",
  origin: ORIGIN.crm,
  session: { tokenKey: "opsgrids.crm.access_token", themeKey: "opsgrids.crm.theme" },
  token: unsignedToken({ ...CUSTOMER_CLAIMS, aud: "crm", roles: ["revenue_admin"] }),
  routes: CRM_ROUTES,
} as const;

/**
 * The Owner Home fan-out reads finance and projects too (dashboard audit §5.4), on top of the
 * marketplace's own crm/hrms — a separate claim set from `CUSTOMER_CLAIMS` so widening it can
 * never move the CRM app's own baselines.
 */
const OWNER_CLAIMS = {
  ...CUSTOMER_CLAIMS,
  ent: ["crm", "hrms", "finance", "projects"],
};

const DASHBOARD = {
  app: "dashboard",
  origin: ORIGIN.dashboard,
  session: { tokenKey: "opsgrids.dashboard.access_token", themeKey: "opsgrids.dashboard.theme" },
  token: unsignedToken({ ...OWNER_CLAIMS, aud: "dashboard", roles: ["owner"] }),
  routes: DASHBOARD_ROUTES,
} as const;

const INTERNAL = {
  app: "internal",
  origin: ORIGIN.internal,
  session: { tokenKey: "opsgrids.operator.access_token", themeKey: "opsgrids.operator.theme" },
  token: unsignedToken({ sub: "7e6d5c4b-3a29-4180-9f8e-7d6c5b4a3928", aud: "operator", operator_roles: ["owner"] }),
  routes: INTERNAL_ROUTES,
} as const;

function heading(name: string): (page: Page) => Locator {
  return (page) => page.getByRole("heading", { level: 1, name });
}

export const PAGES: PageSpec[] = [
  {
    ...DASHBOARD,
    routes: [...DASHBOARD.routes],
    surface: "page/dashboard/home",
    path: "/",
    ready: (page) => page.getByRole("region", { name: "Key metrics" }),
    capture: "fullpage",
  },
  {
    ...DASHBOARD,
    routes: [...DASHBOARD.routes],
    surface: "page/dashboard/marketplace",
    // The Owner Home moved to `/`; the marketplace it displaced is `/products` now (dashboard
    // audit §5.4). Its own fixtures are unchanged, so its baseline still matches pixel for pixel.
    path: "/products",
    ready: heading("Start what your organisation needs"),
    capture: "fullpage",
  },
  {
    ...CRM,
    routes: [...CRM.routes],
    surface: "page/crm/overview",
    path: "/",
    ready: (page) => page.getByRole("region", { name: "Key metrics" }),
    capture: "fullpage",
  },
  {
    ...CRM,
    routes: [...CRM.routes],
    surface: "page/crm/deals-list",
    path: "/deals",
    ready: (page) => page.getByRole("table", { name: "All deals in the pipeline" }),
    act: async (page) => {
      // By position, not by name: below `sm` the tab's label is hidden and it has no name at all
      // (which the a11y baseline records).
      await page.getByRole("tablist", { name: "View" }).getByRole("tab").nth(1).click();
    },
    capture: "fullpage",
  },
  {
    ...CRM,
    routes: [...CRM.routes],
    surface: "page/crm/new-deal-dialog",
    path: "/deals",
    ready: (page) => page.getByRole("dialog"),
    act: async (page) => {
      await page.getByRole("button", { name: "New deal" }).click();
    },
    capture: "viewport",
  },
  {
    ...INTERNAL,
    routes: [...INTERNAL.routes],
    surface: "page/internal/overview",
    path: "/",
    ready: heading("Overview"),
    capture: "fullpage",
  },
  {
    ...INTERNAL,
    routes: [...INTERNAL.routes],
    surface: "page/internal/accounts",
    path: "/accounts",
    ready: heading("Accounts"),
    capture: "fullpage",
  },
];
