/**
 * The order's payment card with a redirect-checkout provider (PhonePe): collecting shows a button
 * that opens the provider's page in a new tab — a real click, never an automatic navigation — and
 * the card starts asking the server, which asks the provider, what happened. A POST-shaped session
 * renders a form that posts its fields to the provider in a new tab.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { OrderCollectionOut, OrderOut, OrderPaymentsOut, RedirectOrderCheckoutSessionOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { OrderPaymentCard } from "./OrderPaymentCard";
import { RedirectCheckout } from "./RedirectCheckout";

const getOrderPayments = vi.fn();
const startOrderCheckout = vi.fn();
const syncOrderPayment = vi.fn();

vi.mock("@/api/client", () => ({
  getOrderPayments: (...a: unknown[]) => getOrderPayments(...a),
  startOrderCheckout: (...a: unknown[]) => startOrderCheckout(...a),
  syncOrderPayment: (...a: unknown[]) => syncOrderPayment(...a),
  cancelOrderPayment: vi.fn(),
  refundOrderPayment: vi.fn(),
}));

const COLLECT = "operations.orders.payment.collect:ORG";
const URL = "https://mercury-uat.phonepe.example/transact/uat_v3?token=dev-only";

const ORDER = {
  id: "ord-1",
  number: "ORD-2026-0301",
  currency: "INR",
  total_minor: 49_999,
  version: 1,
} as OrderOut;

const COLLECTION: OrderCollectionOut = {
  id: "col-1",
  state: "created",
  amount_minor: 49_999,
  currency: "INR",
  provider: "phonepe",
  provider_order_id: "col-1",
  provider_payment_id: null,
  failure_reason: null,
  refunded_minor: 0,
  refunds: [],
  history: [],
  created_at: "2026-10-16T10:00:00Z",
  updated_at: "2026-10-16T10:00:00Z",
};

const PAYMENTS: OrderPaymentsOut = {
  order_id: "ord-1",
  total_minor: 49_999,
  currency: "INR",
  paid_at: null,
  paid_via_charge_id: null,
  payment_term: "prepaid",
  can_collect: true,
  collect_blocked_reason: null,
  online_payments_configured: true,
  mode: "test",
  wire: "stub",
  provider: "phonepe",
  provider_wire: "live",
  collections: [],
};

const SESSION: RedirectOrderCheckoutSessionOut = {
  provider: "phonepe",
  method: "GET",
  url: URL,
  fields: {},
  provider_order_id: "col-1",
  amount_minor: 49_999,
  currency: "INR",
  openable: true,
  order_number: "ORD-2026-0301",
};

describe("OrderPaymentCard — redirect checkout", () => {
  const open = vi.fn();
  beforeEach(() => {
    for (const fn of [getOrderPayments, startOrderCheckout, syncOrderPayment, open]) fn.mockReset();
    vi.stubGlobal("open", open);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("offers PhonePe's page in a new tab on a click and starts asking the provider", async () => {
    const user = userEvent.setup();
    getOrderPayments.mockResolvedValue(PAYMENTS);
    startOrderCheckout.mockResolvedValue({ collection: COLLECTION, checkout: SESSION });
    syncOrderPayment.mockResolvedValue(COLLECTION);
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);

    await user.click(await screen.findByRole("button", { name: "Collect online payment" }));
    const button = await screen.findByRole("button", { name: "Open PhonePe checkout" });
    expect(open).not.toHaveBeenCalled(); // never navigated without the operator's click
    expect(screen.getByText(/Pay .*499\.99 on PhonePe/)).toBeDefined();
    await user.click(button);
    expect(open).toHaveBeenCalledWith(URL, "_blank", "noopener,noreferrer");
    // The card is now watching the payment (it asks the server every few seconds).
    expect(screen.getByText("Payment pending — don't take payment again")).toBeDefined();
  });

  it("posts a form-shaped session's fields to the provider in a new tab", () => {
    render(
      <RedirectCheckout
        session={{ ...SESSION, method: "POST", url: "https://test.payu.example/_payment", fields: { txnid: "col-1" } }}
      />,
    );
    const form = screen.getByRole("button", { name: "Open PhonePe checkout" }).closest("form");
    expect(form?.getAttribute("method")).toBe("post");
    expect(form?.getAttribute("action")).toBe("https://test.payu.example/_payment");
    expect(form?.getAttribute("target")).toBe("_blank");
    expect((form?.querySelector('input[name="txnid"]') as HTMLInputElement).value).toBe("col-1");
  });
});
