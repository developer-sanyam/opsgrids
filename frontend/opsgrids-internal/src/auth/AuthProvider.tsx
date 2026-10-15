import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { USE_FIXTURES } from "@/api/client";
import * as authApi from "./api";
import { AuthContext } from "./context";
import type { AuthStatus } from "./context";
import { isLocked } from "./problems";
import { refreshOperatorToken } from "./refresh";
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "./session";
import type { EnrolmentSetup, OperatorMe, OperatorRole, OperatorStep } from "./types";

/** Loaded only when `USE_FIXTURES` is true — see `fixtureOperator.ts` for why this is a dynamic
 *  import rather than a static one. */
function loadFixtureOperator() {
  return import("./fixtureOperator");
}

/**
 * The credential for a sign-in that is halfway done. `mfa` is between a correct password and the
 * code; `enrol` is between a correct password and an authenticator that works, where `factorId` is
 * the server's name for the factor being confirmed — `null` until `beginEnrolment` names it. The
 * server mints a different pre-auth token for each (its `reason` claim refuses one spent on the
 * other), so `step` says which this is.
 *
 * A `useRef`, never React state and never `sessionStorage`: a pre-auth token is one factor away
 * from a session, so it lives and dies with this tab's JavaScript the same way the access token
 * does, and is discarded — set back to `null` — the moment it is spent or abandoned.
 */
type Pending = { step: "mfa"; token: string } | { step: "enrol"; token: string; factorId: string | null };

/**
 * Owns the operator session: verifies a stored token on boot, drives the sign-in (password, then a
 * mandatory second factor — verify or enrol), and exposes the current staff identity. A `401` from
 * any admin-gateway call (via the client's unauthorized handler) drops the session here, which
 * flips the guard back to the login screen.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [operator, setOperator] = useState<OperatorMe | null>(null);

  const pending = useRef<Pending | null>(null);
  /** The token a completed enrolment minted, held until the operator confirms they saved their
   *  recovery codes — `finishEnrolment` is what actually adopts it. */
  const held = useRef<string | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    pending.current = null;
    held.current = null;
    setOperator(null);
    setStatus("unauthenticated");
  }, []);

  // Boot: verify any token carried over from a reload. With none (a new browser run or a new tab,
  // where `sessionStorage` starts empty) the refresh cookie may still hold a live session, so one
  // silent refresh gets a chance before landing signed out. A stored token that has since expired
  // needs nothing extra here: `me()`'s 401 refreshes and retries inside `apiRequest`.
  useEffect(() => {
    let active = true;
    // Fixtures mode renders the console with a stand-in operator and no gateway call.
    if (USE_FIXTURES) {
      void loadFixtureOperator().then(({ FIXTURE_OPERATOR, FIXTURE_TOKEN }) => {
        if (!active) return;
        setAccessToken(FIXTURE_TOKEN);
        setOperator(FIXTURE_OPERATOR);
        setStatus("authenticated");
      });
      return () => {
        active = false;
      };
    }
    const restore = async (): Promise<boolean> =>
      getAccessToken() !== null || (await refreshOperatorToken()) === "ok";
    void restore()
      .then((restored) => {
        if (!restored) throw new Error("No operator session to restore.");
        return authApi.me();
      })
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
  }, [clearSession]);

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

  /** Keep a token the server minted, and load who it belongs to. Nothing is committed to state
   *  until `me()` resolves, so a failed lookup leaves the operator able to try again rather than
   *  stranded on a token nothing has confirmed. */
  const adopt = useCallback(async (accessToken: string) => {
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
    // Fixtures mode: accept the credential and route straight in with the stand-in operator —
    // there is no live backend to ask for a second factor.
    if (USE_FIXTURES) {
      const { FIXTURE_OPERATOR, FIXTURE_TOKEN } = await loadFixtureOperator();
      setAccessToken(FIXTURE_TOKEN);
      setOperator({ ...FIXTURE_OPERATOR, email: email.trim() || FIXTURE_OPERATOR.email });
      setStatus("authenticated");
      return "mfa";
    }
    const result = await authApi.login(email, password);
    if (result.status === "mfa_required") {
      pending.current = { step: "mfa", token: result.pre_auth_token };
      return "mfa";
    }
    pending.current = { step: "enrol", token: result.pre_auth_token, factorId: null };
    return "enrol";
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
      // discarded now. The token enrolment minted waits for the operator to save their codes.
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
        // call. A lockout does not — the server revoked the session with it.
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
    if (!USE_FIXTURES) {
      try {
        await authApi.logout();
      } catch {
        // Best-effort: even if the server call fails, drop the local session.
      }
    }
    clearSession();
  }, [clearSession]);

  const hasRole = useCallback(
    (role: OperatorRole) => operator?.operator_roles.includes(role) ?? false,
    [operator],
  );

  const value = useMemo(
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
      hasRole,
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
      hasRole,
    ],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
