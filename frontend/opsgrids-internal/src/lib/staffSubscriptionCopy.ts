/**
 * The words the Add product and Extend trial dialogs say, and the IST date arithmetic Extend trial
 * needs — pure functions, pinned by a table-driven test (ADR-0030).
 *
 * **Dates are IST and say so.** The rest of this console renders UTC, which is right for a stored
 * instant nobody acts on; but a trial end is a promise an operator in India makes to a customer in
 * India, and "ends 03 Nov (UTC)" is 05:30 IST on the 3rd — a day early for anyone reading it
 * locally. So Extend trial asks for an IST calendar date, sends the last second of that day with
 * its zone (`2026-11-03T23:59:59+05:30`), and every date it shows carries the "IST" label. IST is a
 * fixed +05:30 with no daylight saving, so this is plain arithmetic, not a timezone database.
 *
 * Everything else here **states what the server said**; nothing decides a rule the server owns.
 * The trial cap in particular is read off the live composite (`trial_max_ends_at`), never restated.
 */

import type { AddProductOption, AddProductResult, ExtendTrialResult } from "@/api/types";
import { refusalGuidance as planChangeGuidance } from "@/lib/planChangeCopy";

const DAY_MS = 86_400_000;
const IST_OFFSET_MS = 330 * 60_000;

/** The label every IST date carries, offset included so nobody has to know it. */
export const IST_LABEL = "IST (UTC+05:30)";

export const ADD_ROLE_HINT = "Owners and billing ops can add a product.";
export const EXTEND_ROLE_HINT = "Only an owner can extend a trial.";

/** What a paid start means, said where the operator has to tick it. */
export const PAID_ACKNOWLEDGEMENT =
  "I understand billing starts now: the first period is claimed for payment immediately, and no payment mandate is checked first.";

/** The IST calendar date (`YYYY-MM-DD`) an instant falls on. */
export function istDate(iso: string): string {
  return new Date(Date.parse(iso) + IST_OFFSET_MS).toISOString().slice(0, 10);
}

/** The last second of an IST calendar date, as an instant that carries its zone. */
export function endOfIstDay(date: string): string {
  return `${date}T23:59:59+05:30`;
}

function shiftDate(date: string, days: number): string {
  return new Date(Date.parse(`${date}T00:00:00Z`) + days * DAY_MS).toISOString().slice(0, 10);
}

/** "03 Nov 2026, 23:59 IST". */
export function formatIst(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) return "—";
  const shifted = new Date(ms + IST_OFFSET_MS);
  const day = shifted.toLocaleDateString("en-IN", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    timeZone: "UTC",
  });
  const time = shifted.toISOString().slice(11, 16);
  return `${day}, ${time} IST`;
}

/** "03 Nov 2026" for an IST calendar date. */
export function formatIstDate(date: string): string {
  return formatIst(`${date}T00:00:00+05:30`).replace(/, \d\d:\d\d IST$/, "");
}

export interface ExtensionWindow {
  /** The first IST date whose end is after both the trial's current end and now. */
  min: string;
  /** The last IST date whose end is within the cap. */
  max: string;
}

/**
 * The IST dates a trial's last day may be moved to, or null when none fits (the trial is already at
 * its cap, or has no cap because it is not trialling). Mirrors the server's own rule — later than
 * the current end, in the future, no later than the cap — so the date field never offers a day the
 * server would refuse; the server still decides.
 */
export function extensionWindow(
  trialEndsAt: string | null,
  trialMaxEndsAt: string | null,
  now: Date,
): ExtensionWindow | null {
  if (!trialMaxEndsAt) return null;
  const floor = Math.max(trialEndsAt ? Date.parse(trialEndsAt) : 0, now.getTime());
  let min = istDate(new Date(floor).toISOString());
  if (Date.parse(endOfIstDay(min)) <= floor) min = shiftDate(min, 1);
  const cap = Date.parse(trialMaxEndsAt);
  let max = istDate(trialMaxEndsAt);
  if (Date.parse(endOfIstDay(max)) > cap) max = shiftDate(max, -1);
  return min <= max ? { min, max } : null;
}

/** The cap as a number of days from the trial's start — what "at most N days" says. */
export function totalTrialDays(trialStart: string | null, trialMaxEndsAt: string | null): number | null {
  if (!trialStart || !trialMaxEndsAt) return null;
  return Math.round((Date.parse(trialMaxEndsAt) - Date.parse(trialStart)) / DAY_MS);
}

/** Why the server refused, in the operator's terms — the next step it implies. The server's own
 *  sentence is shown separately and verbatim. */
export function staffRefusalGuidance(code: string | null): string | null {
  switch (code) {
    case "already_subscribed":
      return "This organization already has this product. A cancelled subscription cannot be reopened or replaced yet.";
    case "one_time":
      return "A one-time plan is never billed. Choose a monthly or yearly plan.";
    case "org_currency_mismatch":
      return "An organization is billed in one currency. Choose a plan in the currency its other subscriptions use.";
    case "trial_not_offered":
      return "This plan sells no trial. Choose a paid start and confirm the charge, or pick a plan with a trial.";
    case "charge_not_acknowledged":
      return "Confirm that billing starts now, or add the product as a trial.";
    case "not_found":
    case "not_published":
      return "That plan is no longer on offer. Choose again from the refreshed list.";
    case "product_mismatch":
      return "That plan belongs to another product. Choose again from the refreshed list.";
    case "bundle":
      return "The product registry cannot vouch for this bundle's members right now. Ask engineering before retrying.";
    case "not_trial":
      return "Only a subscription that is still trialling can be extended.";
    case "claim_in_flight":
      return "Billing is already converting this trial, so it can no longer be extended.";
    case "trial_cap":
      return "That is past the longest a trial may run. Choose an earlier day.";
    case "trial_not_extended":
      return "Choose a day after the trial's current end, and in the future.";
    case "no_subscription":
      return "This organization no longer has that subscription. Refresh the account.";
    default:
      return planChangeGuidance(code);
  }
}

function intervalWord(interval: string): string {
  return interval === "year" ? "year" : interval === "month" ? "month" : interval.replace("_", " ");
}

interface AddSentenceInput {
  organizationName: string;
  productName: string;
  option: AddProductOption;
  trial: boolean;
}

/** The sentence the Add product confirm opens with — what will happen, to whom, and what it costs
 *  in time and money. No amount is promised: billing prices the first period. */
export function addPreviewSentence({ organizationName, productName, option, trial }: AddSentenceInput): string {
  if (trial) {
    const length = option.trial_days ? `a ${option.trial_days}-day trial` : "the plan's standard trial";
    return `${organizationName} gets ${productName} on ${option.plan_name}, starting with ${length}. Nothing is charged until the trial ends; the billing run then converts it.`;
  }
  return `${organizationName} gets ${productName} on ${option.plan_name}, paying from today. Its first ${intervalWord(option.billing_interval)} is claimed for billing immediately and no payment mandate is checked first — if the charge fails, the subscription goes past due and is suspended when the grace window closes.`;
}

export function addResultTitle(result: AddProductResult): string {
  return result.charge_claimed ? "Product added" : "Trial started";
}

export function addResultSentence(
  organizationName: string,
  productName: string,
  planName: string,
  result: AddProductResult,
): string {
  if (result.charge_claimed) {
    return `${organizationName} has ${productName} on ${planName}, paying from today. Its first period has been claimed for billing.`;
  }
  return `${organizationName} has ${productName} on ${planName}, trialling until ${formatIst(result.trial_ends_at)}.`;
}

export function extendPreviewSentence(
  organizationName: string,
  productName: string,
  from: string | null,
  to: string,
): string {
  return `${organizationName}'s ${productName} trial will end at ${formatIst(to)} instead of ${formatIst(from)}. Nothing is charged before then; the billing run converts it at the new end.`;
}

export function extendResultSentence(
  organizationName: string,
  productName: string,
  result: ExtendTrialResult,
): string {
  return `${organizationName}'s ${productName} trial now ends at ${formatIst(result.trial_ends_at)}.`;
}
