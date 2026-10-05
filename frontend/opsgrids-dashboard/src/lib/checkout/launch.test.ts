/**
 * The provider launchers, against fake SDKs. What matters: only public identifiers go in, the
 * Razorpay handler's fields go to `/confirm` and nowhere else, Cashfree's result is thrown away,
 * and a blocked script is a clear refusal rather than a hang.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("@/api/payment", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/api/payment")>()),
  confirmRazorpay: vi.fn(),
}));

import * as payment from "@/api/payment";
import type { CheckoutSession } from "@/api/payment";

import { CASHFREE_SDK_SRC } from "./cashfree";
import { launchCheckout } from "./launch";
import { RAZORPAY_CHECKOUT_SRC } from "./razorpay";
import { CheckoutUnavailableError, resetScriptLoadsForTests } from "./script";

const RZP_SESSION: CheckoutSession = {
  chargeId: "charge-1",
  provider: "razorpay",
  action: { type: "razorpay_checkout", keyId: "rzp_test_Platform0001", orderId: "order_P1" },
  amountMinor: 499900,
  currency: "INR",
  expiresAt: null,
};

const CF_SESSION: CheckoutSession = {
  chargeId: "charge-2",
  provider: "cashfree",
  action: { type: "cashfree_checkout", paymentSessionId: "session_abc", mode: "sandbox" },
  amountMinor: 100,
  currency: "INR",
  expiresAt: null,
};

type Options = Record<string, unknown> & { handler: (r: unknown) => void; modal: { ondismiss: () => void } };
type Win = Window & { Razorpay?: unknown; Cashfree?: unknown };

function stubRazorpay(behaviour: (options: Options, emit: (event: string) => void) => void): Options[] {
  const seen: Options[] = [];
  class FakeRazorpay {
    private listeners = new Map<string, () => void>();
    constructor(private readonly options: Options) {
      seen.push(options);
    }
    on(event: string, listener: () => void) {
      this.listeners.set(event, listener);
    }
    open() {
      behaviour(this.options, (event) => this.listeners.get(event)?.());
    }
  }
  (window as Win).Razorpay = FakeRazorpay;
  return seen;
}

beforeEach(() => {
  vi.mocked(payment.confirmRazorpay).mockResolvedValue("captured");
});

afterEach(() => {
  delete (window as Win).Razorpay;
  delete (window as Win).Cashfree;
  resetScriptLoadsForTests();
  document.head.querySelectorAll("script").forEach((s) => s.remove());
});

describe("Razorpay", () => {
  it("opens with public identifiers only and sends the handler's fields to confirm, nowhere else", async () => {
    const fields = { razorpay_order_id: "order_P1", razorpay_payment_id: "pay_X", razorpay_signature: "sig" };
    const seen = stubRazorpay((o) => o.handler(fields));

    await expect(launchCheckout(RZP_SESSION, "Invoice INV-1")).resolves.toEqual({ wait: "full" });

    const options = seen[0];
    expect(options?.key).toBe("rzp_test_Platform0001");
    expect(options?.order_id).toBe("order_P1");
    expect(options?.description).toBe("Invoice INV-1");
    expect(JSON.stringify(options)).not.toMatch(/secret/i);
    expect(payment.confirmRazorpay).toHaveBeenCalledExactlyOnceWith("charge-1", fields);
  });

  it("still waits on the server when confirm is refused (400 on a signature mismatch)", async () => {
    vi.mocked(payment.confirmRazorpay).mockRejectedValue(new Error("400"));
    stubRazorpay((o) => o.handler({ razorpay_order_id: "o", razorpay_payment_id: "p", razorpay_signature: "bad" }));
    await expect(launchCheckout(RZP_SESSION, "Invoice INV-1")).resolves.toEqual({ wait: "full" });
  });

  it("does not call confirm when the handler's payload is malformed", async () => {
    stubRazorpay((o) => o.handler({ razorpay_payment_id: 42 }));
    await expect(launchCheckout(RZP_SESSION, "Invoice INV-1")).resolves.toEqual({ wait: "full" });
    expect(payment.confirmRazorpay).not.toHaveBeenCalled();
  });

  it("reports a plain dismissal as a glance", async () => {
    stubRazorpay((o) => o.modal.ondismiss());
    await expect(launchCheckout(RZP_SESSION, "Invoice INV-1")).resolves.toEqual({ wait: "glance", failed: false });
    expect(payment.confirmRazorpay).not.toHaveBeenCalled();
  });

  it("remembers a payment.failed that preceded the dismissal", async () => {
    stubRazorpay((o, emit) => {
      emit("payment.failed");
      o.modal.ondismiss();
    });
    await expect(launchCheckout(RZP_SESSION, "Invoice INV-1")).resolves.toEqual({ wait: "glance", failed: true });
  });

  it("loads checkout.js from Razorpay and refuses clearly when the script is blocked", async () => {
    const launched = launchCheckout(RZP_SESSION, "Invoice INV-1");
    const script = document.head.querySelector("script");
    expect(script?.getAttribute("src")).toBe(RAZORPAY_CHECKOUT_SRC);
    script?.dispatchEvent(new Event("error"));
    await expect(launched).rejects.toBeInstanceOf(CheckoutUnavailableError);
    expect(document.head.querySelector("script")).toBeNull();
  });
});

describe("Cashfree", () => {
  it("opens the modal with the session id and mode, and discards what the SDK reports", async () => {
    const checkout = vi.fn().mockResolvedValue({ paymentDetails: { paymentMessage: "Payment finished" } });
    const factory = vi.fn().mockReturnValue({ checkout });
    (window as Win).Cashfree = factory;

    await expect(launchCheckout(CF_SESSION, "Invoice INV-2")).resolves.toEqual({ wait: "full" });

    expect(factory).toHaveBeenCalledExactlyOnceWith({ mode: "sandbox" });
    expect(checkout).toHaveBeenCalledExactlyOnceWith({ paymentSessionId: "session_abc", redirectTarget: "_modal" });
    expect(payment.confirmRazorpay).not.toHaveBeenCalled();
  });

  it("waits on the server even when the SDK rejects", async () => {
    (window as Win).Cashfree = () => ({ checkout: () => Promise.reject(new Error("closed")) });
    await expect(launchCheckout(CF_SESSION, "Invoice INV-2")).resolves.toEqual({ wait: "full" });
  });

  it("loads the v3 SDK from Cashfree and refuses clearly when the script is blocked", async () => {
    const launched = launchCheckout(CF_SESSION, "Invoice INV-2");
    const script = document.head.querySelector("script");
    expect(script?.getAttribute("src")).toBe(CASHFREE_SDK_SRC);
    script?.dispatchEvent(new Event("error"));
    await expect(launched).rejects.toThrow(/Cashfree's payment window could not be opened/);
  });

  it("treats a script that loads without defining the SDK as blocked", async () => {
    const launched = launchCheckout(CF_SESSION, "Invoice INV-2");
    document.head.querySelector("script")?.dispatchEvent(new Event("load"));
    await expect(launched).rejects.toBeInstanceOf(CheckoutUnavailableError);
  });
});
