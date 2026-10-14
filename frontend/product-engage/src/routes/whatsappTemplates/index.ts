import { createElement } from "react";
import type { ReactNode } from "react";

import { RequireGrant } from "@/auth/RequireGrant";

import { TemplateDetailPage } from "./TemplateDetailPage";
import { TemplatesPage } from "./TemplatesPage";
import { TemplateWizardPage } from "./TemplateWizardPage";

/** One screen the orchestrator mounts under the product's shell. */
export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string };
}

/**
 * The WhatsApp template screens. `new` is declared before `:id` so it is never read as an id.
 *
 * Creating is behind `engage.whatsapp.template.write`, which renders the denial rather than the
 * form — presentation only: the create endpoint refuses the call regardless. The list and the
 * detail are open, and show the service's own refusal if the read permission is missing.
 */
export const routes: AppRoute[] = [
  {
    path: "/whatsapp/templates",
    element: createElement(TemplatesPage),
    title: "WhatsApp templates",
    nav: { label: "Templates", group: "WhatsApp" },
  },
  {
    path: "/whatsapp/templates/new",
    element: createElement(RequireGrant, {
      permission: "engage.whatsapp.template.write",
      what: "the template editor",
      children: createElement(TemplateWizardPage),
    }),
    title: "Create WhatsApp template",
  },
  {
    path: "/whatsapp/templates/:id",
    element: createElement(TemplateDetailPage),
    title: "WhatsApp template",
  },
];
