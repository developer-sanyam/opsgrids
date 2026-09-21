/**
 * The one place a request leaves an app. Everything goes to the same-origin `/api/v1` prefix,
 * which Vite proxies to the gateway in dev (see each app's `vite.config.ts`) — so the browser
 * never makes a cross-origin call and CORS never enters the picture. The access token is attached
 * from the in-memory session, and a `401` trips the global "signed out" handler so a stale token
 * bounces the user back to the login screen. Gateway errors arrive as RFC 7807 `problem+json`.
 */

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
  /** Attach the bearer token (default true). Login and select-org opt out — they carry their own. */
  auth?: boolean | undefined;
  /** Fire the global unauthorized handler on a 401 (default true). Login/logout opt out. */
  signalUnauthorized?: boolean | undefined;
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
  const { method = "GET", body, auth = true, signalUnauthorized = true } = options;

  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getAccessToken();
    if (token) headers.Authorization = `Bearer ${token}`;
  }

  const res = await fetch(`${API_BASE}${path}`, {
    method,
    headers,
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });

  if (res.status === 401 && signalUnauthorized) notifyUnauthorized();

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
