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

import {
  CAMPAIGNS,
  CAMPAIGN_RECIPIENTS,
  CONNECTION,
  CONTACTS,
  CONTACT_LISTS,
  CONVERSATION_MESSAGES,
  CONVERSATIONS,
  DOMAINS,
  SENDERS,
  SUPPRESSIONS,
  WHATSAPP_CONNECTION,
  WHATSAPP_TEMPLATES,
} from "./fixtures";
import type {
  CampaignIn,
  CampaignOut,
  CampaignPatch,
  CampaignRecipientOut,
  CampaignStatus,
  ConnectionIn,
  ConnectionOut,
  ContactImportOut,
  ContactImportRow,
  ContactIn,
  ContactListIn,
  ContactListOut,
  ContactListPatch,
  ContactOut,
  ContactPatch,
  ConversationAssignIn,
  ConversationMessageOut,
  ConversationOut,
  ConversationReplyIn,
  ConversationReplyOut,
  ConversationStatus,
  DomainIn,
  DomainOut,
  DomainStatus,
  ListMembersIn,
  ListMembersOut,
  Page,
  ProviderTemplateIn,
  ProviderTemplateOut,
  RecipientStatus,
  SenderIn,
  SenderOut,
  SuppressionIn,
  SuppressionOut,
  SuppressionReason,
  TestSendIn,
  WhatsAppConnectionIn,
  WhatsAppConnectionOut,
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

/* ----------------------------- whatsapp connection ----------------------------- */

/**
 * How this organization sends WhatsApp — `GET /engage/whatsapp/connection`.
 *
 * A page of at most one, the same shape `getConnection` uses and for the same reason: "is there
 * one yet?" stays in the selector, and the unconnected case is an empty page this screen already
 * handles rather than a null it has to special-case.
 */
export async function getWhatsAppConnection(): Promise<Page<WhatsAppConnectionOut>> {
  if (USE_FIXTURES) return settle(fixturePage([WHATSAPP_CONNECTION]));
  return apiRequest<Page<WhatsAppConnectionOut>>("/engage/whatsapp/connection");
}

/**
 * Connect the organization's WhatsApp Business account — `POST /engage/whatsapp/connection`.
 *
 * One account per organization; a second POST is refused. `input.access_token` leaves the
 * browser here and is never read back — `WhatsAppConnectionOut` has no field for it.
 */
export async function connectWhatsApp(
  input: WhatsAppConnectionIn,
): Promise<WhatsAppConnectionOut> {
  if (USE_FIXTURES) refuseWithoutService("Connecting a WhatsApp account");
  return apiRequest<WhatsAppConnectionOut>("/engage/whatsapp/connection", {
    method: "POST",
    body: input,
  });
}

/**
 * Replace the organization's WhatsApp Business account — `PUT /engage/whatsapp/connection`.
 *
 * Same body as connecting, for the reason `replaceProvider` gives: replacing takes exactly what
 * creating does.
 */
export async function replaceWhatsApp(
  input: WhatsAppConnectionIn,
): Promise<WhatsAppConnectionOut> {
  if (USE_FIXTURES) refuseWithoutService("Replacing the WhatsApp account");
  return apiRequest<WhatsAppConnectionOut>("/engage/whatsapp/connection", {
    method: "PUT",
    body: input,
  });
}

/* ------------------------------- provider templates ------------------------------ */

/**
 * WhatsApp templates and their real approval status — `GET /notifications/provider-templates`.
 *
 * **Mounted under `/api/v1/notifications`, not `/api/v1/engage`** — the gateway gates this
 * prefix on the `notification` product entitlement (`services/app-gateway/routes.json`), not
 * `engage`, because `ProviderTemplate` is a `notification`-catalogue resource the engage router
 * only reads. An organization that has bought Engage but not the transactional-notification
 * product will see this call answer `402` even though Engage itself is active — a real product-
 * boundary gap, not a bug in this screen. `TemplatesPage` names that plainly rather than
 * reusing `EngageNotActive`'s wording, which would say the wrong product is missing.
 */
export async function listWhatsAppTemplates(): Promise<Page<ProviderTemplateOut>> {
  if (USE_FIXTURES) return settle(fixturePage(WHATSAPP_TEMPLATES));
  const query = new URLSearchParams({ channel: "whatsapp", size: String(FULL_PAGE) });
  return apiRequest<Page<ProviderTemplateOut>>(`/notifications/provider-templates?${query.toString()}`);
}

/**
 * Register or update a WhatsApp template — `PUT /notifications/provider-templates`.
 *
 * This submits what the organization intends to send; it does not and cannot approve it. The
 * service defaults `status` to `draft`, and only a sync with Meta ever moves it to `pending`,
 * `approved` or `rejected` — nothing this app sends can set it, and nothing here claims to.
 */
export async function submitWhatsAppTemplate(
  input: ProviderTemplateIn,
): Promise<ProviderTemplateOut> {
  if (USE_FIXTURES) refuseWithoutService("Submitting a template");
  return apiRequest<ProviderTemplateOut>("/notifications/provider-templates", {
    method: "PUT",
    body: input,
  });
}

/* --------------------------------- contacts -------------------------------- */

/**
 * The organization's contacts, addresses masked — `GET /engage/contacts`, server-paged.
 *
 * `search` is the service's own filter, spelled as the query parameter the route aliases it to:
 * a name substring, or an exact email address. Partial email search is not offered anywhere in
 * this screen because it cannot be — addresses are sealed at rest, and `email_masked` is the only
 * form of one this app ever holds.
 */
export async function listContacts(
  options: { search?: string; page?: number; size?: number } = {},
): Promise<Page<ContactOut>> {
  const { search, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const q = (search ?? "").trim().toLowerCase();
    const rows =
      q === ""
        ? CONTACTS
        : CONTACTS.filter(
            (c) => c.name.toLowerCase().includes(q) || c.email.toLowerCase() === q,
          );
    return settle({
      items: rows,
      total: rows.length,
      page: 1,
      size: rows.length || size,
      pages: rows.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (search !== undefined && search.trim() !== "") query.set("search", search.trim());
  return apiRequest<Page<ContactOut>>(`/engage/contacts?${query.toString()}`);
}

/** Add a contact — `POST /engage/contacts`. */
export async function addContact(input: ContactIn): Promise<ContactOut> {
  if (USE_FIXTURES) refuseWithoutService("Adding a contact");
  return apiRequest<ContactOut>("/engage/contacts", { method: "POST", body: input });
}

/** Edit a contact — `PATCH /engage/contacts/{contact_id}`. */
export async function editContact(contactId: string, input: ContactPatch): Promise<ContactOut> {
  if (USE_FIXTURES) refuseWithoutService("Editing a contact");
  return apiRequest<ContactOut>(`/engage/contacts/${encodeURIComponent(contactId)}`, {
    method: "PATCH",
    body: input,
  });
}

/** Delete a contact — `DELETE /engage/contacts/{contact_id}`. Any suppression of the address
 *  stays: deleting the contact is not the same fact as the address being safe to mail again. */
export async function deleteContact(contactId: string): Promise<void> {
  if (USE_FIXTURES) refuseWithoutService("Deleting a contact");
  await apiRequest<void>(`/engage/contacts/${encodeURIComponent(contactId)}`, {
    method: "DELETE",
  });
}

/**
 * Upsert up to `CONTACT_IMPORT_MAX` contacts by address, reporting each row —
 * `POST /engage/contacts/import`.
 *
 * Answers `200` even when some rows are invalid: the per-row `status` in `ContactImportOut` is
 * the answer, and one bad address in a pasted spreadsheet does not throw away the other 999.
 */
export async function importContacts(rows: ContactImportRow[]): Promise<ContactImportOut> {
  if (USE_FIXTURES) refuseWithoutService("Importing contacts");
  return apiRequest<ContactImportOut>("/engage/contacts/import", {
    method: "POST",
    body: rows,
  });
}

/* -------------------------------- suppressions ------------------------------- */

/** Addresses that may not be mailed, and why — `GET /engage/suppressions`, server-paged. */
export async function listSuppressions(
  options: { reason?: SuppressionReason; page?: number; size?: number } = {},
): Promise<Page<SuppressionOut>> {
  const { reason, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const rows = reason === undefined ? SUPPRESSIONS : SUPPRESSIONS.filter((s) => s.reason === reason);
    return settle({
      items: rows,
      total: rows.length,
      page: 1,
      size: rows.length || size,
      pages: rows.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (reason !== undefined) query.set("reason", reason);
  return apiRequest<Page<SuppressionOut>>(`/engage/suppressions?${query.toString()}`);
}

/** Suppress an address by hand — `POST /engage/suppressions`. Always lands as `reason: "manual"`;
 *  the other reasons are reported by the recipient or their mailbox provider. */
export async function addSuppression(input: SuppressionIn): Promise<SuppressionOut> {
  if (USE_FIXTURES) refuseWithoutService("Suppressing an address");
  return apiRequest<SuppressionOut>("/engage/suppressions", { method: "POST", body: input });
}

/** Lift a manual suppression — `DELETE /engage/suppressions/{suppression_id}`. The service
 *  refuses this for any reason but `manual`, so the screen never offers it for the rest. */
export async function removeSuppression(suppressionId: string): Promise<void> {
  if (USE_FIXTURES) refuseWithoutService("Lifting a suppression");
  await apiRequest<void>(`/engage/suppressions/${encodeURIComponent(suppressionId)}`, {
    method: "DELETE",
  });
}

/* ------------------------------------ contact lists ------------------------------------ */

/** Contact lists, by name — `GET /engage/lists`, server-paged. */
export async function listLists(
  options: { page?: number; size?: number } = {},
): Promise<Page<ContactListOut>> {
  const { page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    return settle({
      items: CONTACT_LISTS,
      total: CONTACT_LISTS.length,
      page: 1,
      size: CONTACT_LISTS.length || size,
      pages: CONTACT_LISTS.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  return apiRequest<Page<ContactListOut>>(`/engage/lists?${query.toString()}`);
}

/** Create a contact list — `POST /engage/lists`. */
export async function createList(input: ContactListIn): Promise<ContactListOut> {
  if (USE_FIXTURES) refuseWithoutService("Creating a list");
  return apiRequest<ContactListOut>("/engage/lists", { method: "POST", body: input });
}

/** One contact list — `GET /engage/lists/{list_id}`. */
export async function getList(listId: string): Promise<ContactListOut> {
  if (USE_FIXTURES) {
    const found = CONTACT_LISTS.find((l) => l.id === listId);
    if (!found) throw new Error(`No such fixture list: ${listId}`);
    return settle(found);
  }
  return apiRequest<ContactListOut>(`/engage/lists/${encodeURIComponent(listId)}`);
}

/** Rename or describe a contact list — `PATCH /engage/lists/{list_id}`. */
export async function editList(listId: string, input: ContactListPatch): Promise<ContactListOut> {
  if (USE_FIXTURES) refuseWithoutService("Editing a list");
  return apiRequest<ContactListOut>(`/engage/lists/${encodeURIComponent(listId)}`, {
    method: "PATCH",
    body: input,
  });
}

/** Delete a contact list — `DELETE /engage/lists/{list_id}`. Its contacts stay; only the list and
 *  its memberships go. */
export async function deleteList(listId: string): Promise<void> {
  if (USE_FIXTURES) refuseWithoutService("Deleting a list");
  await apiRequest<void>(`/engage/lists/${encodeURIComponent(listId)}`, { method: "DELETE" });
}

/** The contacts on a list, addresses masked — `GET /engage/lists/{list_id}/members`,
 *  server-paged. */
export async function listListMembers(
  listId: string,
  options: { page?: number; size?: number } = {},
): Promise<Page<ContactOut>> {
  const { page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const rows = CONTACTS; // Every fixture contact is shown as a member of every fixture list.
    return settle({
      items: rows,
      total: rows.length,
      page: 1,
      size: rows.length || size,
      pages: rows.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  return apiRequest<Page<ContactOut>>(
    `/engage/lists/${encodeURIComponent(listId)}/members?${query.toString()}`,
  );
}

/** Add contacts to a list (idempotent) — `POST /engage/lists/{list_id}/members`. */
export async function addListMembers(
  listId: string,
  input: ListMembersIn,
): Promise<ListMembersOut> {
  if (USE_FIXTURES) refuseWithoutService("Adding list members");
  return apiRequest<ListMembersOut>(`/engage/lists/${encodeURIComponent(listId)}/members`, {
    method: "POST",
    body: input,
  });
}

/** Remove contacts from a list (idempotent) — `POST /engage/lists/{list_id}/members/remove`.
 *  Not a `DELETE` with a body: removing many members in one call is a batch action on the list,
 *  the same shape as adding them, rather than a delete of any one resource. */
export async function removeListMembers(
  listId: string,
  input: ListMembersIn,
): Promise<ListMembersOut> {
  if (USE_FIXTURES) refuseWithoutService("Removing list members");
  return apiRequest<ListMembersOut>(
    `/engage/lists/${encodeURIComponent(listId)}/members/remove`,
    { method: "POST", body: input },
  );
}

/* -------------------------------------- campaigns -------------------------------------- */

/** Campaigns, newest first — `GET /engage/campaigns`, server-paged. */
export async function listCampaigns(
  options: { status?: CampaignStatus; page?: number; size?: number } = {},
): Promise<Page<CampaignOut>> {
  const { status, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const rows = status === undefined ? CAMPAIGNS : CAMPAIGNS.filter((c) => c.status === status);
    return settle({
      items: rows,
      total: rows.length,
      page: 1,
      size: rows.length || size,
      pages: rows.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (status !== undefined) query.set("status", status);
  return apiRequest<Page<CampaignOut>>(`/engage/campaigns?${query.toString()}`);
}

/** Draft a campaign — `POST /engage/campaigns`. Nothing is queued to send: a draft is edited and
 *  sent by separate, later calls. */
export async function createCampaign(input: CampaignIn): Promise<CampaignOut> {
  if (USE_FIXTURES) refuseWithoutService("Drafting a campaign");
  return apiRequest<CampaignOut>("/engage/campaigns", { method: "POST", body: input });
}

/** One campaign, with its delivery stats — `GET /engage/campaigns/{campaign_id}`. */
export async function getCampaign(campaignId: string): Promise<CampaignOut> {
  if (USE_FIXTURES) {
    const found = CAMPAIGNS.find((c) => c.id === campaignId);
    if (!found) throw new Error(`No such fixture campaign: ${campaignId}`);
    return settle(found);
  }
  return apiRequest<CampaignOut>(`/engage/campaigns/${encodeURIComponent(campaignId)}`);
}

/** Edit a draft campaign — `PATCH /engage/campaigns/{campaign_id}`. The service refuses this
 *  once the campaign has left `draft`; nothing here checks that first, the same as every other
 *  write in this client — the writer's own refusal names the campaign's actual status. */
export async function editCampaign(campaignId: string, input: CampaignPatch): Promise<CampaignOut> {
  if (USE_FIXTURES) refuseWithoutService("Editing a campaign");
  return apiRequest<CampaignOut>(`/engage/campaigns/${encodeURIComponent(campaignId)}`, {
    method: "PATCH",
    body: input,
  });
}

/** Delete a draft campaign — `DELETE /engage/campaigns/{campaign_id}`. */
export async function deleteCampaign(campaignId: string): Promise<void> {
  if (USE_FIXTURES) refuseWithoutService("Deleting a campaign");
  await apiRequest<void>(`/engage/campaigns/${encodeURIComponent(campaignId)}`, {
    method: "DELETE",
  });
}

/**
 * Send a draft campaign to its audience — `POST /engage/campaigns/{campaign_id}/send`.
 *
 * The service is the gate: a `409` means the campaign is not a draft, a `422` names exactly
 * which precondition failed (no subject, an unusable sender, an unverified domain, no connected
 * provider, a deleted list, unsubscribe links not configured) in a sentence meant to be shown
 * verbatim — see `services.request_send`'s docstring. Nothing here re-checks any of that first.
 */
export async function sendCampaign(campaignId: string): Promise<CampaignOut> {
  if (USE_FIXTURES) refuseWithoutService("Sending a campaign");
  return apiRequest<CampaignOut>(`/engage/campaigns/${encodeURIComponent(campaignId)}/send`, {
    method: "POST",
  });
}

/** Cancel a campaign; recipients not yet sent are never sent —
 *  `POST /engage/campaigns/{campaign_id}/cancel`. Refused with `409` once the campaign is `sent`
 *  or already `cancelled`. */
export async function cancelCampaign(campaignId: string): Promise<CampaignOut> {
  if (USE_FIXTURES) refuseWithoutService("Cancelling a campaign");
  return apiRequest<CampaignOut>(`/engage/campaigns/${encodeURIComponent(campaignId)}/cancel`, {
    method: "POST",
  });
}

/** Retry a paused campaign now, ignoring the driver's own backoff —
 *  `POST /engage/campaigns/{campaign_id}/resume`. Refused with `409` unless the campaign is
 *  currently `paused`. */
export async function resumeCampaign(campaignId: string): Promise<CampaignOut> {
  if (USE_FIXTURES) refuseWithoutService("Resuming a campaign");
  return apiRequest<CampaignOut>(`/engage/campaigns/${encodeURIComponent(campaignId)}/resume`, {
    method: "POST",
  });
}

/** A campaign's recipients and what happened to each, addresses masked —
 *  `GET /engage/campaigns/{campaign_id}/recipients`, server-paged. */
export async function listCampaignRecipients(
  campaignId: string,
  options: { status?: RecipientStatus; page?: number; size?: number } = {},
): Promise<Page<CampaignRecipientOut>> {
  const { status, page = 1, size = 25 } = options;
  if (USE_FIXTURES) {
    const rows = CAMPAIGN_RECIPIENTS[campaignId] ?? [];
    const filtered = status === undefined ? rows : rows.filter((r) => r.status === status);
    return settle({
      items: filtered,
      total: filtered.length,
      page: 1,
      size: filtered.length || size,
      pages: filtered.length ? 1 : 0,
    });
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  if (status !== undefined) query.set("status", status);
  return apiRequest<Page<CampaignRecipientOut>>(
    `/engage/campaigns/${encodeURIComponent(campaignId)}/recipients?${query.toString()}`,
  );
}

/* ----------------------------------- inbox (WhatsApp) ----------------------------------- */

/** WhatsApp conversations, most recently active first — `GET /engage/conversations`. */
export async function listConversations(
  options: { status?: ConversationStatus } = {},
): Promise<Page<ConversationOut>> {
  const { status } = options;
  if (USE_FIXTURES) {
    const rows =
      status === undefined ? CONVERSATIONS : CONVERSATIONS.filter((c) => c.status === status);
    return settle(fixturePage(rows));
  }
  const query = new URLSearchParams({ size: String(FULL_PAGE) });
  if (status !== undefined) query.set("status", status);
  return apiRequest<Page<ConversationOut>>(`/engage/conversations?${query.toString()}`);
}

/** One conversation — `GET /engage/conversations/{id}`. */
export async function getConversation(conversationId: string): Promise<ConversationOut> {
  if (USE_FIXTURES) {
    const found = CONVERSATIONS.find((c) => c.id === conversationId);
    if (!found) throw new Error(`No such fixture conversation: ${conversationId}`);
    return settle(found);
  }
  return apiRequest<ConversationOut>(
    `/engage/conversations/${encodeURIComponent(conversationId)}`,
  );
}

/** A conversation's thread, both directions, oldest first — `GET .../messages`, server-paged. */
export async function listConversationMessages(
  conversationId: string,
  options: { page?: number; size?: number } = {},
): Promise<Page<ConversationMessageOut>> {
  const { page = 1, size = FULL_PAGE } = options;
  if (USE_FIXTURES) {
    return settle(fixturePage(CONVERSATION_MESSAGES[conversationId] ?? []));
  }
  const query = new URLSearchParams({ page: String(page), size: String(size) });
  return apiRequest<Page<ConversationMessageOut>>(
    `/engage/conversations/${encodeURIComponent(conversationId)}/messages?${query.toString()}`,
  );
}

/**
 * Send a free-text reply inside the 24-hour service window — `POST .../reply`.
 *
 * Refused by the service with a `409` outside the window, carrying `remedy: "template"` in the
 * problem body: `replyRemedy` in `lib/errors.ts` is how the composer reads it.
 */
export async function replyToConversation(
  conversationId: string,
  input: ConversationReplyIn,
): Promise<ConversationReplyOut> {
  if (USE_FIXTURES) refuseWithoutService("Replying to a conversation");
  return apiRequest<ConversationReplyOut>(
    `/engage/conversations/${encodeURIComponent(conversationId)}/reply`,
    { method: "POST", body: input },
  );
}

/** Assign a conversation to a membership, or clear its assignment with `null` —
 *  `POST .../{id}:assign`. */
export async function assignConversation(
  conversationId: string,
  input: ConversationAssignIn,
): Promise<ConversationOut> {
  if (USE_FIXTURES) refuseWithoutService("Assigning a conversation");
  return apiRequest<ConversationOut>(
    `/engage/conversations/${encodeURIComponent(conversationId)}:assign`,
    { method: "POST", body: input },
  );
}

/** Close a conversation. A later reply, or the customer writing back, reopens it —
 *  `POST .../{id}:close`. */
export async function closeConversation(conversationId: string): Promise<ConversationOut> {
  if (USE_FIXTURES) refuseWithoutService("Closing a conversation");
  return apiRequest<ConversationOut>(
    `/engage/conversations/${encodeURIComponent(conversationId)}:close`,
    { method: "POST" },
  );
}
