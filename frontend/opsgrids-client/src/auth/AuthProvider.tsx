import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import {
  getAccessToken,
  setAccessToken,
  setUnauthorizedHandler,
} from "../session";
import * as authApi from "./api";
import { AuthContext } from "./context";
import type { AuthStatus } from "./context";
import { refreshAccessToken } from "./refresh";
import { decodeAccessToken } from "./token";
import type { LoginResult, LoginStep, MeOut } from "./types";

/** How long before the access token's own `exp` the proactive refresh fires. Comfortably
 *  ahead of any request in flight, and far short of the token's usual TTL — a call that lands
 *  in the gap this misses is still caught by `apiRequest`'s own 401-retry. */
const PROACTIVE_REFRESH_LEAD_MS = 60_000;
/** How soon a *failed* proactive refresh (a network blip, not a dead session) tries again,
 *  rather than recomputing from the same, still-expiring token and firing immediately. */
const PROACTIVE_REFRESH_RETRY_MS = 5_000;

interface AuthProviderProps {
  /**
   * A stand-in identity for fixture mode, so an app renders with no gateway and no login — used
   * for offline design work and screenshots. `null` in every real build; the app decides, since
   * only it knows whether fixtures are its default (`VITE_USE_FIXTURES`).
   */
  fixtureUser?: MeOut | null | undefined;
  children: ReactNode;
}

/**
 * Owns the session: verifies a stored token on boot, drives login / org-selection / logout, and
 * exposes the current user. A `401` from any gateway call (via the client's unauthorized handler)
 * drops the session here, which flips the guard back to the login screen.
 */
export function AuthProvider({ fixtureUser = null, children }: AuthProviderProps) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<MeOut | null>(null);
  /**
   * The credential for a sign-in that is halfway done: between `login()` reporting `mfa_required`
   * and the code arriving, or between `org_selection` and the user picking an org. It is held in
   * a ref and **never** in `localStorage` or `sessionStorage` — a pre-auth token is one factor
   * away from a session, so it lives and dies with this tab's JavaScript, the way the access
   * token does. `step` says which token this is: the server mints a different one for each, and
   * presenting the wrong one is a 401 the screen cannot explain.
   */
  const pending = useRef<{ token: string; step: "mfa" | "org_selection" } | null>(null);

  const clearSession = useCallback(() => {
    setAccessToken(null);
    pending.current = null;
    setUser(null);
    setStatus("unauthenticated");
  }, []);

  /** Load the current user for an already-set token and mark the session authenticated. */
  const hydrate = useCallback(async () => {
    const me = await authApi.me();
    setUser(me);
    setStatus("authenticated");
  }, []);

  // Boot: verify any token carried over from a reload, else land signed-out. A brand-new tab,
  // or a reload that lost the in-memory token sessionStorage did not have yet, holds no access
  // token at all — but the refresh cookie may still be good, so a silent refresh gets one
  // chance to restore the session before the guard ever redirects to /login. This is the same
  // `refreshAccessToken()` a 401 retry uses, same-origin or centralised on `app.opsgrids.com`
  // (ADR-0003 addendum) depending on `configureAuthOrigin` — the provider does not need to
  // know which.
  useEffect(() => {
    let active = true;
    // Fixture mode renders the app with a stand-in identity and no gateway call.
    if (fixtureUser) {
      setUser(fixtureUser);
      setStatus("authenticated");
      return;
    }

    async function boot(): Promise<void> {
      let token = getAccessToken();
      if (!token) {
        const outcome = await refreshAccessToken();
        if (!active) return;
        if (outcome !== "ok") {
          setStatus("unauthenticated");
          return;
        }
        token = getAccessToken();
      }
      if (!token) {
        setStatus("unauthenticated");
        return;
      }
      try {
        const me = await authApi.me();
        if (!active) return;
        setUser(me);
        setStatus("authenticated");
      } catch {
        if (active) clearSession();
      }
    }

    void boot();
    return () => {
      active = false;
    };
  }, [clearSession, fixtureUser]);

  // A 401 from anywhere clears the session and returns to /login (the guard redirects).
  useEffect(() => {
    setUnauthorizedHandler(() => {
      pending.current = null;
      setUser(null);
      setStatus("unauthenticated");
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  // Refresh ~60s ahead of the access token's own expiry, so a request in flight when it turns
  // over is the exception rather than the rule. Runs only while genuinely signed in — fixture
  // mode has no token to decode, and there is nothing to renew before that.
  useEffect(() => {
    if (fixtureUser || status !== "authenticated") return;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function schedule(): void {
      const claims = decodeAccessToken();
      if (!claims?.exp) return;
      const delay = Math.max(claims.exp * 1000 - Date.now() - PROACTIVE_REFRESH_LEAD_MS, 0);
      timer = setTimeout(() => void run(), delay);
    }

    async function run(): Promise<void> {
      const outcome = await refreshAccessToken();
      if (!active) return;
      if (outcome === "unauthorized") {
        pending.current = null;
        setUser(null);
        setStatus("unauthenticated");
        return;
      }
      if (outcome === "error") {
        timer = setTimeout(() => void run(), PROACTIVE_REFRESH_RETRY_MS);
        return;
      }
      schedule();
    }

    schedule();
    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [fixtureUser, status]);

  /** Apply a completed login result: store the token and load the user. */
  const complete = useCallback(
    async (result: Extract<LoginResult, { status: "complete" }>) => {
      setAccessToken(result.access_token);
      pending.current = null;
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

  /**
   * Turn a login / MFA / select-org answer into the step the screen renders, holding whatever
   * credential the next step will need. One organization needs no picker, so it is selected here
   * and the caller is told the sign-in is complete — the same hop whether a second factor was
   * involved or not.
   */
  const stepFor = useCallback(
    async (result: LoginResult): Promise<LoginStep> => {
      if (result.status === "complete") {
        await complete(result);
        return { kind: "complete" };
      }
      if (result.status === "org_selection_required") {
        pending.current = { token: result.pre_auth_token, step: "org_selection" };
        const only = result.organizations.length === 1 ? result.organizations[0] : undefined;
        if (only) {
          await selectOrgInternal(result.pre_auth_token, only.organization_id);
          return { kind: "complete" };
        }
        return { kind: "org_selection", organizations: result.organizations };
      }
      // mfa_required: hold the pre-auth token for `verifyMfa`, and tell the screen to ask.
      pending.current = { token: result.pre_auth_token, step: "mfa" };
      return { kind: "mfa_required" };
    },
    [complete, selectOrgInternal],
  );

  const login = useCallback(
    async (email: string, password: string): Promise<LoginStep> => {
      const result = await authApi.login(email, password);
      return stepFor(result);
    },
    [stepFor],
  );

  /**
   * Finish an MFA sign-in with the code the user read off their authenticator, or a recovery
   * code. Resolves to the same steps `login` does: `complete`, or `org_selection` when the second
   * factor was right and the account reaches several organizations.
   *
   * A wrong code rejects with the server's `ApiError` and changes nothing here — the pre-auth
   * token is still held, so the screen shows `problemMessage(error)` beside the field and the
   * next attempt goes through the same call.
   */
  const verifyMfa = useCallback(
    async (code: string): Promise<LoginStep> => {
      const waiting = pending.current;
      if (!waiting || waiting.step !== "mfa") {
        throw new Error("No sign-in is waiting for a code.");
      }
      const result = await authApi.mfaVerify(waiting.token, code);
      return stepFor(result);
    },
    [stepFor],
  );

  const selectOrg = useCallback(
    async (organizationId: string) => {
      const waiting = pending.current;
      if (!waiting || waiting.step !== "org_selection") {
        throw new Error("No pending sign-in to finish.");
      }
      await selectOrgInternal(waiting.token, organizationId);
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
    () => ({ status, user, login, verifyMfa, selectOrg, logout }),
    [status, user, login, verifyMfa, selectOrg, logout],
  );

  return <AuthContext value={value}>{children}</AuthContext>;
}
