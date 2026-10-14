/** The request and response shapes of `./whatsappSignup`, kept apart so fixtures can import them without importing the requests. */

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

/** `POST /engage/whatsapp/signup/complete`. `waba_id`/`phone_number_id` come from Meta's
 *  `WA_EMBEDDED_SIGNUP` message and are hints: the server discovers the business and its numbers
 *  from the token. `meta_user_id` comes from a different place — `FB.login`'s own response
 *  (`authResponse.userID`) — and is never verified against Meta; it is captured only so a later
 *  deauthorize or data-deletion callback naming this same id can be attributed back to this
 *  connection (`docs/whatsapp/webhooks.md`). */
export interface SignupCompleteIn {
  code: string;
  waba_id?: string;
  phone_number_id?: string;
  meta_user_id?: string;
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

/* ---------------------------------- health ---------------------------------- */

export type TokenState = "valid" | "expiring" | "invalid" | "unknown";

export type WebhookState = "receiving" | "silent" | "unverified";

/** `GET /engage/whatsapp/health`. */
export interface WhatsAppHealth {
  connected: boolean;
  token: {
    state: TokenState;
    checked_at: string | null;
    /** When Meta's deauthorize or data-deletion callback last reported the Facebook login behind
     *  this connection as revoked. Informational only — it never changes `state` above and never
     *  means sends have stopped; see `labels.ts`'s copy for this field. */
    identity_revoked_at: string | null;
  };
  webhook: { state: WebhookState; last_event_at: string | null };
  templates: { count: number; synced_at: string | null };
  number: { quality_rating: string; messaging_limit: string } | null;
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

/* --------------------------------- template sync --------------------------------- */

/** `POST /engage/whatsapp/templates/sync` (W2). Used by the health card's "Sync now". */
export interface TemplateSyncOut {
  synced: number;
  created: number;
  updated: number;
  removed: number;
}
