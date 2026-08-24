import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { USE_FIXTURES } from "@/api/client";
import * as authApi from "./api";
import { AuthContext } from "./context";
import type { AuthStatus } from "./context";
import {
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from "./session";
import type { LoginResult, LoginStep, MeOut } from "./types";

/**
 * A stand-in identity for fixture mode (`VITE_USE_FIXTURES=true`), so the whole app renders with
 * no gateway and no login — used for offline design work and screenshots. Never reached live.
 */
const FIXTURE_ORG = {
  organization_id: "org-fixture-0001",
  slug: "atmora-india",
  name: "Atmora India",
  roles: ["hr_admin"],
};

const FIXTURE_USER: MeOut = {
  user_id: "user-fixture-0001",
  email: "sneha.kulkarni@atmora.example.com",
  full_name: "Sneha Kulkarni",
  organization_id: FIXTURE_ORG.organization_id,
  membership_id: "mem-fixture-0001",
  roles: FIXTURE_ORG.roles,
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [FIXTURE_ORG],
};

/**
 * Owns the session: verifies a stored token on boot, drives login / org-selection / logout, and
 * exposes the current user. A `401` from any gateway call (via the client's unauthorized handler)
 * drops the session here, which flips the guard back to the login screen.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<MeOut | null>(null);
  /** Held between `login()` reporting `org_selection` and the user picking an org. */
  const preAuthToken = useRef<string | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    preAuthToken.current = null;
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  /** Load the current user for an already-set token and mark the session authenticated. */
  const hydrate = useCallback(async () => {
    const me = await authApi.me();
    setUser(me);
    setStatus("authenticated");
  }, []);

  // Boot: verify any token carried over from a reload, else land signed-out.
  useEffect(() => {
    let active = true;
    // Fixture mode renders the app with a stand-in identity and no gateway call.
    if (USE_FIXTURES) {
      setUser(FIXTURE_USER);
      setStatus("authenticated");
      return;
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
        setUser(me);
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
      preAuthToken.current = null;
      setUser(null);
      setStatus("unauthenticated");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  /** Apply a completed login result: store the token and load the user. */
  const complete = useCallback(
    async (result: Extract<LoginResult, { status: "complete" }>) => {
      setAccessToken(result.access_token);
      preAuthToken.current = null;
      await hydrate();
    },
    [hydrate],
  );

  const selectOrgInternal = useCallback(
    async (token: string, organizationId: string) => {
      const result = await authApi.selectOrg(token, organizationId);
      if (result.status !== "complete") {
        throw new Error("Selecting an organization did not complete sign-in.");
      }
      await complete(result);
    },
    [complete],
  );

  const login = useCallback(
    async (email: string, password: string): Promise<LoginStep> => {
      const result = await authApi.login(email, password);
      if (result.status === "complete") {
        await complete(result);
        return { kind: "complete" };
      }
      if (result.status === "org_selection_required") {
        preAuthToken.current = result.pre_auth_token;
        // A single membership needs no picker — select it and continue straight in.
        if (result.organizations.length === 1) {
          const only = result.organizations[0]!;
          await selectOrgInternal(result.pre_auth_token, only.organization_id);
          return { kind: "complete" };
        }
        return { kind: "org_selection", organizations: result.organizations };
      }
      // mfa_required — outside this slice; the screen surfaces a clear message.
      preAuthToken.current = result.pre_auth_token;
      return { kind: "mfa_required" };
    },
    [complete, selectOrgInternal],
  );

  const selectOrg = useCallback(
    async (organizationId: string) => {
      const token = preAuthToken.current;
      if (!token) throw new Error("No pending sign-in to finish.");
      await selectOrgInternal(token, organizationId);
    },
    [selectOrgInternal],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } catch {
      // Best-effort: even if the server call fails, drop the local session.
    }
    clearSession();
  }, [clearSession]);

  const value = useMemo(
    () => ({ status, user, login, selectOrg, logout }),
    [status, user, login, selectOrg, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
