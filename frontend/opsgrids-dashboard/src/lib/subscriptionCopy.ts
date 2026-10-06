/**
 * What the Subscriptions page says, as pure functions — pinned by table-driven tests rather than
 * read off a screenshot.
 *
 * Everything about a plan change **states what the server said**. When a change lands, whether
 * anything is charged and why it is refused come from subscription's own preview, the same
 * evaluation the change itself runs; nothing here decides them. There is no amount in a preview:
 * a paid change is priced by the billing run when it raises the invoice.
 *
 * Amounts appear only where the server sent one — this organisation's own negotiated price, to
 * someone who may read its invoices. A public (list) price never reaches this app: pricing is
 * agreed with OpsGrids per organisation.
 */

import type { PlanChangePreview, PlanOption, PlanRef, Subscription } from "@/api/types";
import { daysUntil, formatDate, formatMoney } from "@/lib/money";

/** Product access follows a change by event and token refresh, never instantly. */
export const ACCESS_LAG_COPY = "Product access updates within about 15 minutes.";

export const PRICING_CONTACT_EMAIL = "info@opsgrids.com";

const CHANGEABLE_STATES = new Set(["trial", "active"]);

/** Only a trial or an active subscription changes plan; the server refuses every other state. */
export function canChangePlan(subscription: Subscription): boolean {
  return CHANGEABLE_STATES.has(subscription.state) && !isBundle(subscription);
}

/** A bundle's plan is changed by the OpsGrids team (the server answers `bundle`). */
export function isBundle(subscription: Subscription): boolean {
  const entitled = subscription.entitled_products ?? [];
  return entitled.length > 0 && !(entitled.length === 1 && entitled[0] === subscription.product_key);
}

/** Why Change plan is unavailable on this row, for the disabled control's reason. */
export function changePlanUnavailableReason(subscription: Subscription): string | null {
  if (isBundle(subscription)) return "A bundle's plan is changed by the OpsGrids team.";
  if (subscription.state === "past_due") return "Pay the overdue invoice before changing plan.";
  if (subscription.state === "suspended") return "A suspended subscription can't change plan.";
  if (!CHANGEABLE_STATES.has(subscription.state)) return "This subscription can't change plan.";
  return null;
}

// ------------------------------------------------------------------------- the state column

export interface StatusLine {
  /** The fact, in a few words: "Renews 5 Nov 2026". */
  primary: string;
  /** A qualifier under it: "14 days left". */
  secondary: string | null;
  /** Deserves attention (a trial in its last days, money owed). Never conveyed by colour alone. */
  attention: boolean;
}

/**
 * The one date or fact that matters next for this subscription, by state — replacing a "Trial
 * ends" column that went on showing a converted trial's end date ("5 Oct 2026 (0 days)") on an
 * active subscription.
 */
export function statusLine(subscription: Subscription, now: Date = new Date()): StatusLine {
  switch (subscription.state) {
    case "trial": {
      const days = daysUntil(subscription.trial_ends_at, now);
      if (subscription.trial_ends_at === null || days === null) {
        return { primary: "On trial", secondary: null, attention: false };
      }
      if (days <= 0) {
        return { primary: `Trial ended ${formatDate(subscription.trial_ends_at)}`, secondary: null, attention: true };
      }
      return {
        primary: `Trial ends ${formatDate(subscription.trial_ends_at)}`,
        secondary: days === 1 ? "1 day left" : `${days} days left`,
        attention: days <= 3,
      };
    }
    case "active":
      return subscription.current_period_end
        ? { primary: `Renews ${formatDate(subscription.current_period_end)}`, secondary: null, attention: false }
        : { primary: "Active", secondary: null, attention: false };
    case "past_due":
      return { primary: "Payment overdue", secondary: "Pay the overdue invoice to keep access", attention: true };
    case "suspended":
      return {
        primary: "Access suspended",
        secondary: subscription.dunning
          ? "Pay the overdue invoice to restore access"
          : `Contact ${PRICING_CONTACT_EMAIL} to restore access`,
        attention: true,
      };
    case "cancelled":
      return {
        primary: subscription.cancel_at ? `Cancelled ${formatDate(subscription.cancel_at)}` : "Cancelled",
        secondary: null,
        attention: false,
      };
    default:
      return { primary: "—", secondary: null, attention: false };
  }
}

// ------------------------------------------------------------------------- prices and plans

/** "Billed monthly" — what is said when there is no amount to show. */
export function billedPhrase(interval: string | null): string {
  switch (interval) {
    case "month":
      return "Billed monthly";
    case "year":
      return "Billed yearly";
    case "one_time":
      return "Billed once";
    default:
      return "Billing cadence not set";
  }
}

function perInterval(interval: string | null): string {
  switch (interval) {
    case "month":
      return "per month";
    case "year":
      return "per year";
    default:
      return "";
  }
}

/**
 * "₹2,000.00 per month", "₹500.00 per seat per month + usage" — or, without a visible amount,
 * "Billed monthly". Formatted, never computed: integer minor units in, `Intl` out.
 */
export function priceLine(ref: PlanRef): string {
  const prices = ref.prices;
  if (!ref.amount_visible || prices === null || ref.currency === null) {
    return billedPhrase(ref.billing_interval);
  }
  const currency = ref.currency;
  const fixed = prices
    .filter((p) => (p.kind === "flat" || p.kind === "per_seat") && p.amount_minor !== null)
    .map((p) => {
      const amount = formatMoney(p.amount_minor ?? 0, currency);
      return p.kind === "per_seat" ? `${amount} per ${p.unit ?? "seat"}` : amount;
    });
  const usage = prices.some((p) => p.kind === "usage");
  if (fixed.length === 0) return usage ? `Usage-based, ${billedPhrase(ref.billing_interval).toLowerCase()}` : billedPhrase(ref.billing_interval);
  const cadence = perInterval(ref.billing_interval);
  return `${fixed.join(" + ")}${cadence ? ` ${cadence}` : ""}${usage ? " + usage" : ""}`;
}

/** A plan's name for people, never a bare id. */
export function planName(ref: PlanRef | null): string {
  return ref?.plan_name ?? "Plan no longer listed";
}

/**
 * Each option's label: the plan name, and — only where two options share a name — the version
 * (and the cadence, where that still collides). What was live on 2026-10-06 listed "Revenue
 * Growth" twice with nothing to tell them apart.
 */
export function optionLabels(options: readonly PlanOption[]): Map<string, string> {
  const byName = new Map<string, PlanOption[]>();
  for (const option of options) {
    const name = planName(option);
    byName.set(name, [...(byName.get(name) ?? []), option]);
  }
  const labels = new Map<string, string>();
  for (const [name, group] of byName) {
    for (const option of group) {
      if (group.length === 1) {
        labels.set(option.plan_version_id, name);
        continue;
      }
      const sameVersion = group.filter((o) => o.version_no === option.version_no).length > 1;
      const version = option.version_no !== null ? ` · v${option.version_no}` : "";
      const cadence = sameVersion ? ` · ${billedPhrase(option.billing_interval).replace("Billed ", "")}` : "";
      labels.set(option.plan_version_id, `${name}${version}${cadence}`);
    }
  }
  return labels;
}

/** The picker's rows, once each — the server sends each version once; this does not trust that. */
export function uniqueOptions(options: readonly PlanOption[]): PlanOption[] {
  const seen = new Set<string>();
  return options.filter((o) => {
    if (seen.has(o.plan_version_id)) return false;
    seen.add(o.plan_version_id);
    return true;
  });
}

/** The line under an option: why it cannot be chosen, or its price and cadence. */
export function optionDescription(option: PlanOption): string {
  if (option.blocked_code !== null && option.blocked_code !== "current" && option.blocked_reason) {
    return option.blocked_reason;
  }
  const price = priceLine(option);
  return option.negotiated ? `${price} · agreed for your organisation` : price;
}

/** "Changes to Revenue on 5 Nov 2026" — the row's scheduled change. */
export function pendingLine(pending: PlanRef | null, effectiveAt: string | null): string | null {
  if (pending === null) return null;
  return effectiveAt
    ? `Changes to ${planName(pending)} on ${formatDate(effectiveAt)}`
    : `Changes to ${planName(pending)} at the next renewal`;
}

// ------------------------------------------------------------------------- the preview, in words

function directionPhrase(direction: PlanChangePreview["direction"]): string {
  switch (direction) {
    case "upgrade":
      return "a higher price";
    case "downgrade":
      return "a lower price";
    case "equal":
      return "the same price";
    default:
      return "";
  }
}

interface SentenceInput {
  product: string;
  fromPlan: string;
  toPlan: string;
  preview: PlanChangePreview;
  /** The change already waiting, by plan name, when there is one. */
  pendingPlan: string | null;
  pendingAt: string | null;
}

/**
 * The sentence the confirm step opens with — what happens, and when, as the server computed it.
 * Never "immediately" by default: a paid downgrade waits for the period end, and the sentence
 * says so because the preview does.
 */
export function previewSentence({ product, fromPlan, toPlan, preview, pendingPlan, pendingAt }: SentenceInput): string {
  if (preview.action === "withdraw_scheduled") {
    const when = pendingAt ? ` on ${formatDate(pendingAt)}` : "";
    return `${product} stays on ${fromPlan}. The change to ${pendingPlan ?? "another plan"} scheduled${when} is cancelled. Nothing is charged.`;
  }
  const kind = directionPhrase(preview.direction);
  if (preview.effective === "next_period") {
    return `${product} stays on ${fromPlan} until ${formatDate(preview.effective_at)}, then moves to ${toPlan} (${kind}). Nothing changes before then, and there is no refund or credit for the rest of the current period.`;
  }
  if (!preview.paid) {
    return `${product} moves from ${fromPlan} to ${toPlan} now. You are on a trial, so nothing is charged.`;
  }
  if (preview.prorated_charge_now) {
    return `${product} moves from ${fromPlan} to ${toPlan} now (${kind}). The price difference for the rest of the current billing period is invoiced separately; the amount is worked out when that invoice is raised.`;
  }
  if (preview.usage_closed_now) {
    return `${product} moves from ${fromPlan} to ${toPlan} now (${kind}). No price difference is charged, but usage so far this period is invoiced at ${fromPlan}'s usage rates; usage from now on is billed at ${toPlan}'s.`;
  }
  return `${product} moves from ${fromPlan} to ${toPlan} now (${kind}). Nothing extra is charged.`;
}

/** The notes under the sentence: what it replaces, what it adds or removes, and the access lag. */
export function previewNotes(
  preview: PlanChangePreview,
  {
    pendingPlan,
    productNames,
  }: { pendingPlan: string | null; productNames: (keys: string[]) => string[] },
): string[] {
  const notes: string[] = [];
  if (preview.action === "switch" && preview.would_clear_pending && pendingPlan) {
    notes.push(`This replaces the change to ${pendingPlan} that was scheduled.`);
  }
  if (preview.entitled_added.length > 0) {
    notes.push(`Adds access to ${productNames(preview.entitled_added).join(", ")}.`);
  }
  if (preview.entitled_removed.length > 0) {
    notes.push(`Removes access to ${productNames(preview.entitled_removed).join(", ")}.`);
  }
  if (preview.usage_priced && !preview.usage_closed_now && preview.action === "switch") {
    notes.push("The price comparison covers fixed charges only; usage-based charges are billed as used.");
  }
  if (preview.effective === "now" && preview.action === "switch") notes.push(ACCESS_LAG_COPY);
  return notes;
}

export function confirmLabel(preview: PlanChangePreview): string {
  if (preview.action === "withdraw_scheduled") return "Keep current plan";
  return preview.effective === "next_period" ? "Schedule change" : "Change plan";
}

export function confirmTitle(product: string, toPlan: string, preview: PlanChangePreview): string {
  if (preview.action === "withdraw_scheduled") return `Keep ${product} on its current plan?`;
  return preview.effective === "next_period"
    ? `Schedule ${product}'s move to ${toPlan}?`
    : `Move ${product} to ${toPlan} now?`;
}

/** What a refusal means for the customer's next step. The server's own sentence is shown too. */
export function refusalGuidance(code: string | null): string | null {
  switch (code) {
    case "state":
      return "Only a trial or an active subscription can change plan. Settle any overdue payment first.";
    case "cadence":
      return `A subscription keeps its billing cycle. Contact ${PRICING_CONTACT_EMAIL} to change how often you are billed.`;
    case "currency":
      return `A subscription keeps its currency. Contact ${PRICING_CONTACT_EMAIL} to change it.`;
    case "already_pinned":
      return "You are already on this plan.";
    case "not_offerable":
      return "This plan is no longer offered. The list has been refreshed — choose again.";
    case "billing_catching_up":
      return `We are still processing this subscription's latest billing period, so its plan can't change right now. Try again later; if it persists, contact ${PRICING_CONTACT_EMAIL}.`;
    case "bundle":
      return `A bundle's plan is changed by the OpsGrids team. Contact ${PRICING_CONTACT_EMAIL}.`;
    case "state_changed":
      return "This subscription changed since you opened this. Review the plans again.";
    case "not_published":
    case "product_mismatch":
    case "not_found":
      return "This plan can't be chosen. Choose another.";
    default:
      return null;
  }
}

// ------------------------------------------------------------------------- cancel

/**
 * The cancel confirm, by state — what `services.cancel` actually does: access ends now, the
 * subscription is terminal and cannot be restarted from the dashboard (one subscription per
 * product per organisation), nothing already billed is refunded, and usage so far is invoiced.
 */
export function cancelBody(product: string, subscription: Subscription, pending: string | null): string {
  const scheduled = pending ? ` The scheduled change to ${pending} will not happen.` : "";
  if (subscription.state === "trial") {
    return `The ${product} trial ends now and access stops for everyone in your organisation. This can't be undone, and ${product} can't be started again from this dashboard — contact ${PRICING_CONTACT_EMAIL} if you need it back.${scheduled}`;
  }
  return `Cancellation is immediate and final: access to ${product} ends now, not at the end of the billing period, and there is no automatic refund for the time left. Any usage-based charges so far this period are still invoiced. ${product} can't be started again from this dashboard — contact ${PRICING_CONTACT_EMAIL} if you need it back.${scheduled}`;
}
