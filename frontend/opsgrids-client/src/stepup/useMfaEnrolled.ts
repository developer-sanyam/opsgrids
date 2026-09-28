/**
 * Whether the signed-in caller has a second factor set up — the other half, alongside the
 * permission, of whether a step-up-gated control (Reveal, unmask) can actually succeed. A
 * screen offering such a control on the permission alone sends someone with no authenticator
 * into `useStepUp`'s dialog only to be told there, after opening it, that there is nothing it
 * can do — `RevealableField`'s own rule ("an absent control, not a disabled one") argues for
 * knowing this before the button renders, not after it is clicked.
 *
 * **Prefers `AuthContext` when one is mounted** (the ordinary app: `AuthProvider` sits at the
 * root, so this is a free read of state already there, and it is what stays correct in fixture
 * mode — `AuthProvider`'s `fixtureUser` populates the context directly, with no `/auth/me` call
 * to hang against a backend fixture mode deliberately has none of). **Falls back to a direct
 * `GET /auth/me`** — the same call `AuthProvider` itself makes at boot — only when no
 * `AuthContext` is present, so a screen using this stays usable without the full session
 * lifecycle mounted around it (a lighter test render, say).
 *
 * `false` while either path is still settling, or if the fetch fails: never a reason to offer a
 * control that cannot possibly succeed.
 */
import { useContext, useMemo } from "react";

import { me } from "../auth/api";
import { AuthContext } from "../auth/context";
import type { MeOut } from "../auth/types";
import { useAsync } from "../useAsync";

export function useMfaEnrolled(): boolean {
  const ctx = useContext(AuthContext);
  const hasContext = ctx !== null;
  // Skipped (resolves instantly, no network call) whenever a context is mounted — `ctx.user` is
  // read directly below instead. `useAsync` still has to be called unconditionally; only its
  // loader is conditional.
  const own = useAsync<MeOut | null>(
    () => (hasContext ? Promise.resolve(null) : me()),
    [hasContext],
  );
  return useMemo(() => {
    if (ctx !== null) return ctx.user?.mfa_enabled ?? false;
    return own.status === "success" && own.data !== null && own.data.mfa_enabled;
  }, [ctx, own]);
}
