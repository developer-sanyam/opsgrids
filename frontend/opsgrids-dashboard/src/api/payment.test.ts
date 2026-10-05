/**
 * The one mapping from payment-service's checkout shapes, and the one reading of its refusals.
 * Strict on purpose: anything the contract does not promise is refused, never guessed at.
 */
import { describe, expect, it } from "vitest";

import { ApiError } from "@opsgrids/client";

import { checkoutRefusal, fromWire, PaymentContractError } from "./payment";

// `PlatformCheckoutOut` as payment-service (feat/payments-phase-a, services/payment/app/schemas.py)
// sends it: `client_action` carries `openable`.
const RAZORPAY = {
  charge_id: "6f1c1d1e-0000-4000-8000-000000000001",
  provider: "razorpay",
  client_action: { type: "razorpay_checkout", key_id: "rzp_test_Platform0001", order_id: "order_P1", openable: true },
  amount_minor: 499900,
  currency: "INR",
  expires_at: "2026-10-06T12:30:00Z",
};

const CASHFREE = {
  charge_id: "6f1c1d1e-0000-4000-8000-000000000002",
  provider: "cashfree",
  client_action: { type: "cashfree_checkout", payment_session_id: "session_abc", mode: "sandbox", openable: true },
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
      openable: true,
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

  it("carries a stub provider's openable: false, so nothing is launched on it", () => {
    // What a stubbed Cashfree answers: a sandbox mode and a stand-in session no real SDK accepts.
    const stub = { ...CASHFREE, client_action: { ...CASHFREE.client_action, payment_session_id: "session_stub_1", openable: false } };
    expect(fromWire("checkout", stub).openable).toBe(false);
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
    ["a client action without openable", { ...RAZORPAY, client_action: { type: "razorpay_checkout", key_id: "rzp_test_Platform0001", order_id: "order_P1" } }],
    ["an openable that is not a boolean", { ...RAZORPAY, client_action: { ...RAZORPAY.client_action, openable: "true" } }],
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

  it.each(["paid", "expired"])("refuses a state the contract does not have (%s)", (state) => {
    expect(() =>
      fromWire("status", { charge_id: "c1", state, provider: "razorpay", amount_minor: 100, currency: "INR" }),
    ).toThrow(PaymentContractError);
  });

  it("reads an expiry the way the server reports it: failed, with failure_reason checkout_expired", () => {
    // `PlatformCheckoutStatusOut`: nobody paid before the checkout expired — not a failed payment.
    const wire = {
      charge_id: "c1",
      provider: "cashfree",
      state: "failed",
      paid: false,
      amount_minor: 100,
      currency: "INR",
      expires_at: "2026-10-06T12:30:00Z",
      failure_reason: "checkout_expired",
      provider_checked: false,
    };
    expect(fromWire("status", wire).state).toBe("expired");
    expect(fromWire("status", { ...wire, failure_reason: "payment_failed" }).state).toBe("failed");
    expect(fromWire("confirm", wire)).toEqual({ state: "expired" });
  });

  it("maps a confirm answer to its state", () => {
    expect(fromWire("confirm", { state: "captured" })).toEqual({ state: "captured" });
  });
});

function problem(status: number, body: Record<string, unknown>): ApiError {
  return new ApiError(status, "refused", { status, ...body });
}

/** A problem exactly as `platform_core.errors.ProblemError.to_problem` writes it: no `errors` key. */
function serviceProblem(status: number, slug: string, title: string, detail: string): ApiError {
  return new ApiError(status, title, {
    type: `https://errors.opsgrids.dev/${slug}`,
    title,
    status,
    detail,
    instance: "/payment/checkouts",
    request_id: "req-1",
  });
}

describe("checkoutRefusal", () => {
  it("reads 404 as unknown and 403 as forbidden", () => {
    expect(checkoutRefusal(problem(404, {}))).toEqual({ kind: "not_found" });
    expect(checkoutRefusal(problem(403, {}))).toEqual({ kind: "forbidden" });
  });

  it.each([
    "This invoice's checkout is being prepared; try again in a moment",
    "The previous checkout for this invoice could not be closed yet; try again shortly",
    "Online payment is not available with the configured provider",
    "Online payment is not available with this provider",
    // Even payment-service's own "paid" sentence: a status and a sentence are not a code.
    "This invoice has already been paid",
  ])("never reads payment-service's bare 409 conflict as paid: %s", (detail) => {
    expect(checkoutRefusal(serviceProblem(409, "conflict", "Conflict", detail))).toEqual({ kind: "conflict", message: detail });
  });

  it("reads a 409 as paid only when it carries the already-paid code, in either convention", () => {
    expect(checkoutRefusal(serviceProblem(409, "invoice-already-paid", "Conflict", "Paid."))).toEqual({ kind: "already_paid" });
    expect(
      checkoutRefusal(problem(409, { detail: "Paid.", errors: [{ field: "invoice_id", code: "invoice_already_paid", message: "Paid." }] })),
    ).toEqual({ kind: "already_paid" });
  });

  it.each([
    ["Add a customer phone number to pay online", "phone_required"],
    ["Add the customer's details to pay online", "phone_required"],
    ["Only INR invoices can be paid online", "currency_unsupported"],
    ["The amount is below the minimum that can be paid online (Rs 1)", "amount_below_minimum"],
  ])("reads payment-service's uncoded validation 422 %j as %s", (detail, code) => {
    expect(checkoutRefusal(serviceProblem(422, "validation", "Validation failed", detail))).toEqual({ kind: "coded", code, message: detail });
  });

  it.each([
    ["customer_phone_required", "phone_required"],
    ["customer_details_required", "phone_required"],
    ["unsupported_currency", "currency_unsupported"],
  ])("reads payment-service's own code name %s, once it is on the wire", (wireCode, code) => {
    const error = problem(422, { detail: "Refused.", errors: [{ field: "customer.phone", code: wireCode, message: "Refused." }] });
    expect(checkoutRefusal(error)).toMatchObject({ kind: "coded", code });
    expect(checkoutRefusal(serviceProblem(422, wireCode.replace(/_/g, "-"), "Refused", "Refused."))).toMatchObject({ kind: "coded", code });
  });

  it.each([
    "There is nothing to pay on this invoice",
    "Add a phone number to pay online",
    "constructor",
    "__proto__",
  ])("shows any other uncoded validation 422 in the server's own words: %s", (detail) => {
    expect(checkoutRefusal(serviceProblem(422, "validation", "Validation failed", detail))).toEqual({ kind: "other", message: detail });
  });

  it("does not read a known sentence under another problem type", () => {
    const error = serviceProblem(422, "business-rule", "Business rule violation", "Add a customer phone number to pay online");
    expect(checkoutRefusal(error)).toMatchObject({ kind: "other" });
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
