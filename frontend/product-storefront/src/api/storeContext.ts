/**
 * "Which shop have I arrived at?" — the first call a buyer's browser makes, before anything is
 * rendered (ADR-0012 §2).
 *
 * ──────────────────────────────────────────────────────────────────────────────────────────
 * **A 404 and everything else are different facts, and keeping them apart is the whole point
 * of this module.**
 *
 * > A 404 means we looked and there is no shop at this address. **Anything else means we could
 * > not look.**
 *
 * This is ADR-0010 §13's "unknown is not no" on the page a real buyer sees. A `catch` that
 * renders "not found" for any failure tells a shopper standing in front of a working shop that
 * it does not exist — from a rate limiter that failed closed, a dropped connection, or a body
 * this build could not parse. So every failure below carries a `reason`, only an HTTP 404 ever
 * produces `"not_found"`, and `BuyerApp` branches on that and nothing else.
 *
 * A 422 is `"unavailable"` too, deliberately: the schema refuses a host the *bundle* sent, so
 * it is our bug and not evidence about the merchant's shop.
 * ──────────────────────────────────────────────────────────────────────────────────────────
 *
 * **Nothing here imports `@opsgrids/client`.** That package's HTTP layer imports the session,
 * and the buyer bundle must contain no token machinery at all: ADR-0001 scopes the refresh
 * cookie to `.opsgrids.com`, so on a merchant's own domain a session cannot exist and auth code
 * is dead weight on the first-paint path with an XSS surface and nothing to gain (ADR-0012 §2).
 * `STOREFRONT_API_BASE` is therefore declared here and pinned to the SDK's `API_BASE` by a test
 * rather than imported from it.
 *
 * The shape is `StoreContextOut` / `PublicStoreContextIn` in
 * `libs/contracts/openapi/storefront.json`, and `storeContext.contract.test.ts` reads that file
 * and compares it against these declarations. Nothing below may be read off ADR-0012's prose
 * instead — ADR-0010's erratum 5 records what that cost the last time.
 */

/**
 * Same-origin base, the value `@opsgrids/client` exports as `API_BASE`.
 *
 * Both the page and its API calls are same-origin on a custom domain (ADR-0012 §2): the browser
 * on `merchant.com` calls `merchant.com/api/...`, Caddy proxies it to the gateway, and
 * `cors_origins` never grows a row per merchant. Vite's dev proxy is the same arrangement.
 *
 * `storeContext.test.ts` asserts this equals the SDK's constant, so the two cannot drift.
 */
export const STOREFRONT_API_BASE = "/api/v1";

/** `POST /api/v1/storefront/public/store-context`. An exact public path (`gateway.py`). */
const STORE_CONTEXT_PATH = `${STOREFRONT_API_BASE}/storefront/public/store-context`;

/**
 * The client's own budget. Past the public route's 2-second server budget (ADR-0012 §13) so a
 * slow-but-working lookup is never cut off here first, and short enough that a buyer is not left
 * looking at a skeleton. **No retries** — the buyer's own retry button is the honest place for
 * one, and a retry on the first-paint path only makes someone wait longer to be told nothing.
 */
const STORE_CONTEXT_TIMEOUT_MS = 8_000;

/**
 * `PublicStoreContextIn` — and it carries `host` and nothing else.
 *
 * The value is `window.location.hostname`: no port, no scheme, no constructed value, nothing the
 * bundle was built with. **The bundle is byte-identical for every merchant**, which is what lets
 * one build serve `greenleaf.stores.opsgrids.com` and `merchant.com` with no config file per
 * store. The server normalises (case, trailing dot, punycode) and refuses what is not a hostname.
 */
export interface PublicStoreContextIn {
  host: string;
}

/**
 * `StoreContextOut` — which store this address is, and what it is called.
 *
 * **`tagline` and `logo_text` are required-but-nullable**: the keys are always present and may be
 * `null`. Declared nullable rather than optional on purpose — `exactOptionalPropertyTypes` is on,
 * so `?: string | null` and `: string | null` are genuinely different types, and "the merchant has
 * not written one" is one state on the wire rather than two that compare differently. Whether
 * `logo_text` falls back to `name` is the renderer's decision; the server copies neither into the
 * other.
 *
 * **`name`, `tagline` and `logo_text` are merchant-authored text returned verbatim.** The service
 * trims, bounds, and strips control characters and bidi overrides — and nothing else, so
 * `<b>Verma</b> & Sons` round-trips as those exact characters. **Output encoding is this bundle's
 * job.** React escapes text children, so the rule that matters is the negative one: nothing on
 * the buyer surface may pass these through `dangerouslySetInnerHTML` or build markup by hand.
 * `BuyerApp.test.tsx` pins it.
 */
export interface StoreContextOut {
  /** The slug. **Every later public call puts this in its `store` field** — including ADR-0010's
   *  `POST /public/serviceability`, whose body is `extra="forbid"` and is unchanged by this route
   *  existing. Resolving the host once in a bootstrap is what buys that (ADR-0012 §2). */
  store: string;
  name: string;
  tagline: string | null;
  logo_text: string | null;
  currency: string;
  timezone: string;
  /** The address this store should be linked to, **as the server sent it**. It goes in
   *  `<link rel="canonical">` verbatim; it is never constructed here. */
  canonical_host: string;
  /** Slice 1 does not redirect. `false` is not an error and not a signal to navigate — at most a
   *  quiet line (ADR-0012 §8). */
  is_canonical: boolean;
  checked_at: string;
}

/** Why the bootstrap did not produce a store. Two values, and they are not interchangeable. */
export type StoreContextFailure =
  /** HTTP 404: we looked, and there is no published shop at this address. Retrying cannot help. */
  | "not_found"
  /** Everything else: we could not look. A retry is the correct affordance. */
  | "unavailable";

/** A bootstrap that did not produce a store, carrying which of the two facts it is. */
export class StoreContextError extends Error {
  readonly reason: StoreContextFailure;
  /** The HTTP status, or `0` when the request never got an answer. Diagnostic only. */
  readonly status: number;

  constructor(reason: StoreContextFailure, status: number, message: string) {
    super(message);
    this.name = "StoreContextError";
    this.reason = reason;
    this.status = status;
  }
}

/* ------------------------------- the session cache ------------------------------- */

/**
 * Where the answer is kept for the tab.
 *
 * **`sessionStorage`, once per session — not per page view, and never `localStorage`.**
 *
 * The anonymous limit is 60 requests per minute **per client IP**, and an office or a
 * shopping-mall wifi is one IP (ADR-0012 §12). Caching per session keeps a browsing buyer far
 * from it.
 *
 * `localStorage` is wrong twice over: this is not the buyer's data, and it goes stale the moment
 * a merchant renames their shop — a store name from six weeks ago would survive every reload on
 * that machine. A tab is the right lifetime for "which shop am I looking at".
 */
const CACHE_KEY = "opsgrids.storefront.buyer.store_context";

interface CachedEntry {
  /** The host the answer was fetched for. A different host is a miss, never a re-use. */
  host: string;
  context: StoreContextOut;
}

/** `true` for a value that has every key `StoreContextOut` requires, at the right type. */
function isStoreContext(value: unknown): value is StoreContextOut {
  if (typeof value !== "object" || value === null) return false;
  const candidate = value as Record<string, unknown>;
  return (
    typeof candidate.store === "string" &&
    typeof candidate.name === "string" &&
    (candidate.tagline === null || typeof candidate.tagline === "string") &&
    (candidate.logo_text === null || typeof candidate.logo_text === "string") &&
    typeof candidate.currency === "string" &&
    typeof candidate.timezone === "string" &&
    typeof candidate.canonical_host === "string" &&
    typeof candidate.is_canonical === "boolean" &&
    typeof candidate.checked_at === "string"
  );
}

/**
 * The cached answer for this host, or `null`.
 *
 * Every failure is a miss: Safari's private mode throws on `sessionStorage`, a half-written entry
 * will not parse, and a payload written by an older build may be short of a field the renderer now
 * reads (ADR-0012's erratum 4 is the same problem one layer down). A miss costs one request; a
 * malformed entry rendered as a store costs a broken shop front.
 */
export function readCachedStoreContext(host: string): StoreContextOut | null {
  try {
    const raw = window.sessionStorage.getItem(CACHE_KEY);
    if (raw === null) return null;
    const entry = JSON.parse(raw) as Partial<CachedEntry>;
    if (entry.host !== host) return null;
    return isStoreContext(entry.context) ? entry.context : null;
  } catch {
    return null;
  }
}

/** Remember the answer for the tab. **Failures are never cached** — only an answer the server
 *  computed. Storage being unavailable costs a request per page view and nothing else. */
function writeCachedStoreContext(host: string, context: StoreContextOut): void {
  try {
    window.sessionStorage.setItem(CACHE_KEY, JSON.stringify({ host, context } satisfies CachedEntry));
  } catch {
    /* Private mode, a full quota, storage disabled. The shop still renders. */
  }
}

/* --------------------------------- the request --------------------------------- */

/**
 * The request already in the air, if there is one.
 *
 * React 19's StrictMode mounts, unmounts and remounts every effect in development, and a remount
 * of any kind would otherwise put a second identical request on a 60/min-per-IP budget before the
 * first has had a chance to populate the cache. One entry is enough: there is exactly one host.
 */
let inFlight: { host: string; request: Promise<StoreContextOut> } | null = null;

/**
 * The hostname this bundle is being served from.
 *
 * `window.location.hostname` and nothing else — it excludes the port, which is exactly what the
 * server wants, and it is free and available before the first request.
 */
export function currentHost(): string {
  return window.location.hostname;
}

/**
 * Ask the server which shop this address is.
 *
 * Returns the cached answer when there is one for this host. Every failure throws a
 * `StoreContextError`, and **only an HTTP 404 carries `"not_found"`** — see the rule at the top
 * of this file.
 */
export function loadStoreContext(host: string): Promise<StoreContextOut> {
  const cached = readCachedStoreContext(host);
  if (cached !== null) return Promise.resolve(cached);

  if (inFlight !== null && inFlight.host === host) return inFlight.request;

  /* Cleared on settle either way, so a failure leaves nothing behind for a retry to join. */
  const request = requestStoreContext(host).finally(() => {
    if (inFlight?.request === request) inFlight = null;
  });
  inFlight = { host, request };
  return request;
}

async function requestStoreContext(host: string): Promise<StoreContextOut> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), STORE_CONTEXT_TIMEOUT_MS);

  let res: Response;
  try {
    res = await fetch(STORE_CONTEXT_PATH, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      /* `host` and nothing else. The schema is `additionalProperties: false`. */
      body: JSON.stringify({ host } satisfies PublicStoreContextIn),
      signal: controller.signal,
    });
  } catch {
    // A timeout and a dead network are the same fact to a buyer: we do not know yet.
    throw new StoreContextError("unavailable", 0, "Could not reach the shop directory.");
  } finally {
    clearTimeout(timer);
  }

  if (res.status === 404) {
    // The one status that means we looked. A draft store, a suspended domain, an unverified claim
    // and a host nobody has claimed are one sentence on the wire on purpose — anything finer is an
    // enumeration oracle — and they are one sentence on screen for the same reason.
    throw new StoreContextError("not_found", 404, "No published store at this address.");
  }

  if (!res.ok) {
    /* 422, 429, 5xx, and anything a proxy invents: we could not look. No `notifyUnauthorized` on
       any status, because this request carries no session and there is none to end. */
    throw new StoreContextError("unavailable", res.status, `Could not look up this address (${res.status}).`);
  }

  let body: unknown;
  try {
    body = await res.json();
  } catch {
    throw new StoreContextError("unavailable", res.status, "The shop directory sent a reply this page could not read.");
  }

  if (!isStoreContext(body)) {
    // A 200 whose body this build cannot interpret is "we could not look", never "no shop here".
    throw new StoreContextError("unavailable", res.status, "The shop directory sent a reply this page could not read.");
  }

  writeCachedStoreContext(host, body);
  return body;
}
