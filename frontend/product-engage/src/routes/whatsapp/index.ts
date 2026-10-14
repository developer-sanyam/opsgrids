import { createElement, lazy, Suspense } from "react";
import type { ComponentType, ReactNode } from "react";

import { Skeleton } from "@opsgrids/ui";

/** One screen the orchestrator wires into the router and the navigation. */
export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string };
}

/**
 * Each screen is its own chunk: the wizard and the settings page are visited a handful of times
 * per organization, and neither belongs in the bundle every other Engage screen downloads. The
 * fallback is a block of the page's own size, so nothing shifts when the chunk arrives.
 */
const ConnectPage = lazy(() =>
  import("./WhatsAppConnect").then((module) => ({ default: module.WhatsAppConnectPage })),
);
const SettingsPage = lazy(() =>
  import("./WhatsAppSettings").then((module) => ({ default: module.WhatsAppSettingsPage })),
);

function page(Screen: ComponentType): ReactNode {
  return createElement(
    Suspense,
    { fallback: createElement(Skeleton, { className: "h-96 w-full rounded-card" }) },
    createElement(Screen),
  );
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
    element: page(ConnectPage),
    title: "Connect WhatsApp",
  },
  {
    path: "whatsapp/settings",
    element: page(SettingsPage),
    title: "WhatsApp settings",
    nav: { label: "Settings", group: "WhatsApp" },
  },
];
