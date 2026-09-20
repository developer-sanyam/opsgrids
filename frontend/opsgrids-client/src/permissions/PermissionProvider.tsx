import { useMemo } from "react";
import type { ReactNode } from "react";

import { decodeAccessToken } from "../auth/token";
import { getAccessToken } from "../session";
import { PermissionContext } from "./context";

interface PermissionProviderProps {
  /**
   * The caller's resolved grants, `name:SCOPE`. Omit it and every permission reads as *unknown*,
   * which hides the controls that depend on one.
   *
   * There is no browser-visible source for these today and this provider deliberately does not
   * invent one: the access token carries no `perms` claim (the gateway resolves grants per
   * request), and deriving them from the token's `roles` would mean re-implementing the server's
   * role-to-grant resolution in the browser — wrong for any customer-authored role, and precisely
   * the "role flag that swaps UIs instead of scopes" ADR-0003 rejects. When the platform exposes
   * the caller's grants, this prop is the one place to feed them in.
   */
  grants?: readonly string[] | null;
  /** Overrides the `ent` claim. Fixture mode uses it; a real build should not need to. */
  entitlements?: readonly string[] | null;
  children: ReactNode;
}

/**
 * Supplies the permission and entitlement facts the UI uses to hide controls it is confident the
 * caller cannot use. Mount it inside `<AuthProvider>`, so the token it reads is the current one.
 *
 * It hides UI. The gateway refuses the call regardless (ADR-0003 §3).
 */
export function PermissionProvider({
  grants = null,
  entitlements,
  children,
}: PermissionProviderProps) {
  const token = getAccessToken();

  const value = useMemo(() => {
    if (entitlements !== undefined) return { grants, entitlements };
    const claims = decodeAccessToken(token);
    return { grants, entitlements: claims?.ent ?? null };
  }, [grants, entitlements, token]);

  return <PermissionContext value={value}>{children}</PermissionContext>;
}
