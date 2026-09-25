/**
 * The one place a request leaves an app. Everything goes to the same-origin `/api/v1` prefix,
 * which Vite proxies to the gateway in dev (see each app's `vite.config.ts`) — so the browser
 * never makes a cross-origin call and CORS never enters the picture. The access token is attached
 * from the in-memory session.
 *
 * A `401` no longer bounces straight to the login screen. It first tries exactly one silent
 * refresh (`auth/refresh.ts`, single-flight and shared with the proactive timer and the boot
 * restore in `AuthProvider`) and retries the original request once with whatever token that
 * produced — this is what stops a session dying every ~15 minutes on the access token's own
 * TTL, and what heals a `token-stale` 401 (a permission change) without a forced re-login. Only
 * when the refresh itself cannot produce a token does the global "signed out" handler fire.
 * `auth: false` and `signalUnauthorized: false` calls (login, MFA, select-org, logout) opt out
 * of all of this — there is no session to refresh, or refreshing one to log out of it would be
 * its own kind of loop. Gateway errors arrive as RFC 7807 `problem+json`.
 */

import { refreshAccessToken } from "./auth/refresh";
import { getAccessToken, notifyUnauthorized } from "./session";

/** Same-origin base. The Vite dev proxy forwards this to the gateway. */
export const API_BASE = "/api/v1";

/** The RFC 7807 body the gateway returns on an error. */
export interface ProblemJson {
  type?: string | undefined;
  title?: string | undefined;
  status?: number | undefined;
  detail?: string | undefined;
  instance?: string | undefined;
  request_id?: string | undefined;
  /**
   * The platform's one extension member: field-level detail on a validation failure, and on any
   * hand-raised problem that names a field. Declared here because it is a platform contract
   * (`platform_core.errors`) rather than one API's private extension — read it through
   * `fieldErrors` / `fieldErrorMap`, which drop anything that is not the documented shape.
   */
  errors?: readonly { field: string; code: string; message: string }[] | undefined;
}

/** A failed request, carrying the HTTP status and the parsed problem document when there is one. */
export class ApiError extends Error {
  readonly status: number;
  readonly problem: ProblemJson | null;

  constructor(status: number, message: string, problem: ProblemJson | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problem = problem;
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE" | undefined;
  body?: unknown | undefined;
  /** Attach the bearer token (default true). Login and select-org opt out — they carry their own.
   *  Also gates the 401-refresh-and-retry below: a call with no token to begin with has no
   *  session for a refresh to save. */
  auth?: boolean | undefined;
  /** Fire the global unauthorized handler on a 401 (default true), after a silent refresh has
   *  had its one chance to fix it. Login/MFA/select-org/logout opt out — none of them has a
   *  session to refresh, or (logout) refreshing one just to end it would be its own loop. */
  signalUnauthorized?: boolean | undefined;
  /** Extra request headers — e.g. an `Idempotency-Key` that makes a retried write a replay.
   *  Cannot override `Accept`, `Content-Type` or `Authorization`, which this function owns. */
  headers?: Record<string, string> | undefined;
  /** An absolute origin prepended ahead of the ordinary `API_BASE` prefix, no trailing slash
   *  (`https://app.opsgrids.com`) — only a cross-origin `/auth/logout` call ever sets this
   *  (`auth/api.ts`, ADR-0003 addendum). `undefined` keeps the ordinary same-origin path every
   *  other call uses. */
  baseUrl?: string | undefined;
  /** `fetch`'s credentials mode. Only needed alongside `baseUrl`: a cross-origin call needs
   *  `"include"` or the browser withholds the cookie the call exists to use. */
  credentials?: RequestCredentials | undefined;
  /** Abort the request — a debounced search firing a new query cancels the one it superseded,
   *  rather than letting a slow, stale response race a fresh one onto the screen. */
  signal?: AbortSignal | undefined;
}

async function readProblem(res: Response): Promise<ProblemJson | null> {
  try {
    return (await res.json()) as ProblemJson;
  } catch {
    // Not every error body is JSON; fall back to the status line.
    return null;
  }
}

function messageFor(problem: ProblemJson | null, status: number): string {
  return problem?.detail ?? problem?.title ?? `Request failed with ${status}`;
}

/** Issue a request to the gateway and decode the JSON body (or `undefined` for a 204). */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return performRequest<T>(path, options, /* allowRefresh */ true);
}

async function performRequest<T>(
  path: string,
  options: RequestOptions,
  allowRefresh: boolean,
): Promise<T> {
  const { method = "GET", body, auth = true, signalUnauthorized = true, baseUrl, credentials, signal } =
    options;

  const headers: Record<string, string> = { ...options.headers, Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${baseUrl ?? ""}${API_BASE}${path}`, {
    method,
    headers,
    ...(credentials ? { credentials } : {}),
    ...(signal ? { signal } : {}),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (res.status === 401 && signalUnauthorized && auth) {
    // One single-flight silent refresh, shared with the proactive timer and the boot restore,
    // and one retry — never a loop, because the retry passes `allowRefresh: false`, so a 401 on
    // the retry itself falls straight through to `notifyUnauthorized` below instead of trying
    // a second refresh.
    if (allowRefresh) {
      const result = await refreshAccessToken();
      if (result.outcome === "ok") return performRequest<T>(path, options, false);
    }
    notifyUnauthorized();
  } else if (res.status === 401 && signalUnauthorized) {
    notifyUnauthorized();
  }

  if (!res.ok) {
    const problem = await readProblem(res);
    throw new ApiError(res.status, messageFor(problem, res.status), problem);
  }

  // No body is a valid answer, and not only on 204: `password/forgot` is a 202 with nothing in
  // it, on purpose (a body would be a way to learn whether the address exists). Reading the
  // text first is what stops an empty 2xx from becoming "Unexpected end of JSON input" on the
  // screen after a request that succeeded.
  if (res.status === 204 || res.status === 205) return undefined as T;
  const text = await res.text();
  if (text.trim() === "") return undefined as T;
  return JSON.parse(text) as T;
}
