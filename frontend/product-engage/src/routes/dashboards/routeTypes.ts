import type { ReactNode } from "react";

/**
 * One screen a slice contributes to the app. The orchestrator wires each slice's `routes` into the
 * router and the navigation; a slice never edits either.
 */
export interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string } | undefined;
}
