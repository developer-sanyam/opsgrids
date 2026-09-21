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
export type DnsRecordKind = "spf" | "dkim" | "dmarc" | "return_path" | "tracking";

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

/** `POST /engage/domains`. */
export interface DomainIn {
  domain: string;
}

/** One record the customer publishes at their registrar, and whether DNS has it yet. */
export interface DnsRecordOut {
  kind: DnsRecordKind;
  /** The RR type as it is typed at a registrar — `TXT`, `CNAME`. */
  type: string;
  host: string;
  value: string;
  /** DMARC is the one that is recommended rather than required: a domain whose security team has
   *  not settled a policy yet still sends. */
  required: boolean;
  verified: boolean;
}

export interface DomainOut {
  id: string;
  domain: string;
  status: DomainStatus;
  /** Returned in full on every read, so the screen shows exactly what to paste — and shows the
   *  same values it showed yesterday. */
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

/* ------------------------------- provider templates ------------------------------ */
//
// `services/notification`'s templates, transcribed from the `engage` section of `schemas.py` in
// shape only — these routes are mounted at `/api/v1/notifications`, gated on the `notification`
// product rather than `engage` (`services/app-gateway/routes.json`). See `client.ts` for what
// that means for this screen.

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

/** `PUT /notifications/provider-templates`. */
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

/** `GET /notifications/provider-templates`. `status` and `sendable` are the provider's own
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

/* ------------------------------ audience: contacts ------------------------------ */

/** `POST /engage/contacts`. Rejected outright on a duplicate address — `POST /engage/contacts/
 *  import` is the idempotent path, and a create that sometimes updates a different row than it
 *  made would be a route that lies about what it did. */
export interface ContactIn {
  email: string;
  name?: string;
  attributes?: Record<string, unknown>;
}

/** `PUT /engage/contacts/{id}`. No `email` — an address is the contact's identity, and changing
 *  it is deleting one contact and creating another, not an edit. */
export interface ContactUpdateIn {
  name?: string;
  attributes?: Record<string, unknown>;
}

export interface ContactOut {
  id: string;
  /** Already normalized (trimmed, lowercased) by the service — shown exactly as stored. */
  email: string;
  name: string;
  attributes: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

/** One row of `POST /engage/contacts/import`'s body. `email` is a plain string on the wire, not
 *  validated client-side beyond "non-empty" — a malformed row becomes one `error` entry in the
 *  report rather than blocking the rows around it. */
export interface ContactBulkRowIn {
  email: string;
  name?: string;
  attributes?: Record<string, unknown>;
}

export interface ContactBulkImportIn {
  rows: ContactBulkRowIn[];
}

export type BulkImportRowStatus = "created" | "updated" | "error";

export interface ContactBulkImportRowOut {
  row: number;
  email: string;
  status: BulkImportRowStatus;
  error: string | null;
}

/** The full per-row report `POST /engage/contacts/import` returns — every input row gets exactly
 *  one entry, in order, so a screen can show which line of a CSV failed and why. */
export interface ContactBulkImportOut {
  total: number;
  created: number;
  updated: number;
  errors: number;
  rows: ContactBulkImportRowOut[];
}

/* -------------------------------- audience: lists -------------------------------- */

export interface ContactListIn {
  name: string;
  description?: string;
}

export interface ContactListOut {
  id: string;
  name: string;
  description: string;
  created_at: string;
}

/** `POST /engage/lists/{id}/members` and `.../members/remove`. Idempotent per contact — a
 *  contact already on (or already off) the list is skipped rather than erroring. */
export interface ListMembershipIn {
  contact_ids: string[];
}

/** The contacts the request actually changed. Already-members on an add, or already-absent
 *  contacts on a remove, are silently skipped and so are absent from this list. */
export interface ListMembershipOut {
  contact_ids: string[];
}

/* ----------------------------- audience: suppression ----------------------------- */

export type SuppressionReason = "unsubscribed" | "bounced" | "complained" | "manual";

/** `POST /engage/suppressions` — an operator adding a contact directly. `unsubscribed` and
 *  `bounced`/`complained` are recorded by the public link and (later) provider feedback; a form
 *  posting this only ever sends `manual`. */
export interface SuppressionIn {
  contact_id: string;
  reason?: SuppressionReason;
  note?: string;
}

export interface SuppressionOut {
  id: string;
  contact_id: string;
  reason: SuppressionReason;
  note: string;
  created_at: string;
}
