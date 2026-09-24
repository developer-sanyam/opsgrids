/**
 * Test helpers, shared by the suites and imported by nothing else.
 *
 * The token builder makes a JWT whose payload is readable and whose signature is meaningless —
 * which is exactly what the browser sees of a real one. The console decodes a token to decide
 * whether it may *send* it, never to trust it, so a signature-free token exercises the real path.
 */

import { vi } from "vitest";

/** A JWT whose payload is readable and whose signature is meaningless. */
export function tokenWith(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA", typ: "JWT" })}.${encode(claims)}.not-a-signature`;
}

/** What `auth-service` mints for an operator: the `operator` audience, `stf`, and a second factor. */
export function operatorToken(roles: string[] = ["owner", "billing_ops", "support"]): string {
  return tokenWith({
    aud: "operator",
    stf: true,
    roles,
    amr: ["pwd", "otp"],
    sub: "8f21a0c4-5b1e-4d3a-9c77-2e6a4b0f1d10",
    exp: 4_102_444_800,
  });
}

/** What a product's token looks like: a product audience, entitlements, and no `stf`. */
export function tenantToken(): string {
  return tokenWith({ aud: "hrms", ent: ["hrms"], roles: ["hr_admin"], amr: ["pwd"], exp: 4_102_444_800 });
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

/** An RFC 7807 answer, shaped the way `platform_core.errors.ProblemError.to_problem` writes it. */
export function problem(
  status: number,
  detail: string,
  extra: { type?: string; title?: string; request_id?: string; errors?: unknown[] } = {},
): Response {
  const body = {
    type: `https://errors.opsgrids.dev/${extra.type ?? "bad-request"}`,
    title: extra.title ?? "Problem",
    status,
    detail,
    instance: "/api/v1/test",
    request_id: extra.request_id ?? null,
    ...(extra.errors ? { errors: extra.errors } : {}),
  };
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/problem+json" },
  });
}

/** One request the console made, as a test reads it. */
export interface Call {
  method: string;
  path: string;
  headers: Record<string, string>;
  body: Record<string, unknown> | null;
}

export type Handler = (call: Call) => Response | Promise<Response>;

/**
 * Stand in for `fetch`, answering by `"METHOD /path"` (the `/api/v1` prefix and any query string
 * are dropped from the key). A request no handler claims fails the test loudly rather than
 * reaching a real network, and every request that *was* made is on `calls` — which is what the
 * isolation tests assert on: what did NOT go out matters as much as what did.
 */
export function stubFetch(handlers: Record<string, Handler>): { calls: Call[] } {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://console.test");
      const method = init?.method ?? "GET";
      const path = url.pathname.replace(/^\/api\/v1/, "");
      const call: Call = {
        method,
        path: `${path}${url.search}`,
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : null,
      };
      calls.push(call);
      const handler = handlers[`${method} ${path}`];
      if (handler === undefined) {
        throw new Error(`Unexpected request: ${method} ${path}`);
      }
      return handler(call);
    }),
  );
  return { calls };
}
