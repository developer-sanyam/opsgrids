/**
 * The one mapping from payment-service's checkout shapes, and the one reading of its refusals,
 * against the contract in libs/contracts/openapi/payment.json. Strict on purpose: anything the
 * contract does not promise is refused, never guessed at.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import {
  CASHFREE_CHECKOUT_WIRE,
  checkoutProblem,
  frameworkProblem,
  RAZORPAY_CHECKOUT_WIRE,
  RAZORPAY_TEST_CHECKOUT_WIRE,
  refusalMessage,
  statusWire,
  STUB_RAZORPAY_CHECKOUT_WIRE,
  withoutTestMode,
} from "@/test-utils/payment";

import { checkoutRefusal, confirmRazorpay, createCheckout, fromWire, getCheckoutStatus, PaymentContractError } from "./payment";

describe("fromWire — PlatformCheckoutOut", () => {
  it("maps a Razorpay checkout to the public key id and order id only", () => {
    expect(fromWire("checkout", RAZORPAY_CHECKOUT_WIRE)).toEqual({
      chargeId: RAZORPAY_CHECKOUT_WIRE.charge_id,
      provider: "razorpay",
      openable: true,
      action: { type: "razorpay_checkout", keyId: "rzp_live_Platform0001", orderId: "order_P1" },
      amountMinor: 499900,
      currency: "INR",
      expiresAt: "2026-10-07T12:30:00Z",
      testMode: false,
    });
  });

  it("maps a Cashfree checkout to its session id and mode", () => {
    expect(fromWire("checkout", CASHFREE_CHECKOUT_WIRE)).toMatchObject({
      openable: true,
      action: { type: "cashfree_checkout", paymentSessionId: "session_abc", mode: "sandbox" },
    });
  });

  it("maps a stub provider's checkout (openable: false, empty key id) to nothing to launch", () => {
    // `RazorpayProvider.ensure_checkout` on the stub: `key_id` is "" and the Order a stand-in.
    expect(fromWire("checkout", STUB_RAZORPAY_CHECKOUT_WIRE)).toEqual({
      chargeId: RAZORPAY_CHECKOUT_WIRE.charge_id,
      provider: "razorpay",
      openable: false,
      action: null,
      amountMinor: 499900,
      currency: "INR",
      expiresAt: "2026-10-07T12:30:00Z",
      testMode: false,
    });
    // The stub Cashfree answers a sandbox mode and a stand-in session; nothing is launched on it either.
    const stubCashfree = { ...CASHFREE_CHECKOUT_WIRE, client_action: { ...CASHFREE_CHECKOUT_WIRE.client_action, openable: false } };
    expect(fromWire("checkout", stubCashfree)).toMatchObject({ openable: false, action: null });
  });

  it.each([
    ["a fractional amount", { ...RAZORPAY_CHECKOUT_WIRE, amount_minor: 4999.5 }],
    ["an amount sent as a string", { ...RAZORPAY_CHECKOUT_WIRE, amount_minor: "499900" }],
    ["a negative amount", { ...RAZORPAY_CHECKOUT_WIRE, amount_minor: -1 }],
    ["an amount past the safe-integer range", { ...RAZORPAY_CHECKOUT_WIRE, amount_minor: 2 ** 53 }],
    ["a currency that is not ISO-4217", { ...RAZORPAY_CHECKOUT_WIRE, currency: "inr" }],
    ["a provider this page does not know", { ...RAZORPAY_CHECKOUT_WIRE, provider: "stripe" }],
    ["an action that does not match its provider", { ...RAZORPAY_CHECKOUT_WIRE, provider: "cashfree" }],
    ["a stub action that does not match its provider", { ...STUB_RAZORPAY_CHECKOUT_WIRE, provider: "cashfree" }],
    ["something that is not a public key id", { ...RAZORPAY_CHECKOUT_WIRE, client_action: { ...RAZORPAY_CHECKOUT_WIRE.client_action, key_id: "s3cr3tvalue" } }],
    ["an openable Razorpay action with an empty key id", { ...RAZORPAY_CHECKOUT_WIRE, client_action: { ...RAZORPAY_CHECKOUT_WIRE.client_action, key_id: "" } }],
    ["a Cashfree mode outside the two it has", { ...CASHFREE_CHECKOUT_WIRE, client_action: { ...CASHFREE_CHECKOUT_WIRE.client_action, mode: "test" } }],
    ["a missing client action", { ...RAZORPAY_CHECKOUT_WIRE, client_action: null }],
    ["a client action without openable", { ...RAZORPAY_CHECKOUT_WIRE, client_action: { type: "razorpay_checkout", key_id: "rzp_test_Platform0001", order_id: "order_P1" } }],
    ["an openable that is not a boolean", { ...RAZORPAY_CHECKOUT_WIRE, client_action: { ...RAZORPAY_CHECKOUT_WIRE.client_action, openable: "true" } }],
    ["a null expires_at (required and never null on a checkout)", { ...RAZORPAY_CHECKOUT_WIRE, expires_at: null }],
    ["a missing charge_id", { ...RAZORPAY_CHECKOUT_WIRE, charge_id: undefined }],
    ["no body at all", null],
  ])("refuses %s", (_label, raw) => {
    expect(() => fromWire("checkout", raw)).toThrow(PaymentContractError);
  });
});

describe("fromWire — test_mode", () => {
  it("reads test_mode: true on test keys and in Cashfree's sandbox", () => {
    expect(fromWire("checkout", RAZORPAY_TEST_CHECKOUT_WIRE)).toMatchObject({
      testMode: true,
      action: { type: "razorpay_checkout", keyId: "rzp_test_Platform0001" },
    });
    expect(fromWire("checkout", CASHFREE_CHECKOUT_WIRE)).toMatchObject({ testMode: true, action: { mode: "sandbox" } });
  });

  it("reads test_mode: false on live keys and on a stub", () => {
    expect(fromWire("checkout", RAZORPAY_CHECKOUT_WIRE).testMode).toBe(false);
    expect(fromWire("checkout", STUB_RAZORPAY_CHECKOUT_WIRE).testMode).toBe(false);
    const production = { ...CASHFREE_CHECKOUT_WIRE, client_action: { ...CASHFREE_CHECKOUT_WIRE.client_action, mode: "production" }, test_mode: false };
    expect(fromWire("checkout", production).testMode).toBe(false);
  });

  it("reads a checkout from a server that predates test_mode as not in test mode, even on a test key", () => {
    expect(fromWire("checkout", withoutTestMode(RAZORPAY_TEST_CHECKOUT_WIRE))).toMatchObject({ openable: true, testMode: false });
    expect(fromWire("checkout", withoutTestMode(CASHFREE_CHECKOUT_WIRE))).toMatchObject({ openable: true, testMode: false });
    expect(fromWire("checkout", withoutTestMode(STUB_RAZORPAY_CHECKOUT_WIRE))).toMatchObject({ openable: false, testMode: false });
  });

  it("accepts a test key with test_mode: false — the notice is only left out", () => {
    expect(fromWire("checkout", { ...RAZORPAY_TEST_CHECKOUT_WIRE, test_mode: false })).toMatchObject({ openable: true, testMode: false });
  });

  it("reads test_mode on a status read, and its absence as false", () => {
    expect(fromWire("status", statusWire("captured", { test_mode: true })).testMode).toBe(true);
    expect(fromWire("status", statusWire("captured")).testMode).toBe(false);
    expect(fromWire("status", withoutTestMode(statusWire("authorized", { test_mode: true }))).testMode).toBe(false);
  });

  it.each([
    ["test_mode sent as a string", { ...RAZORPAY_TEST_CHECKOUT_WIRE, test_mode: "true" }],
    ["test_mode sent as null", { ...RAZORPAY_TEST_CHECKOUT_WIRE, test_mode: null }],
    ["test_mode: true on a live Razorpay key", { ...RAZORPAY_CHECKOUT_WIRE, test_mode: true }],
    [
      "test_mode: true on Cashfree's production mode",
      { ...CASHFREE_CHECKOUT_WIRE, client_action: { ...CASHFREE_CHECKOUT_WIRE.client_action, mode: "production" }, test_mode: true },
    ],
  ])("refuses a checkout with %s — never 'no real money' over a window that takes it", (_label, raw) => {
    expect(() => fromWire("checkout", raw)).toThrow(PaymentContractError);
  });

  it.each([
    ["sent as a string", statusWire("created", { test_mode: "false" })],
    ["sent as null", statusWire("created", { test_mode: null })],
  ])("refuses a status with test_mode %s", (_label, raw) => {
    expect(() => fromWire("status", raw)).toThrow(PaymentContractError);
  });
});

describe("fromWire — PlatformCheckoutStatusOut", () => {
  it("maps every field of a status read", () => {
    expect(fromWire("status", statusWire("authorized", { provider: "cashfree", provider_checked: false }))).toEqual({
      chargeId: "6f1c1d1e-0000-4000-8000-000000000001",
      provider: "cashfree",
      state: "authorized",
      paid: false,
      amountMinor: 499900,
      currency: "INR",
      expiresAt: "2026-10-07T12:30:00Z",
      failureReason: null,
      providerChecked: false,
      testMode: false,
    });
  });

  it("reads paid: true on a captured payment", () => {
    expect(fromWire("status", statusWire("captured"))).toMatchObject({ state: "captured", paid: true });
  });

  it("takes the server's defaults when failure_reason and provider_checked are left out, and a null expires_at", () => {
    const wire = statusWire("created", { expires_at: null });
    delete wire.failure_reason;
    delete wire.provider_checked;
    expect(fromWire("status", wire)).toMatchObject({ failureReason: null, providerChecked: true, expiresAt: null });
  });

  it("reads an expiry the way the server reports it: failed, with failure_reason checkout_expired", () => {
    expect(fromWire("status", statusWire("expired"))).toMatchObject({ state: "expired", paid: false, failureReason: "checkout_expired" });
    expect(fromWire("status", statusWire("failed"))).toMatchObject({ state: "failed", failureReason: "payment_failed" });
  });

  it("keeps a provider it has no launcher for: a status read needs none", () => {
    expect(fromWire("status", statusWire("created", { provider: "juspay" })).provider).toBe("juspay");
  });

  it.each([
    ["a state the contract does not have (paid)", statusWire("created", { state: "paid" })],
    ["a state the contract does not have (expired)", statusWire("created", { state: "expired" })],
    ["paid on a state that is not captured", statusWire("authorized", { paid: true })],
    ["not paid on a captured payment", statusWire("captured", { paid: false })],
    ["a missing paid", statusWire("created", { paid: undefined })],
    ["a missing expires_at", statusWire("created", { expires_at: undefined })],
    ["a provider_checked that is not a boolean", statusWire("created", { provider_checked: "yes" })],
    ["a failure_reason that is not a string", statusWire("failed", { failure_reason: 7 })],
    ["a fractional amount", statusWire("created", { amount_minor: 1.5 })],
  ])("refuses %s", (_label, raw) => {
    expect(() => fromWire("status", raw)).toThrow(PaymentContractError);
  });
});

describe("the calls", () => {
  const fetchMock = vi.fn();

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  function answer(status: number, body: unknown) {
    fetchMock.mockResolvedValue(new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }));
    vi.stubGlobal("fetch", fetchMock);
  }

  function sent(): { url: string; method: string; body: unknown } {
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    return { url, method: init.method ?? "GET", body: typeof init.body === "string" ? JSON.parse(init.body) : undefined };
  }

  it("POST /api/v1/payment/checkouts sends PlatformCheckoutIn: invoice_id, and customer only when given", async () => {
    answer(201, RAZORPAY_CHECKOUT_WIRE);
    await createCheckout("inv-1");
    expect(sent()).toEqual({ url: "/api/v1/payment/checkouts", method: "POST", body: { invoice_id: "inv-1" } });
  });

  it("sends CheckoutCustomerIn with a blank or over-long name or email as null, never cut", async () => {
    answer(201, CASHFREE_CHECKOUT_WIRE);
    await createCheckout("inv-1", { name: "  ", email: `${"a".repeat(250)}@x.in`, phone: "9820011122" });
    expect(sent().body).toEqual({ invoice_id: "inv-1", customer: { name: null, email: null, phone: "9820011122" } });
  });

  it("GET /api/v1/payment/checkouts/{charge_id} reads a status", async () => {
    answer(200, statusWire("captured"));
    await expect(getCheckoutStatus("charge 1")).resolves.toMatchObject({ state: "captured", paid: true });
    expect(sent()).toMatchObject({ url: "/api/v1/payment/checkouts/charge%201", method: "GET" });
  });

  it("POST …/confirm sends CheckoutConfirmIn and reads a PlatformCheckoutStatusOut", async () => {
    answer(200, statusWire("authorized"));
    const fields = { razorpay_order_id: "order_P1", razorpay_payment_id: "pay_1", razorpay_signature: "sig" };
    await expect(confirmRazorpay("c1", fields)).resolves.toMatchObject({ state: "authorized", paid: false });
    expect(sent()).toEqual({ url: "/api/v1/payment/checkouts/c1/confirm", method: "POST", body: fields });
  });
});

describe("checkoutRefusal — errors[0].code", () => {
  it("reads 404 as unknown and 403 as forbidden", () => {
    expect(checkoutRefusal(new ApiError(404, "Not found", { status: 404, detail: "No payable charge for this invoice" }))).toEqual({ kind: "not_found" });
    expect(checkoutRefusal(new ApiError(403, "Not permitted", { status: 403 }))).toEqual({ kind: "forbidden" });
  });

  it("reads 409 invoice_already_paid as paid — the only refusal that is", () => {
    expect(checkoutRefusal(checkoutProblem("invoice_already_paid"))).toEqual({ kind: "already_paid" });
  });

  it.each(["checkout_in_progress", "previous_checkout_not_closable", "previous_checkout_payment_in_flight"] as const)(
    "reads 409 %s as busy: wait and retry, never paid",
    (code) => {
      expect(checkoutRefusal(checkoutProblem(code))).toEqual({ kind: "busy", code, message: refusalMessage(code) });
    },
  );

  it("reads 409 checkout_not_supported as unpayable", () => {
    expect(checkoutRefusal(checkoutProblem("checkout_not_supported"))).toMatchObject({ kind: "unpayable", code: "checkout_not_supported" });
  });

  it.each(["phone_required", "customer_details_required"] as const)("reads 422 %s as the details prompt", (code) => {
    expect(checkoutRefusal(checkoutProblem(code))).toEqual({ kind: "needs_details", code, message: refusalMessage(code) });
  });

  it.each(["currency_unsupported", "amount_below_minimum", "nothing_to_pay"] as const)("reads 422 %s as unpayable", (code) => {
    expect(checkoutRefusal(checkoutProblem(code))).toEqual({ kind: "unpayable", code, message: refusalMessage(code) });
  });

  it("never reads a 409 as paid from its sentence: an uncoded conflict is the server's words", () => {
    // `confirm`'s "no browser confirmation" 409 carries no code at all.
    const bare = new ApiError(409, "Conflict", { type: "https://errors.opsgrids.dev/conflict", status: 409, detail: "This invoice has already been paid" });
    expect(checkoutRefusal(bare)).toEqual({ kind: "conflict", message: "This invoice has already been paid" });
  });

  it("honours a code only at the status the contract gives it", () => {
    const paidAs422 = new ApiError(422, "Validation failed", {
      status: 422,
      detail: "Paid.",
      errors: [{ field: "invoice_id", code: "invoice_already_paid", message: "Paid." }],
    });
    expect(checkoutRefusal(paidAs422)).toMatchObject({ kind: "invalid" });
    const phoneAs409 = new ApiError(409, "Conflict", {
      status: 409,
      detail: "Phone.",
      errors: [{ field: "customer.phone", code: "phone_required", message: "Phone." }],
    });
    expect(checkoutRefusal(phoneAs409)).toEqual({ kind: "conflict", message: "Phone." });
  });

  it("reads only errors[0], as the contract states", () => {
    const second = new ApiError(409, "Conflict", {
      status: 409,
      detail: "Busy.",
      errors: [
        { field: "x", code: "something_else", message: "Busy." },
        { field: "invoice_id", code: "invoice_already_paid", message: "Paid." },
      ],
    });
    expect(checkoutRefusal(second)).toEqual({ kind: "conflict", message: "Busy." });
  });

  it("keeps the framework's field-level 422 for a field it has no refusal code for", () => {
    const error = frameworkProblem("customer.phone", "string_too_long", "String should have at most 16 characters");
    expect(checkoutRefusal(error)).toEqual({
      kind: "invalid",
      fields: { "customer.phone": "String should have at most 16 characters" },
      message: "Request payload failed validation",
    });
  });

  it("passes anything else through as the server's own sentence", () => {
    expect(checkoutRefusal(new ApiError(503, "down", { status: 503, detail: "Online payment is temporarily unavailable" }))).toEqual({
      kind: "other",
      message: "Online payment is temporarily unavailable",
    });
    expect(checkoutRefusal(new TypeError("Failed to fetch"))).toMatchObject({ kind: "other" });
    expect(checkoutRefusal(new PaymentContractError("checkout"))).toMatchObject({ kind: "other", message: expect.stringMatching(/unexpected checkout/) });
  });
});
