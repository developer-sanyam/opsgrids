import { createElement } from "react";
import type { ReactNode } from "react";

import { AutomationAnalyticsPage } from "./AutomationAnalytics";
import { AutomationsPage } from "./Automations";
import { BuilderPage } from "./Builder";
import { RunDetailPage } from "./RunDetail";

/**
 * The automation routes, for the orchestrator to mount under the app shell. `analytics` and `new`
 * are declared before `:automationId` so neither is ever read as an id.
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

export const routes: AppRoute[] = [
  {
    path: "/automations",
    element: createElement(AutomationsPage),
    title: "Automations",
    nav: { label: "Automations", group: "Communication" },
  },
  { path: "/automations/new", element: createElement(BuilderPage), title: "Create automation" },
  { path: "/automations/analytics", element: createElement(AutomationAnalyticsPage), title: "Automation analytics" },
  { path: "/automations/:automationId", element: createElement(BuilderPage), title: "Automation" },
  { path: "/automations/:automationId/runs/:runId", element: createElement(RunDetailPage), title: "Automation run" },
];
