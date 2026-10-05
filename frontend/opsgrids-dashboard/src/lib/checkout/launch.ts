/**
 * Open the provider's payment window for a checkout, by the `client_action` the server chose.
 *
 * The SDK is inherently provider-specific, so this is the one place the dashboard branches on the
 * provider (recorded against ADR-0004 §6 in the design doc). Both branches end the same way: the
 * caller is told how long it is worth waiting on the server, and nothing else.
 */
import { confirmRazorpay } from "@/api/payment";
import type { CheckoutSession } from "@/api/payment";

import { openCashfree } from "./cashfree";
import { openRazorpay } from "./razorpay";

/**
 * - `wait`: a payment may be under way — poll the server for the full window.
 * - `glance`: the customer closed the window without paying, as far as the browser saw — ask the
 *   server once, in case a UPI app finished the payment out of sight.
 */
export type LaunchOutcome = { wait: "full" } | { wait: "glance"; failed: boolean };

export async function launchCheckout(session: CheckoutSession, description: string): Promise<LaunchOutcome> {
  const action = session.action;
  if (action.type === "cashfree_checkout") {
    await openCashfree(action);
    return { wait: "full" };
  }

  const outcome = await openRazorpay(session, action, description);
  if (outcome.kind !== "completed") return { wait: "glance", failed: outcome.kind === "failed" };
  if (outcome.fields) {
    try {
      await confirmRazorpay(session.chargeId, outcome.fields);
    } catch {
      // A refused or failed confirm proves nothing either way (a 400 is a signature mismatch,
      // which the server has already declined to act on); the status read below decides.
    }
  }
  return { wait: "full" };
}
