/**
 * Cashfree's own hosted checkout, opened in a modal for an order's online payment (ADR-0030).
 *
 * The customer's card, UPI or bank details are typed into Cashfree's checkout — served by
 * Cashfree — and never into this app. The only thing this module is given is the
 * `payment_session_id` the server fetched from Cashfree for this one response (it is never
 * stored anywhere), and the environment the SDK is created for.
 *
 * **The SDK's result is not evidence of payment.** `checkout()` resolves with whatever happened in
 * the modal — an error, a redirect, payment details — and all of it is discarded here, unread
 * beyond "did the modal report an error": it is a hint for *when* to ask the server, never *what*
 * to tell it. The caller then asks the server for a status check, and the server asks Cashfree,
 * with the merchant's own keys. The order is marked paid only by that answer (or by a signed
 * webhook), never by this browser.
 */

import type { CashfreeOrderCheckoutSessionOut } from "@/api/types";
import type { CheckoutClosed } from "@/lib/razorpayCheckout";
import { CheckoutLoadError } from "@/lib/checkoutErrors";

export const CASHFREE_SDK_SRC = "https://sdk.cashfree.com/js/v3/cashfree.js";

/** The subset of the v3 SDK this app uses. */
interface CashfreeCheckoutOptions {
  paymentSessionId: string;
  redirectTarget: "_modal";
}

interface CashfreeInstance {
  checkout(options: CashfreeCheckoutOptions): Promise<unknown>;
}

type CashfreeFactory = (options: { mode: "sandbox" | "production" }) => CashfreeInstance;

declare global {
  interface Window {
    Cashfree?: CashfreeFactory;
  }
}

let loading: Promise<CashfreeFactory> | null = null;

/** Load Cashfree's SDK once (a `<script>` from Cashfree's own origin — nothing is bundled). */
export function loadCashfree(): Promise<CashfreeFactory> {
  if (window.Cashfree) return Promise.resolve(window.Cashfree);
  if (loading) return loading;
  loading = new Promise<CashfreeFactory>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = CASHFREE_SDK_SRC;
    script.async = true;
    script.onload = () => {
      if (window.Cashfree) resolve(window.Cashfree);
      else {
        loading = null;
        reject(new CheckoutLoadError("Cashfree's checkout did not load."));
      }
    };
    script.onerror = () => {
      loading = null;
      reject(new CheckoutLoadError("Cashfree's checkout could not be loaded. Check the connection, or an ad blocker, and try again."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** Whether the modal reported an error (it was closed, or the attempt failed there) — read as a
 *  boolean and thrown away with everything else the SDK returned. */
function reportedError(result: unknown): boolean {
  return typeof result === "object" && result !== null && "error" in result && Boolean((result as { error?: unknown }).error);
}

/**
 * Open the checkout for `session` and resolve when the modal closes. The resolution carries no
 * payment data: whatever Cashfree's SDK delivered is discarded here, by design.
 */
export async function openCashfreeCheckout(session: CashfreeOrderCheckoutSessionOut): Promise<CheckoutClosed> {
  const create = await loadCashfree();
  const cashfree = create({ mode: session.mode });
  try {
    const result = await cashfree.checkout({ paymentSessionId: session.payment_session_id, redirectTarget: "_modal" });
    return reportedError(result) ? "dismissed" : "completed";
  } catch {
    // The SDK threw (a blocked frame, a closed session): nothing is known — ask the server anyway.
    return "dismissed";
  }
}

/** Test seam: forget a cached load so a test can simulate a fresh page. */
export function resetCashfreeLoaderForTests(): void {
  loading = null;
}
