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
   * `org_selection` (render the picker), or `mfa_required`. A single org auto-selects.
   */
  login: (email: string, password: string) => Promise<LoginStep>;
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
