/**
 * The words the payment screens use for each provider a merchant can connect (ADR-0033): its
 * name, what its two modes are called, and how a reconciliation difference reads. The server
 * speaks one vocabulary (`test` / `live`, stable mismatch codes); this is the only place that
 * turns it into Razorpay's or Cashfree's own words.
 */

import type { CollectionsProvider, ConnectionState, MerchantAccountOut } from "@/api/types";
import type { StatusTone } from "@opsgrids/ui";

export const PROVIDER_NAME: Record<CollectionsProvider, string> = {
  razorpay: "Razorpay",
  cashfree: "Cashfree",
  phonepe: "PhonePe",
  payu: "PayU",
  paytm: "Paytm",
};

export function providerName(provider: string | null | undefined): string {
  return PROVIDER_NAME[provider as CollectionsProvider] ?? PROVIDER_NAME.razorpay;
}

/** Providers whose merchant chooses Sandbox or Production (the key does not say which). */
export function choosesEnvironment(provider: string | null | undefined): boolean {
  return provider === "cashfree" || provider === "phonepe" || provider === "payu" || provider === "paytm";
}

/** A mode as the provider's own dashboard names it: Razorpay Test / Live, Cashfree Sandbox /
 *  Production. */
export function modeLabel(provider: string | null | undefined, mode: string | null | undefined): string {
  const live = mode === "live";
  if (choosesEnvironment(provider)) return live ? "Production" : "Sandbox";
  return live ? "Live" : "Test";
}

/** Whether `provider`'s checkouts can be paid for real on this platform. Falls back to the legacy
 *  `wire` field (Razorpay only) for a server that does not send `wires`. */
export function wireFor(account: MerchantAccountOut, provider: CollectionsProvider): "stub" | "live" {
  const declared = account.wires?.[provider];
  if (declared) return declared;
  return provider === "razorpay" && account.wire === "razorpay" ? "live" : "stub";
}

export const CONNECTION_BADGE: Record<ConnectionState, { tone: StatusTone; label: string }> = {
  verified: { tone: "success", label: "Verified" },
  unverified: { tone: "warning", label: "Not checked" },
  failed: { tone: "danger", label: "Check failed" },
};

/** What a stable verification error code means, in a sentence that says what to do. */
export function connectionErrorHint(provider: string | null | undefined, code: string | null | undefined): string {
  const name = providerName(provider);
  if (code === "credentials_rejected") {
    if (provider === "paytm") {
      return `${name} did not accept the MID and merchant key. Check they are for the environment you chose (Sandbox is Paytm's Staging), then replace them.`;
    }
    if (provider === "payu") {
      return `${name} did not accept the merchant key and salt. Check they are for the environment you chose (Sandbox or Production), then replace them.`;
    }
    if (provider === "phonepe") {
      return `${name} did not accept the Client ID, Client Secret and client version. Check they are for the environment you chose (Sandbox or Production), then replace them.`;
    }
    return provider === "cashfree"
      ? `${name} did not accept the App ID and Secret Key. Check they are the keys for the environment you chose (Sandbox or Production), then replace them.`
      : `${name} did not accept the key id and key secret. Check they are a pair, in the same mode, then replace them.`;
  }
  return `${name} refused the check (${code ?? "no code"}). Try again; if it keeps failing, replace the credentials.`;
}

/** Reconciliation differences, in the connected provider's name. */
export function mismatchLabel(provider: string | null | undefined, code: string): string {
  const name = providerName(provider);
  const labels: Record<string, string> = {
    captured_at_provider_only: `Paid at ${name}, not recorded here`,
    not_captured_at_provider: `Recorded as paid here, not paid at ${name}`,
    payment_id_mismatch: "Different payment id",
    amount_mismatch: "Different amount",
    currency_mismatch: "Different currency",
    refund_mismatch: "Different refunded amount",
  };
  return labels[code] ?? code;
}
