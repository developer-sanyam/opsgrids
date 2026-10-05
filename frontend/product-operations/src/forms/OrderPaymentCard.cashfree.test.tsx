/**
 * The order's payment card with a Cashfree account (ADR-0030): it dispatches on the session's
 * provider (Cashfree's modal, never Razorpay's Checkout), asks the server afterwards, and — while
 * the payment is still pending — asks again every few seconds for about a minute, telling the
 * operator not to take payment again. A hidden tab does not ask at all.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { OrderCheckoutOut, OrderCollectionOut, OrderOut, OrderPaymentsOut } from "@/api/types";
import { POLL_INTERVAL_MS, POLL_TICKS } from "@/forms/usePaymentStatusPoll";
import { renderScreen } from "@/test-utils/renderScreen";
import { ApiError } from "@opsgrids/client";
import { OrderPaymentCard } from "./OrderPaymentCard";

const getOrderPayments = vi.fn();
const startOrderCheckout = vi.fn();
const syncOrderPayment = vi.fn();
const openRazorpayCheckout = vi.fn();
const openCashfreeCheckout = vi.fn();

vi.mock("@/api/client", () => ({
  getOrderPayments: (...a: unknown[]) => getOrderPayments(...a),
  startOrderCheckout: (...a: unknown[]) => startOrderCheckout(...a),
  syncOrderPayment: (...a: unknown[]) => syncOrderPayment(...a),
  cancelOrderPayment: vi.fn(),
  refundOrderPayment: vi.fn(),
}));
vi.mock("@/lib/razorpayCheckout", () => ({
  openRazorpayCheckout: (...a: unknown[]) => openRazorpayCheckout(...a),
}));
vi.mock("@/lib/cashfreeCheckout", () => ({
  openCashfreeCheckout: (...a: unknown[]) => openCashfreeCheckout(...a),
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

const COLLECT = "operations.orders.payment.collect:ORG";

async function collect() {
  const user = userEvent.setup({ advanceTimers: (ms) => vi.advanceTimersByTime(ms) });
  renderScreen(<OrderPaymentCard order={ORDER} onOrderChanged={vi.fn()} />, [COLLECT]);
  await user.click(await screen.findByRole("button", { name: "Collect online payment" }));
}

async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

describe("OrderPaymentCard — Cashfree", () => {
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    for (const fn of [getOrderPayments, startOrderCheckout, syncOrderPayment, openRazorpayCheckout, openCashfreeCheckout]) {
      fn.mockReset();
    }
    getOrderPayments.mockResolvedValue(PAYMENTS);
    startOrderCheckout.mockResolvedValue(OPENED);
    openCashfreeCheckout.mockResolvedValue("completed");
  });

  afterEach(() => {
    vi.useRealTimers();
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  });

  it("opens Cashfree's modal with the session — never Razorpay's — then asks the server", async () => {
    syncOrderPayment.mockResolvedValue({ ...PENDING, state: "captured", provider_payment_id: "3001" });
    await collect();
    await waitFor(() => expect(syncOrderPayment).toHaveBeenCalledWith("ord-1", "col-1"));
    expect(openRazorpayCheckout).not.toHaveBeenCalled();
    const [session] = openCashfreeCheckout.mock.calls[0] as [OrderCheckoutOut["checkout"]];
    expect(session?.provider).toBe("cashfree");
    expect(JSON.stringify(session)).not.toMatch(/secret|98200/i);
    // Settled at once: nothing to wait for.
    expect(screen.queryByText(/don't take payment again/)).toBeNull();
  });

  it("keeps asking while pending, says not to take payment again, and stops when it is paid", async () => {
    syncOrderPayment
      .mockResolvedValueOnce(PENDING)
      .mockResolvedValueOnce(PENDING)
      .mockResolvedValue({ ...PENDING, state: "captured", provider_payment_id: "3001" });
    await collect();
    expect(await screen.findByText("Payment pending — don't take payment again")).toBeDefined();
    await advance(POLL_INTERVAL_MS);
    await advance(POLL_INTERVAL_MS);
    await waitFor(() => expect(screen.queryByText(/don't take payment again/)).toBeNull());
    expect(syncOrderPayment).toHaveBeenCalledTimes(3);
    await advance(POLL_INTERVAL_MS * 3);
    expect(syncOrderPayment).toHaveBeenCalledTimes(3);
  });

  it("does not ask while the tab is hidden", async () => {
    syncOrderPayment.mockResolvedValue(PENDING);
    await collect();
    await screen.findByText("Payment pending — don't take payment again");
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
    await advance(POLL_INTERVAL_MS * 5);
    expect(syncOrderPayment).toHaveBeenCalledTimes(1);
    Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
    await advance(POLL_INTERVAL_MS);
    expect(syncOrderPayment).toHaveBeenCalledTimes(2);
  });

  it("gives up after about a minute and says what to do", async () => {
    syncOrderPayment.mockResolvedValue(PENDING);
    await collect();
    await screen.findByText("Payment pending — don't take payment again");
    for (let i = 0; i < POLL_TICKS; i += 1) await advance(POLL_INTERVAL_MS);
    expect(await screen.findByText(/Still pending with Cashfree/)).toBeDefined();
    expect(syncOrderPayment).toHaveBeenCalledTimes(1 + POLL_TICKS);
    await advance(POLL_INTERVAL_MS * 3);
    expect(syncOrderPayment).toHaveBeenCalledTimes(1 + POLL_TICKS);
  });

  it("never retries a 4xx", async () => {
    syncOrderPayment
      .mockResolvedValueOnce(PENDING)
      .mockRejectedValue(new ApiError(404, "gone", null));
    await collect();
    await screen.findByText("Payment pending — don't take payment again");
    await advance(POLL_INTERVAL_MS);
    await advance(POLL_INTERVAL_MS * 3);
    expect(syncOrderPayment).toHaveBeenCalledTimes(2);
  });

  it("never opens a checkout that cannot be paid for real", async () => {
    startOrderCheckout.mockResolvedValue({ ...OPENED, checkout: { ...OPENED.checkout!, openable: false } });
    await collect();
    expect(await screen.findByText(/cannot be paid yet/)).toBeDefined();
    expect(openCashfreeCheckout).not.toHaveBeenCalled();
    expect(syncOrderPayment).not.toHaveBeenCalled();
  });
});
