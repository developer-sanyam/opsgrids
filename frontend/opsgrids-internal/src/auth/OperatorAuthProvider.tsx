import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import * as authApi from "./api";
import { OperatorAuthContext } from "./context";
import type { AuthStatus, EnrolmentSetup, OperatorAuthValue } from "./context";
import { isOperatorToken } from "./operatorRequest";
import { isLocked } from "./problems";
import { canDo } from "./roles";
import type { Capability } from "./roles";
import type { OperatorMe, OperatorStep } from "./types";

/**
 * The credential for a sign-in that is halfway done. `mfa` is between a correct password and the
 * code; `enrol` is between a correct password and an authenticator that works, where `factorId`
 * is the server's name for the factor being confirmed. The server mints a different pre-auth
 * token for each — its `reason` claim refuses one spent on the other — so `step` says which this
 * is, and presenting the wrong one is a 401 the screen could not explain.
 */
type Pending =
  | { step: "mfa"; token: string }
  | { step: "enrol"; token: string; factorId: string | null };

interface OperatorAuthProviderProps {
  /**
   * A stand-in operator for fixture mode, so the console renders with no gateway and no sign-in —
   * offline design work and screenshots. `null` in every real build: the app decides, since only
   * it knows whether fixtures are its default.
   */
  fixtureOperator?: OperatorMe | null | undefined;
  children: ReactNode;
}

/**
 * Owns the operator session: verifies a stored token on boot, drives the sign-in (password → second
 * factor, or → enrolment), and exposes the current staff identity. A `401` from any gateway call —
 * through the client's unauthorized handler — drops the session here, which flips the guard back
 * to the sign-in screen.
 */
export function OperatorAuthProvider({ fixtureOperator = null, children }: OperatorAuthProviderProps) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [operator, setOperator] = useState<OperatorMe | null>(null);

  /**
   * The pre-auth credential and, after enrolment, the token it earned. Refs, never state and never
   * storage: a pre-auth token is one factor away from a session, so it lives and dies with this
   * tab's JavaScript the way the access token does.
   */
  const pending = useRef<Pending | null>(null);
  const held = useRef<string | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    pending.current = null;
    held.current = null;
    setOperator(null);
    setStatus("unauthenticated");
  }, []);

  // Boot: verify any token carried over from a reload, else land signed-out.
  useEffect(() => {
    let active = true;
    if (fixtureOperator) {
      setOperator(fixtureOperator);
      setStatus("authenticated");
      return;
    }
    if (getAccessToken() === null) {
      setStatus("unauthenticated");
      return;
    }
    authApi
      .me()
      .then((me) => {
        if (!active) return;
        setOperator(me);
        setStatus("authenticated");
      })
      .catch(() => {
        if (!active) return;
        clearSession();
      });
    return () => {
      active = false;
    };
  }, [clearSession, fixtureOperator]);

  // A 401 from anywhere clears the session and returns to /login (the guard redirects).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      pending.current = null;
      held.current = null;
      setOperator(null);
      setStatus("unauthenticated");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  /**
   * Keep a token the server minted, and load who it belongs to. A token that does not read as an
   * operator's is refused before it is stored: whatever produced it, it is not for this console.
   */
  const adopt = useCallback(async (accessToken: string) => {
    if (!isOperatorToken(accessToken)) {
      throw new Error("Sign-in returned a token for another audience, so it was not kept.");
    }
    setAccessToken(accessToken);
    try {
      const me = await authApi.me();
      setOperator(me);
      setStatus("authenticated");
    } catch (error) {
      setAccessToken(null);
      throw error;
    }
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<OperatorStep> => {
    const result = await authApi.login(email, password);
    if (result.status === "mfa_required") {
      pending.current = { step: "mfa", token: result.pre_auth_token };
      return "mfa";
    }
    if (result.status === "mfa_enrollment_required") {
      pending.current = { step: "enrol", token: result.pre_auth_token, factorId: null };
      return "enrol";
    }
    // The contract says a password alone never earns a session. Anything else is a protocol
    // violation, and treating it as success is how a second factor gets skipped.
    throw new Error("Sign-in did not ask for a second factor, so it was not completed.");
  }, []);

  const beginEnrolment = useCallback(async (enrollmentCode: string): Promise<EnrolmentSetup> => {
    const waiting = pending.current;
    if (!waiting || waiting.step !== "enrol") {
      throw new Error("No sign-in is waiting for an enrolment code.");
    }
    try {
      const started = await authApi.beginEnrolment(waiting.token, enrollmentCode);
      pending.current = { step: "enrol", token: waiting.token, factorId: started.factor_id };
      return { secret: started.secret, otpauthUri: started.otpauth_uri };
    } catch (error) {
      // A lockout revokes the session behind this token; there is nothing left to retry against.
      if (isLocked(error)) pending.current = null;
      throw error;
    }
  }, []);

  const confirmEnrolment = useCallback(async (code: string): Promise<string[]> => {
    const waiting = pending.current;
    if (!waiting || waiting.step !== "enrol" || waiting.factorId === null) {
      throw new Error("No enrolment is waiting for a code.");
    }
    try {
      const enrolled = await authApi.confirmEnrolment(waiting.token, waiting.factorId, code);
      // The second factor is proven and spent: replaying this pre-auth token is refused, so it is
      // dropped. The token the enrolment minted waits for the operator to have saved their codes.
      pending.current = null;
      held.current = enrolled.access_token;
      return enrolled.recovery_codes;
    } catch (error) {
      if (isLocked(error)) pending.current = null;
      throw error;
    }
  }, []);

  const finishEnrolment = useCallback(async () => {
    const token = held.current;
    if (token === null) throw new Error("No enrolment is waiting to be finished.");
    // Kept until it has taken: if loading who it belongs to fails, the operator can try again
    // instead of being stranded with codes shown once and no session.
    await adopt(token);
    held.current = null;
  }, [adopt]);

  const verifyMfa = useCallback(
    async (code: string) => {
      const waiting = pending.current;
      if (!waiting || waiting.step !== "mfa") {
        throw new Error("No sign-in is waiting for a code.");
      }
      let accessToken: string;
      try {
        accessToken = (await authApi.verifyMfa(waiting.token, code)).access_token;
      } catch (error) {
        // A wrong code leaves the pending sign-in intact; the next attempt goes through the same
        // call. A lockout does not: the server revoked the session with it.
        if (isLocked(error)) pending.current = null;
        throw error;
      }
      pending.current = null;
      await adopt(accessToken);
    },
    [adopt],
  );

  const cancelSignIn = useCallback(() => {
    pending.current = null;
    held.current = null;
  }, []);

  const logout = useCallback(async () => {
    // A fixture operator has no server session to end, and no gateway to ask.
    if (!fixtureOperator) {
      try {
        await authApi.logout();
      } catch {
        // Best-effort: even if the server call fails, drop the local session.
      }
    }
    clearSession();
  }, [clearSession, fixtureOperator]);

  const can = useCallback(
    (capability: Capability) => (operator ? canDo(operator.operator_roles, capability) : false),
    [operator],
  );

  const value = useMemo<OperatorAuthValue>(
    () => ({
      status,
      operator,
      login,
      beginEnrolment,
      confirmEnrolment,
      finishEnrolment,
      verifyMfa,
      cancelSignIn,
      logout,
      can,
    }),
    [
      status,
      operator,
      login,
      beginEnrolment,
      confirmEnrolment,
      finishEnrolment,
      verifyMfa,
      cancelSignIn,
      logout,
      can,
    ],
  );

  return <OperatorAuthContext value={value}>{children}</OperatorAuthContext>;
}
