import { useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { AuthContext } from "../auth/context";
import { decodeAccessToken } from "../auth/token";
import { getAccessToken } from "../session";
import { fetchMyGrants } from "./api";
import { PermissionContext } from "./context";
import type { GrantsStatus } from "./context";

interface PermissionProviderProps {
  /**
   * Grants to use **instead of** asking the gateway — fixture mode, and tests that want a caller
   * with a known set.
   *
   * `null` (and omitting it) is the real-build value and means "go and find out": the provider
   * fetches `GET /members/me/grants` once the session is authenticated. Only a non-null array
   * suppresses the request.
   */
  grants?: readonly string[] | null | undefined;
  /** Overrides the `ent` claim. Fixture mode uses it; a real build should not need to. */
  entitlements?: readonly string[] | null | undefined;
  children: ReactNode;
}

interface Fetched {
  status: GrantsStatus;
  grants: readonly string[] | null;
  membershipId: string | null;
  pv: number | null;
  error: Error | null;
}

const WAITING: Fetched = {
  status: "loading",
  grants: null,
  membershipId: null,
  pv: null,
  error: null,
};

/**
 * Supplies the caller's grants and entitlements, so the interface can leave out controls it knows
 * they cannot use.
 *
 * Mount it **inside `<AuthProvider>`**: it reads the session to know whose grants to ask for, and
 * asks only once the session is authenticated. It asks again when the answer could have changed —
 * a different organization, a different membership, or a new access token carrying a new `pv`,
 * which is what the gateway mints after a role change. A `401` anywhere drops the session, and
 * the next sign-in refetches; `reloadGrants()` forces it by hand.
 *
 * Three states, and what each means for a control:
 *
 * - `loading` — nothing is known yet. A write control renders **nothing**. Not a disabled
 *   button, not a visible-then-vanishing one: a Create button that appears for 200ms and then
 *   disappears is worse than one that arrives 200ms late.
 * - `ready` — the answer is exact.
 * - `error` — the grants could not be read. Controls stay hidden (fail closed), and the page
 *   renders regardless. **Surface this.** `usePermissions().status` is exposed precisely so an
 *   app shell can say "we couldn't check your permissions, some actions are hidden" rather than
 *   leave a user staring at a screen with no buttons and no explanation.
 *
 * It hides UI. It is not authorization: the gateway refuses the call regardless (ADR-0003 §3),
 * every list endpoint injects scope into its query, and every endpoint carries a negative-authz
 * test. Nothing here may ever be the only check.
 */
export function PermissionProvider({
  grants: override = null,
  entitlements,
  children,
}: PermissionProviderProps) {
  // Read directly rather than through `useAuth`, which throws outside a provider: an app that
  // has no sign-in (the storefront's buyer surfaces) still mounts this for `useEntitlement`.
  const auth = useContext(AuthContext);
  const token = getAccessToken();
  const claims = useMemo(() => decodeAccessToken(token), [token]);

  const [fetched, setFetched] = useState<Fetched>(WAITING);
  const [nonce, setNonce] = useState(0);
  const reloadGrants = useCallback(() => {
    setNonce((previous) => previous + 1);
  }, []);

  const overridden = override !== null;
  const authenticated = auth?.status === "authenticated";
  // What the answer depends on. When any of it changes the grants may have too, and the widest
  // scope the caller held a moment ago is not something to keep rendering from.
  const identity = [
    auth?.user?.organization_id ?? claims?.act_org ?? "",
    auth?.user?.membership_id ?? "",
    claims?.pv ?? "",
  ].join("|");

  useEffect(() => {
    if (overridden) return;
    if (!authenticated) {
      setFetched(WAITING);
      return;
    }
    let active = true;
    setFetched(WAITING);
    fetchMyGrants()
      .then((result) => {
        if (!active) return;
        setFetched({
          status: "ready",
          grants: result.grants,
          membershipId: result.membership_id,
          pv: result.pv ?? null,
          error: null,
        });
      })
      .catch((thrown: unknown) => {
        if (!active) return;
        setFetched({
          status: "error",
          grants: null,
          membershipId: null,
          pv: null,
          error: thrown instanceof Error ? thrown : new Error(String(thrown)),
        });
      });
    return () => {
      active = false;
    };
  }, [overridden, authenticated, identity, nonce]);

  const value = useMemo(() => {
    const resolvedEntitlements =
      entitlements !== undefined ? entitlements : (decodeAccessToken(token)?.ent ?? null);
    if (overridden) {
      return {
        grants: override,
        entitlements: resolvedEntitlements,
        status: "ready" as const,
        membershipId: null,
        pv: null,
        error: null,
        reloadGrants,
      };
    }
    return {
      grants: fetched.grants,
      entitlements: resolvedEntitlements,
      status: fetched.status,
      membershipId: fetched.membershipId,
      pv: fetched.pv,
      error: fetched.error,
      reloadGrants,
    };
  }, [override, overridden, entitlements, token, fetched, reloadGrants]);

  return <PermissionContext value={value}>{children}</PermissionContext>;
}
