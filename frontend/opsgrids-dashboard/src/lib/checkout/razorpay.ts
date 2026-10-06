/**
 * Razorpay's hosted Checkout for an OpsGrids invoice — the same pattern as
 * `product-operations/src/lib/razorpayCheckout.ts`.
 *
 * The customer's card, UPI or bank details go into Razorpay's own iframe and never into this app.
 * This module is given the public key id and the Razorpay Order id the server created, and the
 * amount Razorpay shows comes from that Order.
 *
 * **The handler's fields are not evidence of payment.** They are returned to the caller for one
 * purpose — handing them to the server's `/confirm`, which verifies the signature and asks Razorpay
 * itself — and the screen still waits on the server's status read before it says "paid".
 */
import type { CheckoutSession, RazorpayHandlerFields } from "@/api/payment";

import { loadScriptOnce } from "./script";

export const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

interface RazorpayOptions {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  handler: (response: unknown) => void;
  modal: { ondismiss: () => void };
}

interface RazorpayInstance {
  open(): void;
  on(event: "payment.failed", listener: (response: unknown) => void): void;
}

type RazorpayConstructor = new (options: RazorpayOptions) => RazorpayInstance;

function razorpayGlobal(): RazorpayConstructor | undefined {
  return (window as unknown as { Razorpay?: RazorpayConstructor }).Razorpay;
}

/**
 * How Checkout ended — a hint for how long to wait on the server, never what happened.
 *
 * - `completed`: the handler ran; its fields (when well-formed) are for `/confirm` only.
 * - `failed`: an attempt failed and the customer then closed Checkout. Razorpay keeps Checkout
 *   open after a failure so the customer can retry, so a failure alone never ends it.
 * - `dismissed`: closed with no attempt this page saw.
 */
export type RazorpayOutcome =
  | { kind: "completed"; fields: RazorpayHandlerFields | null }
  | { kind: "failed" }
  | { kind: "dismissed" };

function handlerFields(response: unknown): RazorpayHandlerFields | null {
  if (typeof response !== "object" || response === null) return null;
  const r = response as Record<string, unknown>;
  const orderId = r.razorpay_order_id;
  const paymentId = r.razorpay_payment_id;
  const signature = r.razorpay_signature;
  if (typeof orderId !== "string" || typeof paymentId !== "string" || typeof signature !== "string") return null;
  return { razorpay_order_id: orderId, razorpay_payment_id: paymentId, razorpay_signature: signature };
}

export async function openRazorpay(
  session: CheckoutSession,
  action: { keyId: string; orderId: string },
  description: string,
  onOpened?: () => void,
): Promise<RazorpayOutcome> {
  await loadScriptOnce(RAZORPAY_CHECKOUT_SRC, () => razorpayGlobal() !== undefined, "Razorpay");
  const Razorpay = razorpayGlobal();
  if (!Razorpay) throw new Error("Razorpay Checkout did not load.");

  return new Promise<RazorpayOutcome>((resolve) => {
    let settled = false;
    let sawFailure = false;
    const settle = (outcome: RazorpayOutcome) => {
      if (settled) return;
      settled = true;
      resolve(outcome);
    };
    const checkout = new Razorpay({
      key: action.keyId,
      order_id: action.orderId,
      amount: session.amountMinor,
      currency: session.currency,
      name: "OpsGrids",
      description,
      handler: (response) => settle({ kind: "completed", fields: handlerFields(response) }),
      modal: { ondismiss: () => settle(sawFailure ? { kind: "failed" } : { kind: "dismissed" }) },
    });
    // The failure payload (codes, reasons, metadata) is not read: the server's status read says
    // what happened, and Razorpay's own window has already told the customer why.
    checkout.on("payment.failed", () => {
      sawFailure = true;
    });
    checkout.open();
    onOpened?.();
  });
}
