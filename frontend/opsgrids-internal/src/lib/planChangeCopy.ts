/**
 * The words the Change plan dialog says, as pure functions — pinned by a table-driven test rather
 * than read off a screenshot.
 *
 * Everything here **states what the server said**. Direction, timing and refusal come from
 * subscription's own preview and are never recomputed in the browser (the console shows no amount
 * either: a paid change is priced by the billing run when it claims the period, so there is no
 * quote to read, and a second implementation here could disagree with the first invoice).
 *
 * Dates are UTC and date-only, like every other date in this console, and the copy says so where a
 * date decides something — an operator in IST can otherwise read a UTC midnight as the day before.
 */

import type { PlanChangeDirection, PlanChangePreview, PlanChangeResult } from "@/api/types";
import { formatDate } from "@/lib/format";

/** Effects reach a customer's session by event and token refresh, never instantly. Said wherever a
 *  change is confirmed or reported, so nobody tells a customer "it's live" a minute after. */
export const ENTITLEMENT_LAG_COPY =
  "Product access follows within about 15 minutes of the change taking effect — it reaches a signed-in user on their next token refresh.";

/** What "next billing period" depends on: the billing run applies it, and only while it runs. */
export const BILLING_RUN_COPY =
  "A change for the next period is applied by the billing run when it claims that period. If the billing run is paused, it waits.";

export const ROLE_HINT = "Owners and billing ops can change a plan.";

export function directionPhrase(direction: PlanChangeDirection | null): string {
  switch (direction) {
    case "upgrade":
      return "a price increase";
    case "downgrade":
      return "a price decrease";
    case "equal":
      return "the same price";
    case null:
      return "";
  }
}

interface SentenceInput {
  organizationName: string;
  fromPlan: string;
  toPlan: string;
  preview: PlanChangePreview;
  /** The change already waiting, by plan name, when there is one. */
  pendingPlan: string | null;
}

/**
 * The one sentence the confirm step opens with — what will happen, to whom, and when.
 * "Zenith Polymers stays on Growth until 1 Nov 2026, then moves to Scale (a price increase)."
 */
export function previewSentence({
  organizationName,
  fromPlan,
  toPlan,
  preview,
  pendingPlan,
}: SentenceInput): string {
  if (preview.action === "withdraw_scheduled") {
    return `Withdraw the change scheduled to ${pendingPlan ?? "another plan"} — ${organizationName} stays on ${fromPlan}.`;
  }
  const kind = directionPhrase(preview.direction);
  if (preview.effective === "next_period") {
    const when = `${formatDate(preview.effective_at)} (UTC)`;
    return `${organizationName} stays on ${fromPlan} until ${when}, then moves to ${toPlan} (${kind}). Nothing changes, and nothing is charged, before then.`;
  }
  if (preview.direction === "equal") {
    if (preview.usage_closed_now) {
      // The pin moves now, so subscription closes the usage metered so far on the current plan
      // and the billing run invoices it at that plan's rates — "nothing is charged" would be false.
      return `${organizationName} moves from ${fromPlan} to ${toPlan} now (${kind}). No proration is charged, but usage metered so far this period is invoiced at ${fromPlan}'s usage rates; usage from now on is billed at ${toPlan}'s.`;
    }
    return `${organizationName} moves from ${fromPlan} to ${toPlan} now (${kind}). Nothing is charged.`;
  }
  return `${organizationName} moves from ${fromPlan} to ${toPlan} now (${kind}). They are on a trial, so nothing is charged.`;
}

/** Why the preview refused, in the operator's terms. The server's own sentence is shown
 *  separately and verbatim; this is the next step it implies. */
export function refusalGuidance(code: string | null): string | null {
  switch (code) {
    case "state":
      return "Only a trial or an active subscription can change plan. A past-due or suspended one has to be put right first.";
    case "cadence":
      return "A subscription keeps one billing cadence, and moving it to another is not possible yet. Do not cancel to get there: a cancelled subscription cannot be replaced, so the customer would lose the product.";
    case "currency":
      return "A subscription keeps one currency.";
    case "already_pinned":
      return "Choose a different plan.";
    case "not_offerable":
      return "The catalogue has moved on since this list was loaded. The list now shows only the plans on offer — choose again.";
    case "billing_catching_up":
      return "Billing has not processed a period that is due on this subscription. While the billing run is paused this does not clear by itself — ask engineering before retrying.";
    case "state_changed":
      return "Review the subscription again — it is not the way it was when you opened this.";
    case "reserved_reason":
      return "Describe the reason in your own words.";
    default:
      return null;
  }
}

/** What a failed confirm means for the operator's next move. */
export type FailureKind =
  /** Refused for a reason a retry cannot change: back to the form, with a fresh key. */
  | "refused"
  /** The outcome is not known: stay put, refresh to look, retry with the *same* key. */
  | "unknown"
  /** Another request holds this key right now. */
  | "in_flight"
  /** The server says this request changed nothing (a dependency was down, or console's own
   *  credentials were refused): stay put, nothing to check — but no "may or may not". */
  | "not_made";

/** 5xx codes whose answer is "nothing was changed by this request", not "outcome unknown". */
const NOT_MADE_CODES = new Set(["upstream_refused", "catalog_unavailable", "preview_unavailable"]);

export function failureKind(status: number | null, code: string | null): FailureKind {
  if (code === "request_in_progress") return "in_flight";
  if (code !== null && NOT_MADE_CODES.has(code)) return "not_made";
  // No status at all is a network failure after the request left; 5xx is an upstream failing.
  if (status === null || status >= 500) return "unknown";
  return "refused";
}

export function unknownOutcomeGuidance(): string {
  return "The change may or may not have been made. Refresh this account to check before doing anything else — pressing Try again repeats the same request, which is safe.";
}

/** The next step when the server says nothing was changed. A refused service credential does not
 *  clear by retrying; a dependency that was down usually does. */
export function notMadeGuidance(code: string | null): string {
  if (code === "upstream_refused") {
    return "Nothing was changed. Console's own service credentials were refused, which retrying will not fix — send engineering the reference.";
  }
  return "Nothing was changed. Try again in a moment.";
}

export function resultTitle(result: PlanChangeResult): string {
  return result.effective === "next_period" ? "Change scheduled" : "Plan changed";
}

/** What was done, from the server's own answer. "Zenith Polymers moves to Scale on 1 Nov 2026." */
export function resultSentence(
  organizationName: string,
  toPlan: string,
  result: PlanChangeResult,
): string {
  if (result.effective === "next_period") {
    return `${organizationName} moves to ${toPlan} on ${formatDate(result.effective_at)} (UTC). They stay on their current plan until then.`;
  }
  return `${organizationName} is on ${toPlan}.`;
}
