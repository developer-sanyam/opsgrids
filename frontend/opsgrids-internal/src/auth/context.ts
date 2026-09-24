/**
 * The operator auth context and its hook. Kept in a `.ts` file with no component export so React
 * Fast Refresh stays happy — the provider component lives next door in `AuthProvider.tsx`.
 */

import { createContext, useContext } from "react";

import type { EnrolmentSetup, OperatorMe, OperatorRole, OperatorStep } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

export interface AuthContextValue {
  /** `loading` while a stored token is being verified on boot. */
  status: AuthStatus;
  /** The signed-in operator, or `null` before sign-in completes. */
  operator: OperatorMe | null;
  /**
   * Check the password. Never resolves to a session by itself — only to the step that comes next:
   * `"mfa"` for an account with a confirmed factor, `"enrol"` for one with none. The pre-auth token
   * behind either is held inside the provider and never surfaced here.
   */
  login: (email: string, password: string) => Promise<OperatorStep>;
  /** Redeem the out-of-band enrolment code an owner issued, and start the first factor. */
  beginEnrolment: (enrollmentCode: string) => Promise<EnrolmentSetup>;
  /**
   * Prove the authenticator works. Resolves to the recovery codes, shown once. The session is
   * **not** live yet — `finishEnrolment` makes it so — so the screen can show the codes before a
   * redirect takes it away.
   */
  confirmEnrolment: (code: string) => Promise<string[]>;
  /** The operator has saved their recovery codes: keep the session enrolment already minted. */
  finishEnrolment: () => Promise<void>;
  /** Present the second factor: six digits, or a recovery code. */
  verifyMfa: (code: string) => Promise<void>;
  /** Drop a half-finished sign-in — the way back to the password step. */
  cancelSignIn: () => void;
  /** Clear the session and return to a signed-out state. */
  logout: () => Promise<void>;
  /** Whether the current operator holds a given operator-role (hides controls; never enforcement —
   *  the server refuses the call regardless of what this reports). */
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
