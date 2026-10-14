import { useMemo } from "react";
import type { ReactNode } from "react";

import { PermissionProvider } from "../permissions/PermissionProvider";
import { AuthProvider } from "./AuthProvider";
import { ProductChromeContext } from "./productChrome";
import type { MeOut } from "./types";

/**
 * A stand-in identity for fixture mode, so a whole app renders with no gateway and no login — for
 * offline design work and screenshots. Never reached live: each app compiles its fixture switch
 * out of a production build (`import.meta.env.DEV`).
 */
export interface ProductFixture {
  user: MeOut;
  /** The grants the fixture identity holds, e.g. `"crm.pipeline.deal.write:ORG"`. */
  grants: readonly string[];
}

interface ProductAuthProviderProps {
  /** The fixture identity, or `null` for the real session — the app decides, usually `USE_FIXTURES`. */
  fixture?: ProductFixture | null | undefined;
  /** The product's mark, pulsing while the session boots. `RequireAuth` reads it from here. */
  mark?: ReactNode;
  /** What the boot screen says — "Signing you in…" unless the product has its own words. */
  bootMessage?: string | undefined;
  children: ReactNode;
}

/**
 * The session and the caller's grants, wired for one product app. The logic lives in
 * `AuthProvider` and `PermissionProvider`; this is the arrangement every app repeated, with the
 * fixture identity as the only per-app input. With no fixture, `PermissionProvider` fetches
 * `GET /members/me/grants` itself.
 */
export function ProductAuthProvider({ fixture = null, mark = null, bootMessage, children }: ProductAuthProviderProps) {
  const chrome = useMemo(() => ({ mark, bootMessage }), [mark, bootMessage]);
  return (
    <ProductChromeContext value={chrome}>
      <AuthProvider fixtureUser={fixture === null ? null : fixture.user}>
        <PermissionProvider grants={fixture === null ? null : fixture.grants}>{children}</PermissionProvider>
      </AuthProvider>
    </ProductChromeContext>
  );
}
