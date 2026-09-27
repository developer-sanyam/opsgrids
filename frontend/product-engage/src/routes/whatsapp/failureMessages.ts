import { ApiError } from "@opsgrids/client";

import type { EmbeddedSignupFault } from "@/lib/metaSdk";

/**
 * What each machine-readable failure code from the WhatsApp setup says on screen.
 *
 * The service names why a setup stopped (`signup/{id}`'s `error_code`, and the `errors[0].code` of a
 * refused `signup/complete`); those words are for the program, not the person, and one of them used
 * to be printed raw ("Reason: number_claimed"). Every code the service can produce has a sentence
 * here that says what happened and what to do next. A code this map has never seen falls back to
 * the service's own sentence when there is one, and to a general line when there is not.
 */
const MESSAGES: Record<string, string> = {
  // The Meta window's code, exchanged the moment it arrives.
  code_expired: "The Meta sign-in expired or was already used. Start again from the Meta window.",
  code_exchange_failed: "Meta did not accept the sign-in. Start again from the Meta window.",
  meta_unreachable: "We couldn't reach Meta just now. Wait a moment, then start again.",
  not_configured: "Connecting with Meta isn't set up on this platform yet. Ask your administrator.",
  setup_in_progress: "A WhatsApp setup is already running. Wait for it to finish, then try again.",
  // Finding the account and the number.
  waba_ambiguous:
    "More than one WhatsApp Business Account was shared with us, and we couldn't tell which to use. Start again and share just one.",
  waba_not_found: "No WhatsApp Business Account was shared. Start again and choose one in the Meta window.",
  waba_not_accessible:
    "That WhatsApp Business Account wasn't shared with us. Start again and allow access to it in the Meta window.",
  no_phone_number:
    "This WhatsApp Business Account has no phone number. Add one in WhatsApp Manager, then start again.",
  number_not_found: "The phone number you chose isn't on that account. Start again and pick it in the Meta window.",
  ownership_check_failed:
    "Meta wouldn't confirm that this account controls that number. Check the number in WhatsApp Manager, then start again.",
  number_claimed:
    "This phone number is already connected to another organization. Disconnect it there first, or use a different number.",
  // Subscribing, registering and checking.
  webhook_subscribe_failed:
    "Meta refused to send message events to OpsGrids. Check that this app has access to the account, then start again.",
  webhook_not_subscribed:
    "Meta doesn't list OpsGrids as subscribed to this account yet. Start again to retry the subscription.",
  pin_required:
    "This number has two-step verification turned on, with a PIN we don't have. Turn it off for this number in WhatsApp Manager, then start again.",
  number_not_verified:
    "Meta needs this number verified again before it can be registered. Verify it in WhatsApp Manager, then start again.",
  register_rate_limited:
    "Meta is limiting registration attempts for this number. Wait a while (up to 72 hours) before trying again.",
  register_failed: "Meta could not register this number for WhatsApp. Start again, or check it in WhatsApp Manager.",
  number_not_connected:
    "The number isn't active on WhatsApp yet, so it can't send or receive messages. Finish its setup in WhatsApp Manager, then start again.",
  templates_sync_failed: "We couldn't import your message templates. Start again, or sync them later from Templates.",
  verify_failed: "The final check with Meta didn't pass. Start again.",
  // Ours.
  channel_not_connected: "WhatsApp isn't connected. Start again from the Meta window.",
  token_missing: "The connection could not be completed. Start again from the Meta window.",
  timed_out: "Setup took too long and was stopped. Start again from the Meta window.",
  abandoned: "An earlier setup was replaced by a newer one. Start again from the Meta window.",
  internal_error: "Something went wrong on our side. Start again in a few minutes.",
};

const GENERAL = "Setup stopped before it finished. Start again from the Meta window.";

/** The sentence for a failure code. `fallback` is the service's own sentence (a step's `detail`,
 *  written to be shown), used only for a code this map does not know. */
export function signupFailureMessage(code: string | null | undefined, fallback?: string | null): string {
  if (code !== null && code !== undefined && Object.hasOwn(MESSAGES, code)) return MESSAGES[code] ?? GENERAL;
  return fallback !== undefined && fallback !== null && fallback !== "" ? fallback : GENERAL;
}

/** The `errors[0].code` of a refused request, when the service sent one. */
export function problemCode(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null;
  const first: unknown = error.problem?.errors?.[0];
  if (typeof first !== "object" || first === null) return null;
  const code = (first as { code?: unknown }).code;
  return typeof code === "string" && code !== "" ? code : null;
}

/** What a refused `signup/complete` says: the friendly sentence for its code when it has one, else
 *  the service's own sentence, else the connection failure the caller supplies. */
export function completeFailureMessage(error: unknown, unreachable: string): string {
  const code = problemCode(error);
  if (code !== null && Object.hasOwn(MESSAGES, code)) return MESSAGES[code] ?? GENERAL;
  return error instanceof ApiError ? error.message : unreachable;
}

/** Meta's own report of why the popup stopped: its sentence, then the code and the session id it
 *  gave, which are what Meta's support asks for. */
export function metaFaultMessage(fault: EmbeddedSignupFault): string {
  const base = fault.message ?? "Meta reported an error.";
  const refs: string[] = [];
  if (fault.code !== null) refs.push(`code ${fault.code}`);
  if (fault.sessionId !== null) refs.push(`session ${fault.sessionId}`);
  return refs.length === 0 ? base : `${base} (${refs.join(", ")})`;
}

/** Whether a `cancel` carries a reason, i.e. Meta stopped it rather than the customer closing it. */
export function hasFault(fault: EmbeddedSignupFault): boolean {
  return fault.message !== null || fault.code !== null;
}
