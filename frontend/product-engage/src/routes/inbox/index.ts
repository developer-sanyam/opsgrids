import type { ReactNode } from "react";

/** The sprint's route-list shape (`{ path, element, title, nav? }`); no shared type exists at this
 *  base commit, so it is declared where it is used and is structurally identical to the contract. */
interface AppRoute {
  path: string;
  element: ReactNode;
  title: string;
  nav?: { label: string; group: string };
}

/** The Customer 360 panel is a component inside the conversation view, not a route of its own. */
export const routes: AppRoute[] = [];
