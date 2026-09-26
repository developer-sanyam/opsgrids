import type {
  MessagingLimit,
  NameStatus,
  QualityRating,
  SignupStepKey,
  SignupStepState,
  TokenState,
  WebhookState,
  WhatsAppNumber,
} from "@/api/whatsappSignup";
import type { StatusTone } from "@opsgrids/ui";

/**
 * How the wire's tokens read on screen. Each is a total map over its union, so a value the
 * service adds is a compile error here rather than `undefined` on a customer's screen.
 */

const QUALITY: Record<QualityRating, { label: string; tone: StatusTone }> = {
  green: { label: "High", tone: "success" },
  yellow: { label: "Medium", tone: "warning" },
  red: { label: "Low", tone: "danger" },
  unknown: { label: "Not rated yet", tone: "neutral" },
};

export function qualityLabel(rating: QualityRating): string {
  return QUALITY[rating].label;
}

export function qualityTone(rating: QualityRating): StatusTone {
  return QUALITY[rating].tone;
}

/** The health endpoint types the number's rating as a plain string; anything it does not
 *  recognise reads as unrated rather than being shown raw. */
export function qualityFromString(value: string): QualityRating {
  return value === "green" || value === "yellow" || value === "red" ? value : "unknown";
}

const LIMIT: Record<MessagingLimit, string> = {
  TIER_250: "250 customers / 24 hours",
  TIER_1K: "1,000 customers / 24 hours",
  TIER_10K: "10,000 customers / 24 hours",
  TIER_100K: "100,000 customers / 24 hours",
  UNLIMITED: "Unlimited customers",
  unknown: "Not available",
};

export function limitLabel(limit: MessagingLimit): string {
  return LIMIT[limit];
}

export function limitFromString(value: string): MessagingLimit {
  return value in LIMIT ? (value as MessagingLimit) : "unknown";
}

const NAME_STATUS: Record<NameStatus, string> = {
  approved: "approved",
  pending_review: "in review",
  declined: "declined",
  none: "not set",
};

/** "Acme Corp (approved)" — the verified name with what Meta has said about it. */
export function displayNameLabel(number: WhatsAppNumber): string {
  if (number.verified_name === null) return "Not set";
  return `${number.verified_name} (${NAME_STATUS[number.name_status]})`;
}

/** The number's own state as a badge: what a person can do with it, in words. `where` picks the
 *  design's two wordings for a usable number: the chooser says "Ready", Settings says "Active". */
export function numberStatus(
  number: WhatsAppNumber,
  where: "select" | "settings",
): { label: string; tone: StatusTone } {
  if (number.status === "pending_verification") return { label: "Needs verification", tone: "neutral" };
  if (number.status === "disconnected") return { label: "Disconnected", tone: "danger" };
  if (number.name_status === "pending_review") return { label: "Display name in review", tone: "warning" };
  if (number.name_status === "declined") return { label: "Display name declined", tone: "danger" };
  return { label: where === "select" ? "Ready" : "Active", tone: "success" };
}

/** A number can be chosen only when it is connected. */
export function isSelectable(number: WhatsAppNumber): boolean {
  return number.status === "connected";
}

/** `••••7890` — the last four of an id, for recognising an account without printing all of it. */
export function maskedId(id: string): string {
  return `ID ••••${id.slice(-4)}`;
}

export const STEP_HINT: Record<SignupStepKey, string> = {
  token: "Stored encrypted. Only the WhatsApp service can read it.",
  number: "Registering the number you chose so it can send and receive.",
  webhook: "Messages, delivery status and template updates arrive in OpsGrids.",
  templates: "Importing your message templates from Meta.",
  verify: "Sends a test event through the webhook and back.",
};

export const STEP_STATE_LABEL: Record<SignupStepState, string> = {
  done: "Done",
  running: "In progress",
  waiting: "Waiting",
  failed: "Failed",
};

export const TOKEN_TEXT: Record<TokenState, string> = {
  valid: "Valid. Refreshed automatically and stored encrypted.",
  expiring: "Expiring soon. Choose Reconnect above to renew it.",
  invalid: "No longer valid. Choose Reconnect above to sign in with Meta again.",
  unknown: "Not checked yet.",
};

export const WEBHOOK_TEXT: Record<Exclude<WebhookState, "receiving">, string> = {
  silent: "No events have arrived recently. Messages sent to this number may not be reaching OpsGrids.",
  unverified: "Meta has not confirmed the webhook yet.",
};

/** Test-message numbers are typed with spaces and dashes as people write them; E.164 has none. */
export function toE164(input: string): string {
  return input.replace(/[\s()-]/g, "");
}

export function isE164(value: string): boolean {
  return /^\+[1-9]\d{6,14}$/.test(value);
}
