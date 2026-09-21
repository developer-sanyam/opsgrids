import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider, PermissionProvider } from "@opsgrids/client";

/**
 * The session. The logic lives in `@opsgrids/client`; this app has no fixture identity because
 * it has no design surface to work on offline — everything it shows is the organisation's own
 * commercial state, which only the gateway can answer.
 *
 * `PermissionProvider` is mounted here, inside `BaseAuthProvider`: it reads the session to know
 * whose grants to ask for (`GET /members/me/grants`), and every `<Can>` / `useCan` in the app —
 * including the sidebar's Administration group — answers from it.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider fixtureUser={null}>
      <PermissionProvider>{children}</PermissionProvider>
    </BaseAuthProvider>
  );
}
