/**
 * The one place a request leaves the console. Everything goes to the same-origin `/api/v1` prefix,
 * which Vite proxies to **admin-gateway** in dev (see `vite.config.ts`) — the operator audience,
 * never a product gateway (ADR-0003 §2). The operator token is attached from the in-memory
 * session, and a `401` trips the global "signed out" handler so a stale token bounces the operator
 * back to the login screen. Gateway errors arrive as RFC 7807 `problem+json`.
 */

import { getAccessToken, notifyUnauthorized } from "@/auth/session";

/** Same-origin base. The Vite dev proxy forwards this to `http://localhost:8110` (admin-gateway). */
export const API_BASE = "/api/v1";

/** The RFC 7807 body the gateway returns on an error. */
export interface ProblemJson {
  type?: string;
  title?: string;
  status?: number;
  detail?: string;
  instance?: string;
  request_id?: string;
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

interface RequestOptions {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Attach the bearer token (default true). Login opts out — it carries its own credential. */
  auth?: boolean;
  /** Fire the global unauthorized handler on a 401 (default true). Login/logout opt out. */
  signalUnauthorized?: boolean;
  /** Extra request headers — e.g. `Idempotency-Key` on a POST that must never double-apply on a
   *  retried click, or the CSRF header `/auth/operator/logout` requires. Cannot override `Accept`,
   *  `Content-Type` or `Authorization`, which this function owns. */
  headers?: Record<string, string>;
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

/** Issue a request to admin-gateway and decode the JSON body (or `undefined` for a 204). */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, auth = true, signalUnauthorized = true } = options;

  const headers: Record<string, string> = { ...options.headers, Accept: "application/json" };
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

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}
