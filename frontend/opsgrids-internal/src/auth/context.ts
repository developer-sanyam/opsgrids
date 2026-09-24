/**
 * The operator auth context and its hook. Kept in a `.ts` file with no component export so React
 * Fast Refresh stays happy — the provider component lives next door.
 *
 * This is deliberately not `@opsgrids/client`'s `AuthContext`. That one is the tenant sign-in —
 * password, optional second factor, organization selection — against tenant routes; the operator's
 * is a different flow on a different audience (mandatory second factor, enrolment, no
 * organization), and sharing a context would let a screen written for one silently run against
 * the other.
 */

import { createContext, useContext } from "react";

import type { Capability } from "./roles";
import type { OperatorMe, OperatorStep } from "./types";

export type AuthStatus = "loading" | "authenticated" | "unauthenticated";

/** What an authenticator app needs to enrol: the URI for a QR code, and the secret to type. */
export interface EnrolmentSetup {
  secret: string;
  otpauthUri: string;
}

export interface OperatorAuthValue {
  /** `loading` while a stored token is being verified on boot. */
  status: AuthStatus;
  /** The signed-in operator, or `null` before sign-in completes. */
  operator: OperatorMe | null;
  /**
   * Check the password. Never resolves to a session: only to the step that comes next — `mfa`
   * for an account with a confirmed factor, `enrol` for one with none. The pre-auth token behind
   * either is held here and must never be stored or put in component state.
   */
  login: (email: string, password: string) => Promise<OperatorStep>;
  /** Redeem the out-of-band enrolment code and start the first factor. */
  beginEnrolment: (enrollmentCode: string) => Promise<EnrolmentSetup>;
  /**
   * Prove the authenticator works. Resolves to the recovery codes, shown once. The session is
   * **not** live yet — `finishEnrolment` makes it so — because the screen must be able to show
   * the codes before a redirect takes it away.
   */
  confirmEnrolment: (code: string) => Promise<string[]>;
  /** The operator has saved their recovery codes: keep the session the enrolment minted. */
  finishEnrolment: () => Promise<void>;
  /**
   * Present the second factor: six digits, or a recovery code. A wrong code rejects with the
   * server's `ApiError`, leaves the pending sign-in intact and can simply be called again.
   */
  verifyMfa: (code: string) => Promise<void>;
  /** Drop a half-finished sign-in — the way back to the password step. */
  cancelSignIn: () => void;
  /** Clear the session and return to a signed-out state. */
  logout: () => Promise<void>;
  /** Whether the operator's roles admit a capability. Hides controls; never enforcement. */
  can: (capability: Capability) => boolean;
}

export const OperatorAuthContext = createContext<OperatorAuthValue | null>(null);

/** Read the operator auth context. Throws outside the provider — a wiring bug, not a runtime path. */
export function useOperatorAuth(): OperatorAuthValue {
  const value = useContext(OperatorAuthContext);
  if (value === null) {
    throw new Error("useOperatorAuth must be used within an <OperatorAuthProvider>");
  }
  return value;
}
