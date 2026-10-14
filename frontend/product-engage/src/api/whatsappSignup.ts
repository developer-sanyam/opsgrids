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

import { USE_FIXTURES, refuseWithoutService, settle } from "./client";
import { SIGNUP_CONFIG, WHATSAPP_HEALTH, WHATSAPP_NUMBERS } from "./sample.fixtures";
export type {
  SignupConfig,
  SignupCompleteIn,
  SignupCompleteOut,
  SignupStatus,
  SignupStepKey,
  SignupStepState,
  SignupStep,
  SignupStatusOut,
  NameStatus,
  QualityRating,
  MessagingLimit,
  NumberStatus,
  NumberRegistration,
  WhatsAppNumber,
  WhatsAppNumbersOut,
  OkOut,
  TokenState,
  WebhookState,
  WhatsAppHealth,
  TestMessageIn,
  TestMessageOut,
  TemplateSyncOut,
} from "./whatsappSignup.types";
import type {
  SignupConfig,
  SignupCompleteIn,
  SignupCompleteOut,
  SignupStatusOut,
  WhatsAppNumbersOut,
  OkOut,
  WhatsAppHealth,
  TestMessageIn,
  TestMessageOut,
  TemplateSyncOut,
} from "./whatsappSignup.types";

export async function getSignupConfig(): Promise<SignupConfig> {
  if (USE_FIXTURES) return settle(SIGNUP_CONFIG);
  return apiRequest<SignupConfig>("/engage/whatsapp/signup/config");
}

export async function completeSignup(input: SignupCompleteIn): Promise<SignupCompleteOut> {
  if (USE_FIXTURES) refuseWithoutService("Connecting a WhatsApp account");
  return apiRequest<SignupCompleteOut>("/engage/whatsapp/signup/complete", {
    method: "POST",
    body: input,
  });
}

export async function getSignupStatus(setupId: string): Promise<SignupStatusOut> {
  // A setup only exists once `completeSignup` has run, and that is refused in sample mode.
  if (USE_FIXTURES) refuseWithoutService("Checking a WhatsApp setup");
  return apiRequest<SignupStatusOut>(`/engage/whatsapp/signup/${encodeURIComponent(setupId)}`);
}

export async function listWhatsAppNumbers(): Promise<WhatsAppNumbersOut> {
  if (USE_FIXTURES) return settle(WHATSAPP_NUMBERS);
  return apiRequest<WhatsAppNumbersOut>("/engage/whatsapp/numbers");
}

export async function setDefaultNumber(id: string): Promise<OkOut> {
  if (USE_FIXTURES) refuseWithoutService("Changing the default WhatsApp number");
  return apiRequest<OkOut>(`/engage/whatsapp/numbers/${encodeURIComponent(id)}/default`, {
    method: "POST",
  });
}

export async function getWhatsAppHealth(): Promise<WhatsAppHealth> {
  if (USE_FIXTURES) return settle(WHATSAPP_HEALTH);
  return apiRequest<WhatsAppHealth>("/engage/whatsapp/health");
}

export async function sendWhatsAppTestMessage(input: TestMessageIn): Promise<TestMessageOut> {
  if (USE_FIXTURES) refuseWithoutService("Sending a WhatsApp test message");
  return apiRequest<TestMessageOut>("/engage/whatsapp/test-message", {
    method: "POST",
    body: input,
  });
}

/** `POST /engage/whatsapp/disconnect`: marks the connection disconnected and wipes the sealed
 *  token. History — templates, automations, conversations — is kept. */
export async function disconnectWhatsApp(): Promise<OkOut> {
  if (USE_FIXTURES) refuseWithoutService("Disconnecting WhatsApp");
  return apiRequest<OkOut>("/engage/whatsapp/disconnect", { method: "POST" });
}

export async function syncWhatsAppTemplates(): Promise<TemplateSyncOut> {
  if (USE_FIXTURES) refuseWithoutService("Syncing WhatsApp templates");
  return apiRequest<TemplateSyncOut>("/engage/whatsapp/templates/sync", { method: "POST" });
}