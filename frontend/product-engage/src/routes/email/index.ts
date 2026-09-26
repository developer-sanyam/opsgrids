import { createElement } from "react";

import { RequireGrant } from "@/auth/RequireGrant";

import { EmailCampaignsPage } from "./EmailCampaigns";
import { EmailSettingsPage } from "./EmailSettings";
import { EmailTemplateEditorPage } from "./EmailTemplateEditor";
import { EmailTemplatesPage } from "./EmailTemplates";
import type { AppRoute } from "./routeTypes";

export type { AppRoute } from "./routeTypes";

/**
 * The email area's screens, for the orchestrator to wire into the router and the navigation.
 *
 * Each screen sits behind the read permission of the API it is built on, so a member without it sees
 * the denial rather than an empty list: templates are served by the notification service under
 * `notification.delivery.template.read`, campaigns under the Engage campaign permission, and the
 * settings screen reads the connection. The gate hides the screen; the service refuses the read
 * regardless.
 *
 * `/email/templates/new` is listed before `/email/templates/:id` for readability; the router ranks a
 * fixed segment above a parameter either way.
 */
const templates = (page: AppRoute["element"]) =>
  createElement(RequireGrant, { permission: "notification.delivery.template.read", what: "email templates", children: page });

export const routes: AppRoute[] = [
  {
    path: "/email/templates",
    element: templates(createElement(EmailTemplatesPage)),
    title: "Email templates",
    nav: { label: "Templates", group: "Email" },
  },
  {
    path: "/email/templates/new",
    element: templates(createElement(EmailTemplateEditorPage)),
    title: "New email template",
  },
  {
    path: "/email/templates/:id",
    element: templates(createElement(EmailTemplateEditorPage)),
    title: "Edit email template",
  },
  {
    path: "/email/campaigns",
    element: createElement(RequireGrant, {
      permission: "engage.campaign.campaign.read",
      what: "campaigns",
      children: createElement(EmailCampaignsPage),
    }),
    title: "Email campaigns",
    nav: { label: "Campaigns", group: "Email" },
  },
  {
    path: "/email/settings",
    element: createElement(RequireGrant, {
      permission: "engage.setup.connection.read",
      what: "email accounts",
      children: createElement(EmailSettingsPage),
    }),
    title: "Email accounts",
    nav: { label: "Settings", group: "Email" },
  },
];
