/**
 * Thin, typed calls to the auth service through the gateway. Login and select-org carry a
 * pre-auth credential in the body rather than a bearer token, and a 401 from them means "wrong
 * credentials" — not "stale session" — so they never trip the global sign-out handler.
 */

import { apiRequest } from "../http";
import { getAuthOrigin } from "./origin";
import { StepUpError, toStepUpError } from "./stepUpError";
import type { LoginResult, MeOut, StepUpToken } from "./types";

/** `POST /auth/login`. Returns a discriminated result on `status`. */
export function login(email: string, password: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/login", {
    method: "POST",
    body: { email, password },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `POST /auth/select-org`. Exchanges a pre-auth token + chosen org for an access token. */
export function selectOrg(preAuthToken: string, organizationId: string): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/select-org", {
    method: "POST",
    body: { pre_auth_token: preAuthToken, organization_id: organizationId },
    auth: false,
    signalUnauthorized: false,
  });
}

/**
 * `POST /auth/mfa/verify`. The second step of sign-in: the pre-auth token `login` answered with,
 * plus six digits from an authenticator app or a recovery code.
 *
 * Answers the same union `login` does — `complete`, or `org_selection_required` **with a fresh
 * pre-auth token** for a user who belongs to several organizations. A wrong code is a 401 carrying
 * the server's own sentence ("That code is not valid"); the pre-auth token survives it, so the
 * screen keeps the field and lets the user try again. Like `login`, it never trips the global
 * sign-out handler: there is no session here to be stale.
 */
export function mfaVerify(
  preAuthToken: string,
  code: string,
  organization?: string,
): Promise<LoginResult> {
  return apiRequest<LoginResult>("/auth/mfa/verify", {
    method: "POST",
    body: {
      pre_auth_token: preAuthToken,
      code,
      ...(organization !== undefined ? { organization } : {}),
    },
    auth: false,
    signalUnauthorized: false,
  });
}

/** `GET /auth/me`. Who the current token belongs to, plus their org memberships. */
export function me(): Promise<MeOut> {
  return apiRequest<MeOut>("/auth/me");
}

/**
 * How long a step-up request may run before it is abandoned. The dialog that asked for the code
 * cannot be dismissed while the request is out, so a request that never answers must end on its
 * own — otherwise a dead connection leaves the person in front of a dialog they cannot close.
 */
const STEP_UP_TIMEOUT_MS = 10_000;

function isStepUpToken(value: unknown): value is StepUpToken {
  if (typeof value !== "object" || value === null) return false;
  const token = value as Record<string, unknown>;
  return (
    typeof token.access_token === "string" &&
    token.access_token !== "" &&
    typeof token.expires_in === "number" &&
    Number.isFinite(token.expires_in) &&
    token.expires_in > 0
  );
}

/**
 * `POST /auth/mfa/step-up`. Trades a fresh authenticator code for a short-lived token whose `amr`
 * includes `otp` — the proof a "reveal" call needs on top of its permission.
 *
 * Bearer-authenticated with the **session** token, and `signalUnauthorized: false`: a wrong code
 * is a 401, and the default handling would refresh the session, retry, and then sign the person
 * out for mistyping six digits. Here a 401 is only ever a rejected code (or a session the caller
 * checks separately — see `stepup/requestToken.ts`).
 *
 * The result must never reach `setAccessToken`. It is held by `stepup/holder.ts`, in memory, for
 * its own few minutes, and presented as the bearer of the one call that needs it.
 *
 * Every failure is a `StepUpError` with a `failure` kind (`wrong-code`, `locked`, `not-enrolled`,
 * `sso`, `reauthenticate`, `unavailable`) so a screen never reads a status code or the server's
 * sentence to decide what to show.
 */
export async function stepUp(code: string): Promise<StepUpToken> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STEP_UP_TIMEOUT_MS);
  try {
    const token = await apiRequest<unknown>("/auth/mfa/step-up", {
      method: "POST",
      body: { code },
      signalUnauthorized: false,
      signal: controller.signal,
    });
    if (!isStepUpToken(token)) {
      throw new StepUpError("unavailable", "The server did not answer with a step-up token.", null, null);
    }
    return token;
  } catch (error) {
    throw toStepUpError(error);
  } finally {
    clearTimeout(timer);
  }
}

/**
 * `POST /auth/logout`. Best-effort — clears the refresh cookie server-side.
 *
 * Goes to the auth origin (`configureAuthOrigin`), same as `auth/refresh.ts` — same-origin by
 * default, cross-origin to `app.opsgrids.com` for every product SPA that centralises its
 * refresh there (ADR-0003 addendum). Ending the session on the host that actually holds the
 * cookie is what makes every other open tab and app fail its next refresh and land back on
 * `/login`; logging out only against this app's own (same-)origin would leave that cookie
 * live everywhere else.
 *
 * `auth: false` (security review, fix round 1, B2): this call is cookie-authenticated, and the
 * gateway's scoped CORS for `/api/v1/auth/logout` allows exactly `Content-Type` and
 * `X-Requested-With` — never `Authorization` (`platform_core.app._PathScopedCorsMiddleware`).
 * `apiRequest`'s default `auth: true` would attach `Authorization: Bearer …` alongside the CSRF
 * header on every cross-origin logout, which is a header the preflight never allow-listed: the
 * browser refuses the preflight outright (400, before the request ever reaches the server), and
 * the `try { await authApi.logout() } catch {}` in `AuthProvider.logout` swallowed that silently
 * — the server-side session, and the cookie, were never actually cleared. Logout does not need a
 * bearer token to identify who is signing out; the cookie already says.
 */
export function logout(): Promise<void> {
  const origin = getAuthOrigin();
  return apiRequest<void>("/auth/logout", {
    method: "POST",
    auth: false,
    signalUnauthorized: false,
    headers: { "X-Requested-With": "opsgrids" },
    ...(origin ? { baseUrl: origin, credentials: "include" } : {}),
  });
}
