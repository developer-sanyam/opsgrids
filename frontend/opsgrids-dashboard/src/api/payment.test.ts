/**
 * The one mapping from payment-service's checkout shapes, and the one reading of its refusals.
 * Strict on purpose: anything the contract does not promise is refused, never guessed at.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@opsgrids/client";

import { checkoutRefusal, fromWire, PaymentContractError } from "./payment";

const RAZORPAY = {
  charge_id: "6f1c1d1e-0000-4000-8000-000000000001",
  provider: "razorpay",
  client_action: { type: "razorpay_checkout", key_id: "rzp_test_Platform0001", order_id: "order_P1" },
  amount_minor: 499900,
  currency: "INR",
  expires_at: "2026-10-06T12:30:00Z",
};

const CASHFREE = {
  charge_id: "6f1c1d1e-0000-4000-8000-000000000002",
  provider: "cashfree",
  client_action: { type: "cashfree_checkout", payment_session_id: "session_abc", mode: "sandbox" },
  amount_minor: 100,
  currency: "INR",
  expires_at: null,
};

describe("fromWire — checkout", () => {
  it("maps a Razorpay checkout to the public key id and order id only", () => {
    expect(fromWire("checkout", RAZORPAY)).toEqual({
      chargeId: RAZORPAY.charge_id,
      provider: "razorpay",
      action: { type: "razorpay_checkout", keyId: "rzp_test_Platform0001", orderId: "order_P1" },
      amountMinor: 499900,
      currency: "INR",
      expiresAt: "2026-10-06T12:30:00Z",
    });
  });

  it("maps a Cashfree checkout to its session id and mode", () => {
    expect(fromWire("checkout", CASHFREE).action).toEqual({
      type: "cashfree_checkout",
      paymentSessionId: "session_abc",
      mode: "sandbox",
    });
  });

  it.each([
    ["a fractional amount", { ...RAZORPAY, amount_minor: 4999.5 }],
    ["an amount sent as a string", { ...RAZORPAY, amount_minor: "499900" }],
    ["a negative amount", { ...RAZORPAY, amount_minor: -1 }],
    ["an amount past the safe-integer range", { ...RAZORPAY, amount_minor: 2 ** 53 }],
    ["a currency that is not ISO-4217", { ...RAZORPAY, currency: "inr" }],
    ["a provider this page does not know", { ...RAZORPAY, provider: "stripe" }],
    ["an action that does not match its provider", { ...RAZORPAY, provider: "cashfree" }],
    ["something that is not a public key id", { ...RAZORPAY, client_action: { ...RAZORPAY.client_action, key_id: "s3cr3tvalue" } }],
    ["a Cashfree mode outside the two it has", { ...CASHFREE, client_action: { ...CASHFREE.client_action, mode: "test" } }],
    ["a missing client action", { ...RAZORPAY, client_action: null }],
    ["no body at all", null],
  ])("refuses %s", (_label, raw) => {
    expect(() => fromWire("checkout", raw)).toThrow(PaymentContractError);
  });
});

describe("fromWire — status and confirm", () => {
  it("maps a status read", () => {
    expect(
      fromWire("status", { charge_id: "c1", state: "authorized", provider: "cashfree", amount_minor: 100, currency: "INR" }),
    ).toEqual({ chargeId: "c1", state: "authorized", provider: "cashfree", amountMinor: 100, currency: "INR" });
  });

  it("refuses a state the contract does not have", () => {
    expect(() =>
      fromWire("status", { charge_id: "c1", state: "paid", provider: "razorpay", amount_minor: 100, currency: "INR" }),
    ).toThrow(PaymentContractError);
  });

  it("maps a confirm answer to its state", () => {
    expect(fromWire("confirm", { state: "captured" })).toEqual({ state: "captured" });
  });
});

function problem(status: number, body: Record<string, unknown>): ApiError {
  return new ApiError(status, "refused", { status, ...body });
}

describe("checkoutRefusal", () => {
  it("reads 409 as already paid, 404 as unknown, 403 as forbidden", () => {
    expect(checkoutRefusal(problem(409, { detail: "paid" }))).toEqual({ kind: "already_paid" });
    expect(checkoutRefusal(problem(404, {}))).toEqual({ kind: "not_found" });
    expect(checkoutRefusal(problem(403, {}))).toEqual({ kind: "forbidden" });
  });

  it("reads a coded 422 from errors[].code", () => {
    const error = problem(422, {
      detail: "A phone number is needed.",
      errors: [{ field: "customer.phone", code: "phone_required", message: "Required." }],
    });
    expect(checkoutRefusal(error)).toEqual({ kind: "coded", code: "phone_required", message: "A phone number is needed." });
  });

  it("reads a coded 422 from the problem type slug", () => {
    const error = problem(422, { type: "https://errors.opsgrids.dev/currency-unsupported", detail: "USD is not supported." });
    expect(checkoutRefusal(error)).toMatchObject({ kind: "coded", code: "currency_unsupported" });
  });

  it("keeps field-level detail for a 422 it has no code for", () => {
    const error = problem(422, {
      detail: "Validation failed",
      errors: [{ field: "customer.phone", code: "string_pattern_mismatch", message: "Enter ten digits." }],
    });
    expect(checkoutRefusal(error)).toEqual({
      kind: "invalid",
      fields: { "customer.phone": "Enter ten digits." },
      message: "Validation failed",
    });
  });

  it("passes anything else through as the server's own sentence", () => {
    expect(checkoutRefusal(problem(500, { detail: "Upstream down." }))).toEqual({ kind: "other", message: "Upstream down." });
    expect(checkoutRefusal(new TypeError("Failed to fetch"))).toMatchObject({ kind: "other" });
  });
});
