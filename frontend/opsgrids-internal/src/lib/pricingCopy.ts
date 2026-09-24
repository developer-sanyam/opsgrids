/**
 * The one approved sentence the "Set price" dialog shows, in both places it appears: the
 * pre-submit confirmation step (predicted from the form) and the post-submit result (the response's
 * own `effective`). Kept as pure functions so the copy is pinned by a table-driven test rather than
 * read off a screenshot.
 */

import type { BillingInterval, PricingEffect } from "@/api/types";
import { formatMoney } from "@/lib/money";

/** A client-side best guess at `effective`, shown before the request exists. The server's own
 *  answer always wins once it arrives — this never substitutes for it. */
export function predictEffective(hasSubscription: boolean, applyToSubscription: boolean): PricingEffect {
  if (!hasSubscription) return "not_subscribed";
  return applyToSubscription ? "now" : "next_period";
}

export function outcomePhrase(effective: PricingEffect): string {
  switch (effective) {
    case "now":
      return "applies now";
    case "next_period":
      return "applies from the next period";
    case "not_subscribed":
      return "applies when they subscribe";
  }
}

function perInterval(interval: BillingInterval): string {
  return interval === "year" ? "year" : "month";
}

/** "Set OpsGrids CRM for Zenith Polymers to ₹1,20,000 per month — applies now." */
export function summarySentence(
  productName: string,
  organizationName: string,
  amountMinor: number,
  currency: string,
  interval: BillingInterval,
  effective: PricingEffect,
): string {
  // `precise` — a price agreed to the paisa should never round its decimals away in the one
  // sentence that states what was agreed, unlike the rounded MRR figures elsewhere in the console.
  const amount = formatMoney(amountMinor, currency, { precise: true });
  return `Set ${productName} for ${organizationName} to ${amount} per ${perInterval(interval)} — ${outcomePhrase(effective)}.`;
}
