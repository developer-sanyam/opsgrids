import type { ReactNode } from "react";
import type { RouteObject } from "react-router-dom";

import { routes as dashboardRoutes } from "./dashboards";

/**
 * One screen a feature area contributes to the app. Each area (dashboards, WhatsApp, templates,
 * automations, ...) exports a `routes` list in this shape from its own folder and never edits the
 * router; this file is the one place they are joined, so a new area is one import and one spread.
 */
export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string } | undefined;
}

/** Every area's contribution, in the order their entries appear in the navigation. */
export const areaRoutes: AppRoute[] = [...dashboardRoutes];

/**
 * The router's child routes for the areas above. `/` becomes the index route; every other path
 * loses its leading slash because they are children of the shell's `/` route.
 */
export function toRouteObjects(routes: AppRoute[]): RouteObject[] {
  return routes.map((route) =>
    route.path === "/"
      ? { index: true, element: route.element }
      : { path: route.path.replace(/^\//, ""), element: route.element },
  );
}
