import { createElement, lazy, Suspense } from "react";
import type { ComponentType, LazyExoticComponent, ReactNode } from "react";

import { RouteFallback } from "./RouteFallback";

/**
 * The automation routes, for the orchestrator to mount under the app shell. `analytics` and `new`
 * are declared before `:automationId` so neither is ever read as an id.
 *
 * Each screen is loaded on demand: the builder and the analytics charts are the heaviest code in
 * this app, and someone who never opens Automations should not download them. The fallback holds
 * the space the screen will fill.
 *
 * Nothing here gates on a permission: the service refuses every call the caller may not make, and
 * each screen shows the refusal it gets.
 */

export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string };
}

const AutomationsPage = lazy(() => import("./Automations").then((m) => ({ default: m.AutomationsPage })));
const BuilderPage = lazy(() => import("./Builder").then((m) => ({ default: m.BuilderPage })));
const RunDetailPage = lazy(() => import("./RunDetail").then((m) => ({ default: m.RunDetailPage })));
const AutomationAnalyticsPage = lazy(() =>
  import("./AutomationAnalytics").then((m) => ({ default: m.AutomationAnalyticsPage })),
);

function screen(Page: LazyExoticComponent<ComponentType>): ReactNode {
  return createElement(Suspense, { fallback: createElement(RouteFallback) }, createElement(Page));
}

export const routes: AppRoute[] = [
  {
    path: "/automations",
    element: screen(AutomationsPage),
    title: "Automations",
    nav: { label: "Automations", group: "Communication" },
  },
  { path: "/automations/new", element: screen(BuilderPage), title: "Create automation" },
  { path: "/automations/analytics", element: screen(AutomationAnalyticsPage), title: "Automation analytics" },
  { path: "/automations/:automationId", element: screen(BuilderPage), title: "Automation" },
  { path: "/automations/:automationId/runs/:runId", element: screen(RunDetailPage), title: "Automation run" },
];
