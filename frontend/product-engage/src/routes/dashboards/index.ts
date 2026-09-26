import { createElement } from "react";

import { CampaignAnalyticsPage } from "./CampaignAnalytics";
import { CommunicationHubPage } from "./CommunicationHub";
import { EmailOverviewPage } from "./EmailOverview";
import type { AppRoute } from "./routeTypes";
import { WhatsAppAnalyticsPage } from "./WhatsAppAnalytics";
import { WhatsAppOverviewPage } from "./WhatsAppOverview";

export type { AppRoute } from "./routeTypes";

/**
 * The Communication dashboards, for the orchestrator to wire into the router. `/` replaces the
 * get-started checklist as the landing screen; the checklist stays reachable wherever the
 * orchestrator chooses to keep it. None of these screens is code-split here — the charts are
 * `@opsgrids/ui` components the app already ships — so the orchestrator may lazy-load the route
 * list as one chunk if it wants the dashboards off the initial bundle.
 *
 * `nav` is set only on the entries that belong in the sidebar; the per-campaign screen is reached
 * from a campaign, not from navigation.
 */
export const routes: AppRoute[] = [
  {
    path: "/",
    element: createElement(CommunicationHubPage),
    title: "Communication",
    nav: { label: "Overview", group: "Communication" },
  },
  {
    path: "/whatsapp",
    element: createElement(WhatsAppOverviewPage),
    title: "WhatsApp",
    nav: { label: "WhatsApp", group: "Communication" },
  },
  {
    path: "/whatsapp/analytics",
    element: createElement(WhatsAppAnalyticsPage),
    title: "WhatsApp analytics",
    nav: { label: "Analytics", group: "WhatsApp" },
  },
  {
    path: "/email",
    element: createElement(EmailOverviewPage),
    title: "Email",
    nav: { label: "Email", group: "Communication" },
  },
  {
    path: "/email/campaigns/:id/analytics",
    element: createElement(CampaignAnalyticsPage),
    title: "Campaign analytics",
  },
];
