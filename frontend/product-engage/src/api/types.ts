/**
 * Wire types for the Engage product, transcribed field for field from
 * `services/notification/app/schemas.py` — the engage section at the bottom of that module is the
 * published contract, and these names are its names. Nothing here is inferred from a screen's
 * needs; a field the service does not send has no declaration, and a codegen run against the
 * OpenAPI document should collapse this file into an import rather than a redesign.
 *
 * **There is no secret in any `Out` shape, and there must never be one.** `ConnectionOut` omits
 * the app password deliberately — not masked, not truncated, not counted — and the DKIM private
 * half has no field anywhere in the service. A type that declared one would be an invitation for
 * a screen to render it.
 */

/** `platform` is in the wire enum and refused by the writer, so an API caller gets a sentence
 *  about the MTA that does not exist rather than an enum error listing three values and
 *  explaining none. The wizard does not offer it: a choice whose every outcome is that refusal
 *  is a dead end with a label on it. */
export type ConnectionKind = "google" | "microsoft" | "smtp" | "platform";
export type ConnectionStatus = "connected" | "disconnected";
export type DomainStatus = "pending" | "verified" | "failed";
/** No `return_path` or `tracking` any more — OpsGrids publishes no mail infrastructure of its
 *  own: no bounce envelope, no link rewriting. See `app/domain.py`'s sending-identity section. */
export type DnsRecordKind = "spf" | "dkim" | "dmarc";
/** Whether DNS satisfies one record. `multiple` is SPF-specific (a domain may publish only one);
 *  `mismatch` is something published at the right name that does not satisfy the requirement —
 *  distinct from `missing` (nothing published at all) because a customer who pasted the wrong
 *  value needs to know a value arrived. */
export type DnsRecordStatus = "verified" | "missing" | "multiple" | "mismatch";

/** `POST /engage/connection`. One connection per organization; a second is refused. */
export interface ConnectionIn {
  kind: ConnectionKind;
  username: string;
  /** Write-only. Sealed on the way in, with no counterpart on the way out. */
  secret: string;
  /** Required for `smtp`, which has no submission host implied by its kind. Sent for a named
   *  provider it overrides the derived value — how a relay in front of Workspace is supported
   *  without inventing a fifth kind. */
  host?: string;
  port?: number | null;
}

/** How this organization sends. Every field the service returns, and nothing else. */
export interface ConnectionOut {
  kind: ConnectionKind;
  username: string;
  host: string;
  port: number;
  status: ConnectionStatus;
  /** What the provider accepts in a day. Shown because a campaign larger than it is refused. */
  daily_cap: number;
}

/** `POST /engage/domains`. Normalised server-side by `dns_check.normalize_domain_name` — a
 *  pasted URL, a trailing dot or slash, and mixed case are all accepted and canonicalised. */
export interface DomainIn {
  domain: string;
}

/** `PATCH /engage/domains/{id}`. An absent field is left alone; a change to either resets the
 *  domain to `pending` — its DNS has to be re-checked against the new value. */
export interface DomainPatch {
  dkim_selector?: string;
  spf_include?: string;
}

/**
 * One record the customer publishes at their registrar, and whether DNS has it yet.
 *
 * `fqdn` is the full host DNS is actually asked about; `registrar_name` is the same record named
 * the way a registrar's panel wants it — `@` for the apex, `google._domainkey` for a subdomain.
 * Neither the exact DKIM value nor a Microsoft CNAME's target is ever known here — the provider
 * mints the key, not this platform — so `value` for those is instructional text, and only SPF
 * ever carries a `suggested_value`.
 */
export interface DnsRecordOut {
  kind: DnsRecordKind;
  /** The RR type as it is typed at a registrar — `TXT`, `CNAME`. */
  type: string;
  fqdn: string;
  registrar_name: string;
  value: string;
  suggested_value: string | null;
  /** DMARC is the one that is recommended rather than required: a domain whose security team has
   *  not settled a policy yet still sends. */
  required: boolean;
  status: DnsRecordStatus;
  instruction: string;
}

export interface DomainOut {
  id: string;
  domain: string;
  status: DomainStatus;
  /** `google`, `microsoft`, `smtp`, or null when no provider is connected yet — in which case
   *  `records` is empty and the screen's job is to say "connect your email provider first". */
  connection_kind: ConnectionKind | null;
  dkim_selector: string;
  spf_include: string | null;
  /** The records this domain needs *right now*, given its connection — regenerated on every
   *  read, not a snapshot frozen at creation. */
  records: DnsRecordOut[];
  last_checked_at: string | null;
  created_at: string;
}

/** `POST /engage/senders`. The address is checked against the domain by the writer, because
 *  whether it belongs to a proven domain is a fact about rows, not about the string. */
export interface SenderIn {
  domain_id: string;
  name: string;
  address: string;
  reply_to?: string;
  signature?: string;
  department?: string;
}

export interface SenderOut {
  id: string;
  domain_id: string;
  name: string;
  address: string;
  reply_to: string;
  signature: string;
  department: string;
  /** Derived from `verified_at` by the service, so it cannot disagree with the one fact that
   *  decides it: a test message actually reached the provider. */
  usable: boolean;
  verified_at: string | null;
  created_at: string;
}

/** `POST /engage/senders/{sender_id}/test`. Subject and body are fixed by the service — a
 *  customisable test message is a send nobody reviewed, to an address of the caller's choosing. */
export interface TestSendIn {
  to_address: string;
}

/* ----------------------------- whatsapp connection ----------------------------- */

/** `POST /engage/whatsapp/connection`. One account per organization; a second is refused. */
export interface WhatsAppConnectionIn {
  waba_id: string;
  phone_number_id: string;
  display_number?: string;
  /** Write-only. Sealed on the way in, with no counterpart on the way out. */
  access_token: string;
}

/** How this organization sends WhatsApp. Every field the service returns, and nothing else —
 *  no field here is derived from the access token, not masked, not a length. */
export interface WhatsAppConnectionOut {
  waba_id: string;
  phone_number_id: string;
  display_number: string;
  status: ConnectionStatus;
}

/* ------------------------------- whatsapp templates ------------------------------ */
//
// `services/notification`'s `ProviderTemplate`, transcribed from `schemas.py` in shape only.
// Read and written through Engage's own routes — `/api/v1/engage/whatsapp/templates`, gated on
// `engage.whatsapp.template.read`/`.write` (`services/app-gateway/routes.json`) — rather than
// `notification`'s `/api/v1/notifications/provider-templates`, which the same rows are also
// reachable through under that product's own permission. See `client.ts`.

export type TemplateCategory = "utility" | "marketing" | "authentication";
export type ParameterFormat = "positional" | "named";
/** The provider's own words. Never fabricated on this side — `approved` here means the service
 *  read it from a sync, not that a screen decided to say so. */
export type TemplateStatus = "draft" | "pending" | "approved" | "rejected" | "paused" | "disabled";
export type VariableComponent = "body" | "header" | "button";
export type VariableKind = "text" | "payload";

export interface TemplateVariableIn {
  key: string;
  component?: VariableComponent;
  position?: number;
  button_index?: number;
  kind?: VariableKind;
  parameter_name?: string;
}

export type TemplateVariableOut = Required<TemplateVariableIn>;

/** `PUT /engage/whatsapp/templates`. */
export interface ProviderTemplateIn {
  code: string;
  channel: "whatsapp" | "email";
  provider_name: string;
  language?: string;
  category?: TemplateCategory;
  parameter_format?: ParameterFormat;
  variables?: TemplateVariableIn[];
  preview_text?: string;
}

/** `GET /engage/whatsapp/templates`. `status` and `sendable` are the provider's own
 *  answer — `sendable` is derived by the service from `status` so it can never disagree with
 *  it, and neither is ever set by this app. */
export interface ProviderTemplateOut {
  id: string;
  channel: "whatsapp" | "email";
  provider_name: string;
  provider_template_id: string | null;
  language: string;
  category: TemplateCategory;
  parameter_format: ParameterFormat;
  status: TemplateStatus;
  status_reason: string | null;
  status_checked_at: string | null;
  variables: TemplateVariableOut[];
  preview_text: string;
  sendable: boolean;
  created_at: string;
  updated_at: string;
}

/* --------------------------------------- contacts --------------------------------------- */

/** `POST /engage/contacts`. */
export interface ContactIn {
  email: string;
  name?: string;
  attributes?: Record<string, unknown>;
}

/** `PATCH /engage/contacts/{contact_id}`. An absent field is left alone. */
export interface ContactPatch {
  email?: string;
  name?: string;
  attributes?: Record<string, unknown>;
}

/** A contact as the service returns it. `email` is always the mask — `email_enc` has no field
 *  here at all, and no route on this service ever opens it. */
export interface ContactOut {
  id: string;
  email: string;
  name: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** One pasted row, parsed client-side. Deliberately loose, same as the service's own
 *  `ContactImportRow` — a bad row is reported back as `invalid` rather than failing the other
 *  999, so shape checks happen server-side, per row. */
export interface ContactImportRow {
  email: string;
  name?: string;
  attributes?: Record<string, unknown>;
}

/** The most rows one `POST /engage/contacts/import` call may carry — `CONTACT_IMPORT_MAX`. */
export const CONTACT_IMPORT_MAX = 1000;

export interface ContactImportResult {
  index: number;
  status: "created" | "updated" | "invalid";
  id: string | null;
  error: string | null;
}

export interface ContactImportOut {
  created: number;
  updated: number;
  invalid: number;
  results: ContactImportResult[];
}

/* ------------------------------------- suppressions -------------------------------------- */

/** Why an address may not be mailed. Only `manual` is written or lifted by a person through the
 *  API; the others are reported by the recipient or their mailbox provider and are never sent by
 *  this app. */
export type SuppressionReason = "unsubscribed" | "bounced" | "complained" | "manual";

/** `POST /engage/suppressions`. The reason is not a field: a person adding one through the API
 *  is by definition `manual`, so there is nothing here to claim otherwise with. */
export interface SuppressionIn {
  email: string;
}

export interface SuppressionOut {
  id: string;
  email: string;
  reason: SuppressionReason;
  created_at: string;
}

/**
 * `platform_core.pagination.Page` — the envelope every list route on this service returns. All
 * five fields are present on the wire; `pages` is `0` for an empty result rather than `1`.
 */
export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
  pages: number;
}

/* ----------------------------------------- inbox ------------------------------------------ */

export type ConversationStatus = "open" | "closed";
export type MessageDirection = "inbound" | "outbound";

/**
 * One WhatsApp conversation. `wa_address` is always the mask — sealed at rest, and no route on
 * this service ever opens it here; the real number is reachable only through `contact_id`, and
 * only from the CRM's own audited unmask. `within_service_window` is computed by the service at
 * read time from `last_customer_message_at`, so this screen never reimplements the 24-hour rule.
 */
export interface ConversationOut {
  id: string;
  channel: "whatsapp";
  wa_address: string;
  /** A `customer.contacts` id, once `app/consumers.py`'s resolver has matched the number. `null`
   *  for a stranger, or before resolution has run — an ordinary state, not a fault. */
  contact_id: string | null;
  last_customer_message_at: string | null;
  within_service_window: boolean;
  status: ConversationStatus;
  assignee_membership_id: string | null;
  created_at: string;
  updated_at: string;
}

/**
 * One entry in a conversation's thread. `body` is masked for an inbound message (the customer's
 * own words) and shown in full for an outbound one (this organization's own reply) — see
 * `selectors.thread_stmt`'s docstring for why only one side is PII-gated. Revealing an inbound
 * message's real text needs its own audited, stepped-up permission this app does not offer a
 * route to yet.
 */
export interface ConversationMessageOut {
  id: string;
  direction: MessageDirection;
  message_type: string;
  body: string;
  occurred_at: string;
  provider_message_id: string | null;
  profile_name: string | null;
}

/** `POST /engage/conversations/{id}/reply`. `idempotency_key` makes a retried click a no-op
 *  rather than a second message on the wire. */
export interface ConversationReplyIn {
  body: string;
  idempotency_key: string;
}

/** pending | sending | sent | delivered | read | failed | suppressed. Only `pending` and
 *  `suppressed` are possible immediately after a reply — the dispatcher owns every transition
 *  after that. `suppressed` is how a customer's earlier STOP reaches a reply already in flight. */
export type MessageState =
  | "pending"
  | "sending"
  | "sent"
  | "delivered"
  | "read"
  | "failed"
  | "suppressed";

export interface ConversationReplyOut {
  message_id: string;
  state: MessageState;
  conversation_id: string;
}

/** `POST /engage/conversations/{id}:assign`. `null` clears the assignment. */
export interface ConversationAssignIn {
  assignee_membership_id: string | null;
}

/* ----------------------------------- contact lists ----------------------------------- */

/** `POST /engage/lists`. */
export interface ContactListIn {
  name: string;
  description?: string;
}

/** `PATCH /engage/lists/{list_id}`. An absent field is left alone. */
export interface ContactListPatch {
  name?: string;
  description?: string;
}

export interface ContactListOut {
  id: string;
  name: string;
  description: string;
  /** Folded from `contact_list_members` at read time — never held on the list row itself, so it
   *  can never drift from who is actually on the list. */
  member_count: number;
  created_at: string;
  updated_at: string;
}

/** The most contact ids one `POST /engage/lists/{list_id}/members` (or `.../members/remove`)
 *  call may carry — `LIST_MEMBERS_MAX`. */
export const LIST_MEMBERS_MAX = 1000;

/** `POST /engage/lists/{list_id}/members` and `.../members/remove`. Ids that are not this
 *  organization's contacts are reported back in `unknown` rather than failing the whole call. */
export interface ListMembersIn {
  contact_ids: string[];
}

export interface ListMembersOut {
  changed: number;
  unknown: string[];
}

/* -------------------------------------- campaigns -------------------------------------- */

/** draft -> scheduled | sending -> sent, or cancelled from any state before sent. `paused` is the
 *  send driver's own "no route to send on right now" state, distinct from a person cancelling. */
export type CampaignStatus = "draft" | "scheduled" | "sending" | "paused" | "sent" | "cancelled";
/** `list` addresses one named list; `all_sendable` addresses every contact this organization may
 *  mail (every contact minus every suppression). */
export type CampaignAudience = "list" | "all_sendable";
/** `sending` (the driver's in-flight claim lease) is folded into `queued` on the wire — see
 *  `_campaign_out`'s docstring in `engage.py` — so this app never has a fifth value to render. */
export type RecipientStatus = "queued" | "sent" | "failed" | "suppressed";

/** `POST /engage/campaigns`. A draft — nothing is queued to send until `sendCampaign` is called
 *  and the service's send gate lets it through. */
export interface CampaignIn {
  name: string;
  sender_id: string;
  subject?: string;
  html_body?: string;
  text_body?: string;
  audience?: CampaignAudience;
  list_id?: string | null;
  scheduled_at?: string | null;
}

/** `PATCH /engage/campaigns/{campaign_id}`. Refused once the campaign has left `draft` — what a
 *  campaign said once it started going out is a record, not a form. */
export interface CampaignPatch {
  name?: string;
  sender_id?: string;
  subject?: string;
  html_body?: string;
  text_body?: string;
  audience?: CampaignAudience;
  list_id?: string | null;
  scheduled_at?: string | null;
}

/** `POST /engage/campaigns/{campaign_id}/schedule`. Also how an already-`scheduled` campaign is
 *  rescheduled — same endpoint, same shape, a new instant. */
export interface CampaignScheduleIn {
  scheduled_at: string;
}

/** Recipient counts, folded from `campaign_recipients` by a single grouped query. There is no
 *  `delivered`, `bounced` or `unsubscribed` count here: the service's `campaign_recipients.status`
 *  vocabulary is exactly `queued | sending | sent | failed | suppressed` (`models.py`), because a
 *  recipient row has no link back to the delivery ledger or to a later unsubscribe — those are
 *  provider-webhook and consent facts this schema does not yet fold per campaign. `sent` here
 *  means "handed to the provider without a transport error", not "reached the inbox". */
export interface CampaignStats {
  recipients: number;
  queued: number;
  sent: number;
  failed: number;
  suppressed: number;
}

export interface CampaignOut {
  id: string;
  name: string;
  sender_id: string;
  subject: string;
  html_body: string;
  text_body: string;
  audience: CampaignAudience;
  list_id: string | null;
  status: CampaignStatus;
  scheduled_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  cancelled_at: string | null;
  /** Why the send driver paused this campaign (no connected provider, no usable sender). Empty
   *  unless `status === "paused"`. */
  pause_reason: string;
  stats: CampaignStats;
  created_at: string;
  updated_at: string;
}

/** One row of `GET /engage/campaigns/{campaign_id}/recipients` — addresses masked, exactly as
 *  `campaign_recipients.email_masked` holds them. */
export interface CampaignRecipientOut {
  id: string;
  contact_id: string;
  email: string;
  status: RecipientStatus;
  provider_message_id: string | null;
  error: string;
  sent_at: string | null;
}

/** `POST /engage/campaigns/{campaign_id}/test`. Where the rendered-exactly-as-recipients-see-it
 *  copy went — domain verification is not required for this, unlike the real send. */
export interface CampaignTestSendOut {
  sent: boolean;
  to: string;
}

/* --------------------------------------------------------------------- setup checklist */

export type SetupStepName = "connection" | "domain" | "sender" | "contacts" | "campaign";
export type SetupStepStatus = "done" | "todo" | "blocked";

/** One step of `GET /engage/setup`'s get-started checklist. `link` is a route relative to this
 *  product's own root. */
export interface SetupStepOut {
  step: SetupStepName;
  status: SetupStepStatus;
  detail: string;
  link: string;
}

export interface SetupOut {
  steps: SetupStepOut[];
}
