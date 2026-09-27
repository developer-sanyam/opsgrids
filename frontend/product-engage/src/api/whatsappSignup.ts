/**
 * WhatsApp Embedded Signup, numbers and health — the W1 half of the WhatsApp go-live contract.
 *
 * Every path is under `/api/v1/engage/whatsapp` (the gateway's `engage` prefix; `API_BASE` already
 * carries `/api/v1`). The shapes are transcribed field for field from the sprint's API contract,
 * and a field the service does not send has no declaration here. Unknown fields the service adds
 * later are ignored by construction: nothing below spreads a response.
 *
 * **No function here sends or receives an access token.** The one credential in play is the
 * short-lived authorization `code` Meta's popup hands the browser, which goes to
 * `signup/complete` once and is never stored, logged or put in a URL; the token it is exchanged
 * for lives only on the server.
 *
 * Writes are not retried anywhere in this module: `signup/complete` starts a background job and a
 * second call would start a second one, and the code it carries is single-use.
 */

import { apiRequest } from "@opsgrids/client";

/* ---------------------------------- signup ---------------------------------- */

/** `GET /engage/whatsapp/signup/config`. `enabled: false` means the server has no Meta app
 *  configured — the screen shows a not-configured state instead of a button that cannot work. */
export interface SignupConfig {
  enabled: boolean;
  app_id: string | null;
  config_id: string | null;
  graph_version: string;
  reason?: "not_configured";
}

/** `POST /engage/whatsapp/signup/complete`. The two ids come from Meta's `WA_EMBEDDED_SIGNUP`
 *  message and are hints: the server discovers the business and its numbers from the token. */
export interface SignupCompleteIn {
  code: string;
  waba_id?: string;
  phone_number_id?: string;
}

export interface SignupCompleteOut {
  setup_id: string;
  status: "running";
}

export type SignupStatus = "running" | "done" | "failed";
export type SignupStepKey = "token" | "number" | "webhook" | "register" | "templates" | "verify";
export type SignupStepState = "waiting" | "running" | "done" | "failed";

export interface SignupStep {
  key: SignupStepKey;
  label: string;
  state: SignupStepState;
  detail: string | null;
}

/** `GET /engage/whatsapp/signup/{setup_id}`. */
export interface SignupStatusOut {
  setup_id: string;
  status: SignupStatus;
  steps: SignupStep[];
  error_code: string | null;
}

export async function getSignupConfig(): Promise<SignupConfig> {
  return apiRequest<SignupConfig>("/engage/whatsapp/signup/config");
}

export async function completeSignup(input: SignupCompleteIn): Promise<SignupCompleteOut> {
  return apiRequest<SignupCompleteOut>("/engage/whatsapp/signup/complete", {
    method: "POST",
    body: input,
  });
}

export async function getSignupStatus(setupId: string): Promise<SignupStatusOut> {
  return apiRequest<SignupStatusOut>(`/engage/whatsapp/signup/${encodeURIComponent(setupId)}`);
}

/* ---------------------------------- numbers ---------------------------------- */

export type NameStatus = "approved" | "pending_review" | "declined" | "none";
export type QualityRating = "green" | "yellow" | "red" | "unknown";
export type MessagingLimit = "TIER_250" | "TIER_1K" | "TIER_10K" | "TIER_100K" | "UNLIMITED" | "unknown";
export type NumberStatus = "connected" | "pending_verification" | "disconnected";
/** Whether the number is registered for the WhatsApp Cloud API (`not_registered` means it cannot
 *  send yet), derived by the service from Meta's `platform_type` and `status`. */
export type NumberRegistration = "registered" | "not_registered" | "unknown";

/** One phone number on the WhatsApp Business Account. `display_phone_number` is masked by the
 *  service, as every address on the wire is. */
export interface WhatsAppNumber {
  id: string;
  phone_number_id: string;
  display_phone_number: string;
  verified_name: string | null;
  name_status: NameStatus;
  quality_rating: QualityRating;
  messaging_limit: MessagingLimit;
  status: NumberStatus;
  is_default: boolean;
  registration: NumberRegistration;
  /** Meta's `code_verification_status`, verbatim (`VERIFIED`, `NOT_VERIFIED`, ...). */
  code_verification_status: string | null;
  /** Meta's `throughput.level` (`STANDARD`, `HIGH`, `NOT_APPLICABLE`). */
  throughput_level: string | null;
}

export interface WhatsAppNumbersOut {
  items: WhatsAppNumber[];
}

export interface OkOut {
  ok: true;
}

export async function listWhatsAppNumbers(): Promise<WhatsAppNumbersOut> {
  return apiRequest<WhatsAppNumbersOut>("/engage/whatsapp/numbers");
}

export async function setDefaultNumber(id: string): Promise<OkOut> {
  return apiRequest<OkOut>(`/engage/whatsapp/numbers/${encodeURIComponent(id)}/default`, {
    method: "POST",
  });
}

/* ---------------------------------- health ---------------------------------- */

export type TokenState = "valid" | "expiring" | "invalid" | "unknown";
export type WebhookState = "receiving" | "silent" | "unverified";

/** `GET /engage/whatsapp/health`. */
export interface WhatsAppHealth {
  connected: boolean;
  token: { state: TokenState; checked_at: string | null };
  webhook: { state: WebhookState; last_event_at: string | null };
  templates: { count: number; synced_at: string | null };
  number: { quality_rating: string; messaging_limit: string } | null;
}

export async function getWhatsAppHealth(): Promise<WhatsAppHealth> {
  return apiRequest<WhatsAppHealth>("/engage/whatsapp/health");
}

/* ------------------------------ test message, disconnect ------------------------------ */

/** `POST /engage/whatsapp/test-message`. Goes through the consent decision like any send, and is
 *  refused with 409 `channel_not_connected` when there is no connection. */
export interface TestMessageIn {
  /** E.164, `+919876543210`. */
  to: string;
  template_name: string;
  language: string;
}

export interface TestMessageOut {
  message_id: string;
}

export async function sendWhatsAppTestMessage(input: TestMessageIn): Promise<TestMessageOut> {
  return apiRequest<TestMessageOut>("/engage/whatsapp/test-message", {
    method: "POST",
    body: input,
  });
}

/** `POST /engage/whatsapp/disconnect`: marks the connection disconnected and wipes the sealed
 *  token. History — templates, automations, conversations — is kept. */
export async function disconnectWhatsApp(): Promise<OkOut> {
  return apiRequest<OkOut>("/engage/whatsapp/disconnect", { method: "POST" });
}

/* --------------------------------- template sync --------------------------------- */

/** `POST /engage/whatsapp/templates/sync` (W2). Used by the health card's "Sync now". */
export interface TemplateSyncOut {
  synced: number;
  created: number;
  updated: number;
  removed: number;
}

export async function syncWhatsAppTemplates(): Promise<TemplateSyncOut> {
  return apiRequest<TemplateSyncOut>("/engage/whatsapp/templates/sync", { method: "POST" });
}
