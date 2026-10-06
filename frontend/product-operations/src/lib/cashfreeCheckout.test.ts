/**
 * Cashfree's SDK is handed only the payment session id and the environment, opened as a modal,
 * and whatever it resolves with — payment details included — is thrown away: the caller learns
 * only that the modal closed, and asks the server (which asks Cashfree) what happened.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import type { CashfreeOrderCheckoutSessionOut } from "@/api/types";
import { CASHFREE_SDK_SRC, loadCashfree, openCashfreeCheckout, resetCashfreeLoaderForTests } from "./cashfreeCheckout";

const SESSION: CashfreeOrderCheckoutSessionOut = {
  provider: "cashfree",
  payment_session_id: "session_dev_only_0001",
  mode: "sandbox",
  provider_order_id: "9b1d0b7e-1111-4c3a-9d2e-000000000001",
  amount_minor: 374500,
  currency: "INR",
  openable: true,
  order_number: "ORD-2026-0301",
};

function stubCashfree(answer: () => Promise<unknown>) {
  const created: { mode: string }[] = [];
  const checkouts: Record<string, unknown>[] = [];
  window.Cashfree = (options) => {
    created.push(options);
    return {
      checkout: (opts) => {
        checkouts.push({ ...opts });
        return answer();
      },
    };
  };
  return { created, checkouts };
}

afterEach(() => {
  delete window.Cashfree;
  resetCashfreeLoaderForTests();
  document.head.querySelectorAll(`script[src="${CASHFREE_SDK_SRC}"]`).forEach((s) => s.remove());
});

describe("openCashfreeCheckout", () => {
  it("opens a modal with only the session id, in the session's environment, and discards the result", async () => {
    const { created, checkouts } = stubCashfree(() =>
      Promise.resolve({ paymentDetails: { paymentMessage: "Payment finished", cf_payment_id: 3001 } }),
    );
    const closed = await openCashfreeCheckout({ ...SESSION, mode: "production" });
    expect(closed).toBe("completed");
    expect(created).toEqual([{ mode: "production" }]);
    expect(checkouts).toEqual([{ paymentSessionId: "session_dev_only_0001", redirectTarget: "_modal" }]);
  });

  it("reports a modal that closed with an error as dismissed", async () => {
    stubCashfree(() => Promise.resolve({ error: { message: "User closed the popup" } }));
    await expect(openCashfreeCheckout(SESSION)).resolves.toBe("dismissed");
  });

  it("reports an SDK that threw as dismissed, so the server is still asked", async () => {
    stubCashfree(() => Promise.reject(new Error("frame blocked")));
    await expect(openCashfreeCheckout(SESSION)).resolves.toBe("dismissed");
  });

  it("loads Cashfree's own script once, from Cashfree's origin", async () => {
    const pending = loadCashfree();
    const again = loadCashfree();
    const scripts = document.head.querySelectorAll<HTMLScriptElement>(`script[src="${CASHFREE_SDK_SRC}"]`);
    expect(scripts).toHaveLength(1);
    expect(CASHFREE_SDK_SRC).toBe("https://sdk.cashfree.com/js/v3/cashfree.js");
    const factory = vi.fn();
    window.Cashfree = factory;
    scripts[0]?.onload?.(new Event("load"));
    await expect(pending).resolves.toBe(factory);
    await expect(again).resolves.toBe(factory);
  });

  it("says so when the script cannot be loaded, and lets a retry load it again", async () => {
    const pending = loadCashfree();
    const script = document.head.querySelector<HTMLScriptElement>(`script[src="${CASHFREE_SDK_SRC}"]`);
    script?.onerror?.(new Event("error"));
    await expect(pending).rejects.toThrow(/could not be loaded/);
    loadCashfree().catch(() => undefined);
    expect(document.head.querySelectorAll(`script[src="${CASHFREE_SDK_SRC}"]`)).toHaveLength(2);
  });
});
