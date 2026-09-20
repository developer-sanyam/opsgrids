/**
 * The announcement channel, kept apart from the component that mounts it so React Fast Refresh
 * stays happy — the same split as `theme.ts` / `ThemeProvider.tsx`.
 *
 * One polite region per screen, owned by the screen. Anything that changes without a click — a
 * decision that lands, a delegation revoked, another page of rows appended — is said once, here.
 */

import { createContext, useContext } from "react";

export type Announce = (message: string) => void;

export const AnnouncerContext = createContext<Announce | null>(null);

/**
 * The screen's polite announcer.
 *
 * Outside an `<Announcer>` this is a no-op rather than a throw, deliberately and unlike
 * `useTheme`: a shared component announcing its own side effect (a `Pagination` saying how many
 * rows it appended) must not crash an app that has not adopted the provider yet. A missing region
 * costs an announcement; a throw costs the screen.
 */
export function useAnnounce(): Announce {
  return useContext(AnnouncerContext) ?? noop;
}

function noop(): void {
  // No region mounted — nothing to say it into.
}
