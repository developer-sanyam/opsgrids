/**
 * The Checkout loader hands Razorpay only the public key id and the server's Order id, and throws
 * away whatever Razorpay's callback delivers (payment id, signature): the caller learns only that
 * Checkout closed, and asks the server — which asks Razorpay — what happened.
 */
import { afterEach, describe, expect, it } from "vitest";

import type { OrderCheckoutSessionOut } from "@/api/types";
import { openRazorpayCheckout, resetRazorpayLoaderForTests } from "./razorpayCheckout";

const SESSION: OrderCheckoutSessionOut = {
  provider: "razorpay",
  key_id: "rzp_test_Merchant0001",
  provider_order_id: "order_R1",
  amount_minor: 374500,
  currency: "INR",
  mode: "test",
  openable: true,
  order_number: "ORD-2026-0301",
  prefill: { name: "Rohan Malhotra", contact: "+91 98200 11122" },
};

type Options = Record<string, unknown> & {
  handler: (r: unknown) => void;
  modal: { ondismiss: () => void };
};

function stubRazorpay(behaviour: (options: Options) => void): { seen: Options[] } {
  const seen: Options[] = [];
  class FakeRazorpay {
    constructor(private readonly options: Options) {
      seen.push(options);
    }
    open() {
      behaviour(this.options);
    }
  }
  window.Razorpay = FakeRazorpay as unknown as NonNullable<typeof window.Razorpay>;
  return { seen };
}

afterEach(() => {
  delete window.Razorpay;
  resetRazorpayLoaderForTests();
});

describe("openRazorpayCheckout", () => {
  it("passes only public identifiers and resolves without the callback's payment data", async () => {
    const { seen } = stubRazorpay((o) =>
      o.handler({ razorpay_payment_id: "pay_X", razorpay_order_id: "order_R1", razorpay_signature: "sig" }),
    );
    const closed = await openRazorpayCheckout(SESSION, { businessName: "Order payment" });
    expect(closed).toBe("completed");
    const options = seen[0];
    expect(options?.key).toBe("rzp_test_Merchant0001");
    expect(options?.order_id).toBe("order_R1");
    expect(options?.amount).toBe(374500);
    expect(JSON.stringify(options)).not.toMatch(/secret/i);
  });

  it("reports a dismissed checkout as dismissed", async () => {
    stubRazorpay((o) => o.modal.ondismiss());
    await expect(openRazorpayCheckout(SESSION, { businessName: "Order payment" })).resolves.toBe("dismissed");
  });
});
