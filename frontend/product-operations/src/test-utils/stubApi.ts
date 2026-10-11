import { vi } from "vitest";

/** What a stubbed endpoint answers with. A problem body is any object with `detail`. */
export interface StubResponse {
  status?: number;
  body?: unknown;
}

export interface RecordedRequest {
  method: string;
  /** The path after `/api/v1`, query string removed. */
  path: string;
  query: URLSearchParams;
  headers: Record<string, string>;
  body: unknown;
}

interface Route {
  method: string;
  match: RegExp;
  respond: (request: RecordedRequest) => StubResponse | Promise<StubResponse>;
}

export interface StubApi {
  requests: RecordedRequest[];
  /** Every request that matched `method` and `match`, in the order they were made. */
  calls: (method: string, match: RegExp) => RecordedRequest[];
}

export function route(
  method: string,
  match: RegExp,
  respond: StubResponse | ((request: RecordedRequest) => StubResponse | Promise<StubResponse>),
): Route {
  return { method, match, respond: typeof respond === "function" ? respond : () => respond };
}

/** An RFC 7807 problem the way the gateway sends one. */
export function problem(status: number, detail: string, extra: Record<string, unknown> = {}): StubResponse {
  return { status, body: { type: "https://errors.opsgrids.dev/problem", title: "Problem", status, detail, ...extra } };
}

/**
 * Replaces `fetch` with a router over `routes`. Anything unmatched is a 404 problem, so a screen
 * that calls an endpoint a test did not expect fails loudly instead of hanging. Tests assert on
 * `requests` — the method, path, headers and JSON body the screen actually sent.
 */
export function stubApi(routes: Route[]): StubApi {
  const requests: RecordedRequest[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string, init: RequestInit = {}) => {
      const url = new URL(input, "http://localhost");
      const request: RecordedRequest = {
        method: init.method ?? "GET",
        path: url.pathname.replace(/^\/api\/v1/, ""),
        query: url.searchParams,
        headers: (init.headers ?? {}) as Record<string, string>,
        body: typeof init.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      };
      requests.push(request);
      const found = routes.find((r) => r.method === request.method && r.match.test(request.path));
      const answer = found
        ? await found.respond(request)
        : problem(404, `No stub for ${request.method} ${request.path}`);
      const status = answer.status ?? 200;
      return new Response(status === 204 ? null : JSON.stringify(answer.body ?? {}), {
        status,
        headers: { "content-type": "application/json" },
      });
    }),
  );
  return {
    requests,
    calls: (method, match) => requests.filter((r) => r.method === method && match.test(r.path)),
  };
}
