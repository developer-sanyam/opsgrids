import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider } from "@opsgrids/client";

/**
 * The session. The logic lives in `@opsgrids/client`; this app has no fixture identity because
 * it has no design surface to work on offline — everything it shows is the organisation's own
 * commercial state, which only the gateway can answer.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  return <BaseAuthProvider fixtureUser={null}>{children}</BaseAuthProvider>;
}
