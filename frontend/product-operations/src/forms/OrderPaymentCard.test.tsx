/**
 * The order's payment card (ADR-0024 A4): collecting opens Razorpay's hosted Checkout with only
 * the public key id and the Order id, and — however Checkout closes — asks the server to check
 * with the provider. Nothing the Checkout callback delivers is ever sent anywhere; the screen
 * never marks an order paid on its own. Permission-gated actions stay hidden without the grant.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { OrderCheckoutOut, OrderCollectionOut, OrderOut, OrderPaymentsOut } from "@/api/types";
import { renderScreen } from "@/test-utils/renderScreen";
import { OrderPaymentCard } from "./OrderPaymentCard";

const getOrderPayments = vi.fn();
const startOrderCheckout = vi.fn();
const syncOrderPayment = vi.fn();
const cancelOrderPayment = vi.fn();
const refundOrderPayment = vi.fn();
const openRazorpayCheckout = vi.fn();

vi.mock("@/api/client", () => ({
  getOrderPayments: (...a: unknown[]) => getOrderPayments(...a),
  startOrderCheckout: (...a: unknown[]) => startOrderCheckout(...a),
  syncOrderPayment: (...a: unknown[]) => syncOrderPayment(...a),
  cancelOrderPayment: (...a: unknown[]) => cancelOrderPayment(...a),
  refundOrderPayment: (...a: unknown[]) => refundOrderPayment(...a),
}));
vi.mock("@/lib/razorpayCheckout", () => ({
  openRazorpayCheckout: (...a: unknown[]) => openRazorpayCheckout(...a),
}));

const ORDER: OrderOut = {
  id: "ord-1",
  number: "ORD-2026-0301",
  channel: "phone",
  status: "ACCEPTED",
  version: 3,
  warehouse_id: "wh-1",
  customer_id: null,
  customer_name: "Rohan Malhotra",
  customer_phone: "+91 98200 11122",
  subtotal_minor: 374500,
  total_minor: 374500,
  currency: "INR",
  lines: [],
  created_at: "2026-09-24T09:15:00Z",
  updated_at: "2026-09-24T09:15:00Z",
};

const COLLECTION: OrderCollectionOut = {
  id: "col-1",
  state: "created",
  amount_minor: 374500,
  currency: "INR",
  provider: "razorpay",
  provider_order_id: "order_R1",
  provider_payment_id: null,
  failure_reason: null,
  refunded_minor: 0,
  refunds: [],
  history: [],
  created_at: "2026-09-29T10:00:00Z",
  updated_at: "2026-09-29T10:00:00Z",
};

function payments(over: Partial<OrderPaymentsOut> = {}): OrderPaymentsOut {
  return {
    order_id: "ord-1",
    total_minor: 374500,
    currency: "INR",
    paid_at: null,
    paid_via_charge_id: null,
    payment_term: "prepaid",
    can_collect: true,
    collect_blocked_reason: null,
    online_payments_configured: true,
    mode: "test",
    wire: "razorpay",
    collections: [],
    ...over,
  };
}

const OPENED: OrderCheckoutOut = {
  collection: COLLECTION,
  checkout: {
    provider: "razorpay",
    key_id: "rzp_test_Merchant0001",
    provider_order_id: "order_R1",
    amount_minor: 374500,
    currency: "INR",
    mode: "test",
    openable: true,
    order_number: "ORD-2026-0301",
    prefill: { name: "Rohan Malhotra", contact: "+91 98200 11122" },
  },
};

const COLLECT = "operations.orders.payment.collect:ORG";

describe("OrderPaymentCard", () => {
  beforeEach(() => {
    for (const fn of [getOrderPayments, startOrderCheckout, syncOrderPayment, cancelOrderPayment, refundOrderPayment, openRazorpayCheckout]) {
      fn.mockReset();
    }
  });

  it("opens Razorpay's checkout, then asks the server to check with the provider", async () => {
    const user = userEvent.setup();
    getOrderPayments.mockResolvedValue(payments());
    startOrderCheckout.mockResolvedValue(OPENED);
    // Whatever Checkout's callback says is dropped by the loader; the card only learns it closed.
    openRazorpayCheckout.mockResolvedValue("completed");
    syncOrderPayment.mockResolvedValue({ ...COLLECTION, state: "captured", provider_payment_id: "pay_1" });
    const onOrderChanged = vi.fn();
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={onOrderChanged} />, [COLLECT]);

    await user.click(await screen.findByRole("button", { name: "Collect online payment" }));

    await waitFor(() => expect(syncOrderPayment).toHaveBeenCalledWith("ord-1", "col-1"));
    // No amount leaves the browser: the server takes the order's own total.
    expect(startOrderCheckout).toHaveBeenCalledWith("ord-1");
    const [session] = openRazorpayCheckout.mock.calls[0] as [OrderCheckoutOut["checkout"]];
    expect(session?.key_id).toBe("rzp_test_Merchant0001");
    expect(JSON.stringify(session)).not.toMatch(/secret/i);
    // The status check sends only the order and the collection — never payment data.
    expect(syncOrderPayment.mock.calls[0]).toHaveLength(2);
    await waitFor(() => expect(onOrderChanged).toHaveBeenCalled());
  });

  it("shows an empty state, not an error, for an order with no payment recorded yet", async () => {
    getOrderPayments.mockResolvedValue(
      payments({ can_collect: false, collect_blocked_reason: "Online payments are not set up for this organization" }),
    );
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);

    expect(await screen.findByText("No payment recorded yet")).toBeDefined();
    expect(screen.getByText("Online payments are not set up for this organization")).toBeDefined();
    expect(screen.queryByText(/something went wrong/i)).toBeNull();
  });

  it("points at the collect action in the empty state when the order can be paid online", async () => {
    getOrderPayments.mockResolvedValue(payments());
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);

    expect(await screen.findByText("No payment recorded yet")).toBeDefined();
    expect(screen.getByRole("button", { name: "Collect online payment" })).toBeDefined();
  });

  it("never opens a checkout that cannot be paid for real", async () => {
    const user = userEvent.setup();
    getOrderPayments.mockResolvedValue(payments({ wire: "stub" }));
    startOrderCheckout.mockResolvedValue({ ...OPENED, checkout: { ...OPENED.checkout!, openable: false } });
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);

    await user.click(await screen.findByRole("button", { name: "Collect online payment" }));

    expect(await screen.findByText(/cannot be paid yet/)).toBeDefined();
    expect(openRazorpayCheckout).not.toHaveBeenCalled();
    expect(syncOrderPayment).not.toHaveBeenCalled();
  });

  it("hides collect without the permission, and says why when an order cannot be paid online", async () => {
    getOrderPayments.mockResolvedValue(payments());
    const { unmount } = renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [
      "operations.orders.order.read:ORG",
    ]);
    await screen.findByText(/Prepaid/);
    expect(screen.queryByRole("button", { name: "Collect online payment" })).toBeNull();
    unmount();

    getOrderPayments.mockResolvedValue(
      payments({ can_collect: false, collect_blocked_reason: "Order ORD-2026-0301 is on credit terms" }),
    );
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);
    expect(await screen.findByText(/on credit terms/)).toBeDefined();
    expect(screen.queryByRole("button", { name: "Collect online payment" })).toBeNull();
  });

  it("shows a paid order as paid online, and offers a refund only with the refund grant", async () => {
    const paid = { ...COLLECTION, state: "captured" as const, provider_payment_id: "pay_1" };
    getOrderPayments.mockResolvedValue(
      payments({
        paid_at: "2026-09-29T10:05:00Z",
        paid_via_charge_id: "col-1",
        can_collect: false,
        collect_blocked_reason: "Order ORD-2026-0301 is already paid",
        collections: [paid],
      }),
    );
    const { unmount } = renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);
    expect(await screen.findByText("Paid online")).toBeDefined();
    expect(screen.getByText("pay_1")).toBeDefined();
    expect(screen.queryByRole("button", { name: "Refund" })).toBeNull();
    unmount();

    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [
      COLLECT,
      "operations.orders.payment.refund:ORG",
    ]);
    expect(await screen.findByRole("button", { name: "Refund" })).toBeDefined();
  });

  it("sends a refund with one idempotency key per refund dialog", async () => {
    const user = userEvent.setup();
    const paid = { ...COLLECTION, state: "captured" as const, provider_payment_id: "pay_1" };
    getOrderPayments.mockResolvedValue(payments({ paid_at: "2026-09-29T10:05:00Z", paid_via_charge_id: "col-1", can_collect: false, collections: [paid] }));
    refundOrderPayment.mockResolvedValue({ ...paid, refunded_minor: 100000 });
    renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [
      "operations.orders.payment.refund:ORG",
    ]);

    await user.click(await screen.findByRole("button", { name: "Refund" }));
    const amount = screen.getByLabelText(/Amount/);
    await user.clear(amount);
    await user.type(amount, "1000");
    await user.type(screen.getByLabelText("Reason"), "Damaged in transit");
    await user.click(screen.getByRole("button", { name: "Send refund" }));

    await waitFor(() => expect(refundOrderPayment).toHaveBeenCalledTimes(1));
    const [orderId, chargeId, body, key] = refundOrderPayment.mock.calls[0] as [string, string, unknown, string];
    expect([orderId, chargeId]).toEqual(["ord-1", "col-1"]);
    expect(body).toEqual({ amount_minor: 100000, reason: "Damaged in transit" });
    expect(key.length).toBeGreaterThanOrEqual(8);
  });
});
