import type { ReactNode } from "react";

import { AuthProvider as BaseAuthProvider, PermissionProvider } from "@opsgrids/client";

/**
 * The session, wired to this app's own auth. Unlike every other `product-*` app, this one carries
 * no fixture identity and no fixture grants, and has none of `product-operations`' own
 * `VITE_USE_FIXTURES` design-time path either (`api/client.ts`'s own doc comment): a picker's
 * shift is real data or nothing. Login, `/auth/me` and every fulfilment call are real from day
 * one; `PermissionProvider` always fetches the caller's live grants from `GET /members/me/grants`.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <BaseAuthProvider>
      <PermissionProvider>{children}</PermissionProvider>
    </BaseAuthProvider>
  );
}
