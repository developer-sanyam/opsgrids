/**
 * The operator auth context and its hook. Kept in a `.ts` file with no component export so React
 * Fast Refresh stays happy — the provider component lives next door in `AuthProvider.tsx`.
 */

import { createContext, useContext } from "react";

import type { LoginStep, OperatorMe, OperatorRole } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  /** `loading` while the stored token is being verified on boot. */
  status: AuthStatus;
  /** The signed-in operator, or `null` before sign-in completes. */
  operator: OperatorMe | null;
  /** Sign in with staff credentials. Resolves to `complete` or `mfa_required`. */
  login: (email: string, password: string) => Promise<LoginStep>;
  /** Clear the session and return to a signed-out state. */
  logout: () => Promise<void>;
  /** Whether the current operator holds a given operator-role (hides controls; never enforcement). */
  hasRole: (role: OperatorRole) => boolean;
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
