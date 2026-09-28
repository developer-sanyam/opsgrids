import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider, PermissionProvider } from "@opsgrids/client";

/**
 * The session, wired to this app's own auth. Unlike every other `product-*` app, this one carries
 * no fixture identity and no fixture grants: there is no local screen yet whose data a fixture
 * would stand in for (`docs/fulfilment/plan.md` F3 — pick tasks, offers and scans are a later
 * phase's backend), so a fixture path here would have nothing honest to render. Login and
 * `/auth/me` are real from day one; `PermissionProvider` always fetches the caller's live grants
 * from `GET /members/me/grants`.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider>
      <PermissionProvider>{children}</PermissionProvider>
    </BaseAuthProvider>
  );
}
