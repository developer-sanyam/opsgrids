import { createElement } from "react";
import type { ReactNode } from "react";

import { WhatsAppConnectPage } from "./WhatsAppConnect";
import { WhatsAppSettingsPage } from "./WhatsAppSettings";

/** One screen the orchestrator wires into the router and the navigation. */
export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string };
}

/**
 * The WhatsApp connection screens (slice F1).
 *
 * `/whatsapp/connect` is the whole wizard — the step lives in `?step=` — and is deliberately not in
 * the navigation: it is reached from Settings and from Overview's setup prompts, not a place
 * someone works from. Each page gates itself on its own read/write permission (presentation; the
 * service refuses every call behind it regardless).
 */
export const routes: AppRoute[] = [
  {
    path: "whatsapp/connect",
    element: createElement(WhatsAppConnectPage),
    title: "Connect WhatsApp",
  },
  {
    path: "whatsapp/settings",
    element: createElement(WhatsAppSettingsPage),
    title: "WhatsApp Settings",
    nav: { label: "Settings", group: "WhatsApp" },
  },
];
