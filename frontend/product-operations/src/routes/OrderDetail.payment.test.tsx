/**
 * The payment card inside the real order page (ADR-0030): when a payment changes, the page
 * refreshes the order **without unmounting the card**, so a payment still pending keeps being
 * asked about, "don't take payment again" stays up, and an error the operator has not read yet —
 * a missing customer phone, a checkout that could not load — stays on screen. The card's own tests
 * render it alone with a parent that never reloads, which is exactly what hid this.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { OrderCheckoutOut, OrderCollectionOut, OrderOut, OrderPaymentsOut } from "@/api/types";
import { POLL_INTERVAL_MS } from "@/forms/usePaymentStatusPoll";
import { CheckoutLoadError } from "@/lib/checkoutErrors";
import { renderScreen } from "@/test-utils/renderScreen";
import { ApiError } from "@opsgrids/client";
import { OrderDetailPage } from "./OrderDetail";

const getOrder = vi.fn();
const getOrderPayments = vi.fn();
const startOrderCheckout = vi.fn();
const syncOrderPayment = vi.fn();
const openCashfreeCheckout = vi.fn();

vi.mock("@/api/client", () => ({
  getOrder: (...a: unknown[]) => getOrder(...a),
  getWarehouseById: () => Promise.resolve({ id: "wh-1", code: "WH-PUN", name: "Pune Plant", location: "" }),
  quoteOrder: vi.fn(),
  placeOrder: vi.fn(),
  acceptOrder: vi.fn(),
  holdOrder: vi.fn(),
  releaseOrder: vi.fn(),
  cancelOrder: vi.fn(),
  getOrderPayments: (...a: unknown[]) => getOrderPayments(...a),
  startOrderCheckout: (...a: unknown[]) => startOrderCheckout(...a),
  syncOrderPayment: (...a: unknown[]) => syncOrderPayment(...a),
  cancelOrderPayment: vi.fn(),
  refundOrderPayment: vi.fn(),
}));
vi.mock("@/lib/razorpayCheckout", () => ({ openRazorpayCheckout: vi.fn() }));
vi.mock("@/lib/cashfreeCheckout", () => ({
  openCashfreeCheckout: (...a: unknown[]) => openCashfreeCheckout(...a),
}));

const COLLECT = "operations.orders.payment.collect:ORG";
const PENDING_TITLE = "Payment pending — don't take payment again";

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

const PENDING: OrderCollectionOut = {
  id: "col-1",
  state: "created",
  amount_minor: 374500,
  currency: "INR",
  provider: "cashfree",
  provider_order_id: "col-1",
  provider_payment_id: null,
  failure_reason: null,
  refunded_minor: 0,
  refunds: [],
  history: [],
  created_at: "2026-10-06T10:00:00Z",
  updated_at: "2026-10-06T10:00:00Z",
};

const PAYMENTS: OrderPaymentsOut = {
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
  wire: "stub",
  provider: "cashfree",
  provider_wire: "live",
  collections: [],
};

const OPENED: OrderCheckoutOut = {
  collection: PENDING,
  checkout: {
    provider: "cashfree",
    payment_session_id: "session_dev_only_0001",
    mode: "sandbox",
    provider_order_id: "col-1",
    amount_minor: 374500,
    currency: "INR",
    openable: true,
    order_number: "ORD-2026-0301",
  },
};

/** The order as the server answers it a little later, with its version moved on — what a real
 *  refresh after a payment change looks like. */
function slowOrder() {
  let version = ORDER.version;
  return () =>
    new Promise<OrderOut>((resolve) => {
      const answer = { ...ORDER, version };
      version += 1;
      window.setTimeout(() => resolve(answer), 100);
    });
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

async function openPageAndCollect() {
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  renderScreen(<OrderDetailPage />, [COLLECT], { path: "/orders/:id", at: "/orders/ord-1" });
  await advance(150);
  await user.click(await screen.findByRole("button", { name: "Collect online payment" }));
}

describe("OrderDetailPage — the payment card through a refresh", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    for (const fn of [getOrder, getOrderPayments, startOrderCheckout, syncOrderPayment, openCashfreeCheckout]) {
      fn.mockReset();
    }
    getOrder.mockImplementation(slowOrder());
    getOrderPayments.mockResolvedValue(PAYMENTS);
    startOrderCheckout.mockResolvedValue(OPENED);
    openCashfreeCheckout.mockResolvedValue("completed");
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("keeps asking about a pending payment, and keeps the warning up, after the order refreshes", async () => {
    syncOrderPayment.mockResolvedValue(PENDING);
    await openPageAndCollect();
    expect(await screen.findByText(PENDING_TITLE)).toBeDefined();
    // The order is fetched again in the background, and comes back at a new version.
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));
    await advance(150);
    expect(screen.getByText(PENDING_TITLE)).toBeDefined();
    for (let tick = 0; tick < 3; tick += 1) {
      await advance(POLL_INTERVAL_MS);
      expect(screen.getByText(PENDING_TITLE)).toBeDefined();
    }
    expect(syncOrderPayment).toHaveBeenCalledTimes(4);
  });

  it("keeps a refusal the operator must act on on screen after the order refreshes", async () => {
    startOrderCheckout.mockRejectedValue(
      new ApiError(422, "phone", {
        type: "https://errors.opsgrids.dev/phone-required",
        title: "A customer phone number is required",
        status: 422,
        detail: "Add the customer's phone number to this order to take an online payment",
      }),
    );
    await openPageAndCollect();
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));
    await advance(150);
    expect(await screen.findByText(/Add the customer's phone number to this order/)).toBeDefined();
    expect(openCashfreeCheckout).not.toHaveBeenCalled();
  });

  it("keeps the 'checkout could not load' error after the order refreshes", async () => {
    openCashfreeCheckout.mockRejectedValue(
      new CheckoutLoadError("Cashfree's checkout could not be loaded. Check the connection, or an ad blocker."),
    );
    await openPageAndCollect();
    await waitFor(() => expect(getOrder).toHaveBeenCalledTimes(2));
    await advance(150);
    expect(await screen.findByText(/checkout could not be loaded/)).toBeDefined();
  });
});
