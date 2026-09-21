/**
 * The auth context and its hook. Kept in a `.ts` file with no component export so React Fast
 * Refresh stays happy — the provider component lives next door in `AuthProvider.tsx`.
 */

import { createContext, useContext } from "react";

import type { LoginStep, MeOut } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  /** `loading` while the stored token is being verified on boot. */
  status: AuthStatus;
  /** The signed-in user, or `null` before sign-in completes. */
  user: MeOut | null;
  /**
   * Sign in with credentials. Resolves to a step: `complete` (routed straight in),
   * `org_selection` (render the picker), or `mfa_required` (ask for the code, then call
   * `verifyMfa`). A single org auto-selects. The pre-auth token behind the last two is held in
   * the provider — the screen never sees it and must never store it.
   */
  login: (email: string, password: string) => Promise<LoginStep>;
  /**
   * Finish an MFA sign-in with a code from the user's authenticator app, or a recovery code.
   * Resolves to the same steps `login` does — `complete`, or `org_selection` for an account that
   * reaches several organizations. A wrong code rejects with the server's `ApiError`, leaves the
   * pending sign-in intact and can simply be called again.
   */
  verifyMfa: (code: string) => Promise<LoginStep>;
  /** Finish an org-selection sign-in by choosing one of the offered organizations. */
  selectOrg: (organizationId: string) => Promise<void>;
  /** Clear the session and return to a signed-out state. */
  logout: () => Promise<void>;
}

export const AuthContext = createContext<AuthContextValue | null>(null);

/** Read the auth context. Throws if used outside the provider — a wiring bug, not a runtime path. */
export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used within an <AuthProvider>");
  }
  return value;
}
