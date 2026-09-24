import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { USE_FIXTURES } from "@/api/client";
import * as authApi from "./api";
import { AuthContext } from "./context";
import type { AuthStatus } from "./context";
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "./session";
import type { LoginStep, OperatorMe, OperatorRole } from "./types";

/** Loaded only when `USE_FIXTURES` is true — see `fixtureOperator.ts` for why this is a dynamic
 *  import rather than a static one. */
function loadFixtureOperator() {
  return import("./fixtureOperator");
}

/**
 * Owns the operator session: verifies a stored token on boot, drives login / logout, and exposes
 * the current staff identity. A `401` from any admin-gateway call (via the client's unauthorized
 * handler) drops the session here, which flips the guard back to the login screen.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [operator, setOperator] = useState<OperatorMe | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    setOperator(null);
    setStatus("unauthenticated");
  }, []);

  // Boot: verify any token carried over from a reload, else land signed-out.
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
    const token = getAccessToken();
    if (!token) {
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
  }, [clearSession]);

  // A 401 from anywhere clears the session and returns to /login (the guard redirects).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      setOperator(null);
      setStatus("unauthenticated");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (email: string, password: string): Promise<LoginStep> => {
    // Fixtures mode: accept the credential and route straight in with the stand-in operator.
    if (USE_FIXTURES) {
      const { FIXTURE_OPERATOR, FIXTURE_TOKEN } = await loadFixtureOperator();
      setAccessToken(FIXTURE_TOKEN);
      setOperator({ ...FIXTURE_OPERATOR, email: email.trim() || FIXTURE_OPERATOR.email });
      setStatus("authenticated");
      return { kind: "complete" };
    }
    const result = await authApi.login(email, password);
    if (result.status === "complete") {
      setAccessToken(result.access_token);
      const me = await authApi.me();
      setOperator(me);
      setStatus("authenticated");
      return { kind: "complete" };
    }
    // mfa_required — outside this slice; the screen surfaces a clear message.
    return { kind: "mfa_required" };
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
    () => ({ status, operator, login, logout, hasRole }),
    [status, operator, login, logout, hasRole],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
