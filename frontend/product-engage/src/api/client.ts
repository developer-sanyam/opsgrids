/**
 * Typed Engage API client.
 *
 * Live by default: every function calls `services/notification`'s engage router through the
 * gateway, which mounts it at `/api/v1/engage` behind the `engage` entitlement
 * (`services/app-gateway/routes.json`). `API_BASE` already carries `/api/v1`, so the paths below
 * start at `/engage`. Set `VITE_USE_FIXTURES=true` to render the whole app from `fixtures.ts`
 * with no gateway and no login — for offline design work.
 *
 * **No function here sends or receives a credential except the one that must.** `connectProvider`
 * posts the app password once; nothing reads one back, because `ConnectionOut` has no field for
 * it. Do not add a call, a derived value or a cached copy that reconstructs a hint from anything
 * else — a masked secret is still a secret with a hint attached.
 */

import { apiRequest } from "@opsgrids/client";

import { CONNECTION, DOMAINS, SENDERS } from "./fixtures";
import type {
  ConnectionIn,
  ConnectionOut,
  DomainIn,
  DomainOut,
  DomainStatus,
  Page,
  SenderIn,
  SenderOut,
  TestSendIn,
} from "./types";

/** Default source. Live unless `VITE_USE_FIXTURES=true` pins the app to fixtures.
 *  Dev builds only: `import.meta.env.DEV` compiles to `false` in production, so a stray
 *  CI/production env var can never ship fixture mode — an app rendering fabricated data
 *  with the "Sample data" markers suppressed. */
export const USE_FIXTURES = import.meta.env.DEV && import.meta.env.VITE_USE_FIXTURES === "true";

/** True when the app is talking to the real gateway. */
export const LIVE = !USE_FIXTURES;

/** A generous page size — one request returns the whole (small) working set for this org. The
 *  service caps `size` at 200, so this is the largest single page it will serve. */
const FULL_PAGE = 200;

/** Small artificial delay so loading states are exercised in fixture mode. */
function settle<T>(value: T, ms = 240): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), ms));
}

/** Wrap fixture rows in the envelope `platform_core.pagination.Page` puts on the wire, so a
 *  screen reads the same shape either side of the flag. */
function fixturePage<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: FULL_PAGE, pages: items.length ? 1 : 0 };
}

/**
 * **Every write in this product is refused in fixture mode, and says which real thing it cannot
 * do.** Connecting seals a credential, adding a domain mints a DKIM keypair, verifying resolves
 * DNS, and a test send puts mail on the wire — none of which happens in a browser with no service
 * behind it. The alternative is worse than a refusal: a fabricated DKIM record is one the customer
 * could paste and could never verify, and a sender flipped to `usable` by the client is the gate
 * this product exists to enforce, opened by the side that does not get to decide it.
 *
 * Fixture mode is dev-only, so the audience for this sentence is an engineer and it names the flag.
 */
function refuseWithoutService(action: string): never {
  throw new Error(
    `${action} needs the notification service, and this app is running on sample data. ` +
      "Set VITE_USE_FIXTURES=false to run against the gateway.",
  );
}

/* ------------------------------- connection ------------------------------- */

/**
 * How this organization sends email — `GET /engage/connection`.
 *
 * A page of at most one, which is the service's shape rather than an accident: it keeps "is there
 * one yet?" in the selector, and makes the unconnected case an empty page every list screen
 * already handles instead of a null each caller special-cases. The envelope is preserved here for
 * the same reason.
 */
export async function getConnection(): Promise<Page<ConnectionOut>> {
  if (USE_FIXTURES) return settle(fixturePage([CONNECTION]));
  return apiRequest<Page<ConnectionOut>>("/engage/connection");
}

/**
 * Connect the organization's email provider — `POST /engage/connection`.
 *
 * POST rather than PUT because one organization gets one connection and a second is refused with
 * a 409; the verb matches what the service actually promises. `input.secret` leaves the browser
 * here and is never read back.
 */
export async function connectProvider(input: ConnectionIn): Promise<ConnectionOut> {
  if (USE_FIXTURES) refuseWithoutService("Connecting a provider");
  return apiRequest<ConnectionOut>("/engage/connection", { method: "POST", body: input });
}

/**
 * Replace the organization's email connection — `PUT /engage/connection`.
 *
 * PUT rather than POST, for the reverse of `connectProvider`'s reason: this replaces the one
 * connection a caller already knows exists. Same `ConnectionIn` body as connecting — the service
 * takes exactly the same four fields to replace a connection as it does to create one.
 *
 * The write resets every sender in the organization to unusable in the same transaction: a
 * `verified_at` is proof a test message reached the provider that was in place when it was
 * stamped, and that proof does not survive the provider changing under it. Nothing here decides
 * that or hides it from the caller — the screen that calls this tells the customer before it
 * fires, in the consequence's own words.
 */
export async function replaceProvider(input: ConnectionIn): Promise<ConnectionOut> {
  if (USE_FIXTURES) refuseWithoutService("Replacing the email connection");
  return apiRequest<ConnectionOut>("/engage/connection", { method: "PUT", body: input });
}

/* --------------------------------- domains -------------------------------- */

/**
 * Sending domains and the state of their DNS — `GET /engage/domains`.
 *
 * `status` is the server's filter, spelled as the query parameter the route aliases it to. It is
 * sent as the customer's own filter rather than applied here so the `total` in the envelope counts
 * the same rows the page holds.
 */
export async function listDomains(options: { status?: DomainStatus } = {}): Promise<Page<DomainOut>> {
  const { status } = options;
  if (USE_FIXTURES) {
    const rows = status === undefined ? DOMAINS : DOMAINS.filter((d) => d.status === status);
    return settle(fixturePage(rows));
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (status !== undefined) query.set("status", status);
  return apiRequest<Page<DomainOut>>(`/engage/domains?${query.toString()}`);
}

/**
 * Add a sending domain and generate its DNS records — `POST /engage/domains`.
 *
 * The record set comes back on the response and on every later read, so the screen shows what to
 * paste without asking for it again — and shows the same values it showed yesterday.
 */
export async function addDomain(input: DomainIn): Promise<DomainOut> {
  if (USE_FIXTURES) refuseWithoutService("Adding a sending domain");
  return apiRequest<DomainOut>("/engage/domains", { method: "POST", body: input });
}

/**
 * Re-read DNS and fold the answer into the domain's status —
 * `POST /engage/domains/{domain_id}/verify`.
 *
 * The returned domain replaces the one the caller held: both `records` and `status` are rewritten
 * by the check, and merging the two would let a stale `status` outlive the records it was folded
 * from.
 */
export async function verifyDomain(domainId: string): Promise<DomainOut> {
  if (USE_FIXTURES) refuseWithoutService("Verifying a domain");
  return apiRequest<DomainOut>(`/engage/domains/${encodeURIComponent(domainId)}/verify`, {
    method: "POST",
  });
}

/* --------------------------------- senders -------------------------------- */

/** The named identities mail can go out as — `GET /engage/senders`, optionally one domain's. */
export async function listSenders(options: { domainId?: string } = {}): Promise<Page<SenderOut>> {
  const { domainId } = options;
  if (USE_FIXTURES) {
    const rows = domainId === undefined ? SENDERS : SENDERS.filter((s) => s.domain_id === domainId);
    return settle(fixturePage(rows));
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (domainId !== undefined) query.set("domain_id", domainId);
  return apiRequest<Page<SenderOut>>(`/engage/senders?${query.toString()}`);
}

/**
 * Add a sender on a verified domain — `POST /engage/senders`.
 *
 * The service refuses an address that does not belong to a domain this organization has proven,
 * and refuses a domain that is not verified yet. Neither check is mirrored here: whether an
 * address belongs to a proven domain is a fact about rows, not about the string.
 */
export async function addSender(input: SenderIn): Promise<SenderOut> {
  if (USE_FIXTURES) refuseWithoutService("Adding a sender");
  return apiRequest<SenderOut>("/engage/senders", { method: "POST", body: input });
}

/**
 * Send a test message and, if it arrives, make the sender usable —
 * `POST /engage/senders/{sender_id}/test`.
 *
 * The caller waits for the provider's answer because that answer *is* the state change: the
 * returned sender carries the new `verified_at` and the `usable` the service derived from it.
 * Nothing on this side decides either — a client that set `usable` itself would be opening the
 * gate from the side that cannot see whether mail left.
 */
export async function sendTestMessage(senderId: string, input: TestSendIn): Promise<SenderOut> {
  if (USE_FIXTURES) refuseWithoutService("Sending a test message");
  return apiRequest<SenderOut>(`/engage/senders/${encodeURIComponent(senderId)}/test`, {
    method: "POST",
    body: input,
  });
}
