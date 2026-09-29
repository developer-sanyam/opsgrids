/**
 * Razorpay's own hosted Checkout, opened for an order's online payment (ADR-0024 A4).
 *
 * The customer's card, UPI or bank details are typed into Razorpay's Checkout — an iframe served
 * by Razorpay — and never into this app: nothing here renders a payment field, and nothing here
 * ever sees one. The only things this module is given are the public key id and the Razorpay
 * Order id the server created (with the amount fixed server-side from the order's own total).
 *
 * **The Checkout callback is not evidence of payment.** Razorpay's `handler` receives a payment
 * id and a signature; both are deliberately dropped here and sent nowhere. When Checkout closes —
 * paid, dismissed or failed — the caller asks the server for a status check, and the server asks
 * Razorpay itself, with the merchant's keys. The order is marked paid only by that answer (or by a
 * signed webhook), never by this browser.
 */

import type { OrderCheckoutSessionOut } from "@/api/types";

export const RAZORPAY_CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";

/** The subset of Razorpay Checkout's options this app sets. */
interface RazorpayOptions {
  key: string;
  order_id: string;
  amount: number;
  currency: string;
  name: string;
  description: string;
  prefill?: { name?: string; contact?: string };
  handler: (response: unknown) => void;
  modal: { ondismiss: () => void };
}

interface RazorpayInstance {
  open(): void;
}

type RazorpayConstructor = new (options: RazorpayOptions) => RazorpayInstance;

declare global {
  interface Window {
    Razorpay?: RazorpayConstructor;
  }
}

let loading: Promise<RazorpayConstructor> | null = null;

/** Load Razorpay's script once (a `<script>` from Razorpay's own origin — nothing is bundled). */
export function loadRazorpay(): Promise<RazorpayConstructor> {
  if (window.Razorpay) return Promise.resolve(window.Razorpay);
  if (loading) return loading;
  loading = new Promise<RazorpayConstructor>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = RAZORPAY_CHECKOUT_SRC;
    script.async = true;
    script.onload = () => {
      if (window.Razorpay) resolve(window.Razorpay);
      else reject(new Error("Razorpay Checkout did not load."));
    };
    script.onerror = () => {
      loading = null;
      reject(new Error("Razorpay Checkout could not be loaded. Check the connection and try again."));
    };
    document.head.appendChild(script);
  });
  return loading;
}

/** How Checkout closed. Only a hint for *when* to ask the server — never *what* happened. A
 *  failed attempt does not close Checkout (Razorpay keeps it open so the customer can retry), so
 *  there is no "failed" here: the customer retries, or dismisses it. */
export type CheckoutClosed = "completed" | "dismissed";

/**
 * Open Checkout for `session` and resolve when it closes. The resolution carries no payment data:
 * whatever Razorpay's callback delivered is discarded here, by design.
 */
export async function openRazorpayCheckout(
  session: OrderCheckoutSessionOut,
  { businessName }: { businessName: string },
): Promise<CheckoutClosed> {
  const Razorpay = await loadRazorpay();
  return new Promise<CheckoutClosed>((resolve) => {
    let settled = false;
    const settle = (how: CheckoutClosed) => {
      if (settled) return;
      settled = true;
      resolve(how);
    };
    const checkout = new Razorpay({
      key: session.key_id,
      order_id: session.provider_order_id,
      amount: session.amount_minor,
      currency: session.currency,
      name: businessName,
      description: `Order ${session.order_number}`,
      prefill: {
        ...(session.prefill.name ? { name: session.prefill.name } : {}),
        ...(session.prefill.contact ? { contact: session.prefill.contact } : {}),
      },
      // The payment id and signature Razorpay passes here are intentionally ignored.
      handler: () => settle("completed"),
      modal: { ondismiss: () => settle("dismissed") },
    });
    checkout.open();
  });
}

/** Test seam: forget a cached load so a test can simulate a fresh page. */
export function resetRazorpayLoaderForTests(): void {
  loading = null;
}
