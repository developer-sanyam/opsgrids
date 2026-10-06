/**
 * payment-service's checkout wire, as the tests feed it to the page: the bodies of
 * `libs/contracts/openapi/payment.json` (`PlatformCheckoutOut`, `PlatformCheckoutStatusOut`) and
 * the problem documents `POST /payment/checkouts` refuses with — `platform_core.errors`'
 * `to_problem`, with `errors[0] = {field?, code, message}` and the sentences of
 * services/payment/app/checkout/services.py (`_REFUSALS`, `conflict`). Every mapped value a test
 * uses goes through `fromWire`, so a fixture that drifts from the contract fails at the mapper.
 */
import { ApiError } from "@opsgrids/client";
import type { ProblemJson } from "@opsgrids/client";

import { fromWire } from "@/api/payment";
import type {
  BusyCode,
  CheckoutSession,
  CheckoutState,
  CheckoutStatus,
  DetailsCode,
  OpenableCheckout,
  UnpayableCode,
} from "@/api/payment";

export const CHARGE_ID = "6f1c1d1e-0000-4000-8000-000000000001";

/** `PlatformCheckoutOut` from a live Razorpay: the public key id and the Order id. */
export const RAZORPAY_CHECKOUT_WIRE = {
  charge_id: CHARGE_ID,
  provider: "razorpay",
  client_action: { type: "razorpay_checkout", key_id: "rzp_test_Platform0001", order_id: "order_P1", openable: true },
  amount_minor: 499900,
  currency: "INR",
  expires_at: "2026-10-07T12:30:00Z",
};

/** `PlatformCheckoutOut` from a live Cashfree: the session id and the SDK mode. */
export const CASHFREE_CHECKOUT_WIRE = {
  charge_id: "6f1c1d1e-0000-4000-8000-000000000002",
  provider: "cashfree",
  client_action: { type: "cashfree_checkout", payment_session_id: "session_abc", mode: "sandbox", openable: true },
  amount_minor: 100,
  currency: "INR",
  expires_at: "2026-10-07T12:30:00Z",
};

/** The stub Razorpay's `PlatformCheckoutOut`: no key id at all, and a stand-in Order. */
export const STUB_RAZORPAY_CHECKOUT_WIRE = {
  ...RAZORPAY_CHECKOUT_WIRE,
  client_action: { type: "razorpay_checkout", key_id: "", order_id: "order_stub_0001", openable: false },
};

function openableFromWire(raw: unknown): OpenableCheckout {
  const session = fromWire("checkout", raw);
  if (!session.openable) throw new Error("This fixture's client_action is not openable.");
  return session;
}

/** The mapped checkouts the page sees for the bodies above. */
export const RAZORPAY_CHECKOUT: OpenableCheckout = openableFromWire(RAZORPAY_CHECKOUT_WIRE);
export const CASHFREE_CHECKOUT: OpenableCheckout = openableFromWire(CASHFREE_CHECKOUT_WIRE);
export const STUB_CHECKOUT: CheckoutSession = fromWire("checkout", STUB_RAZORPAY_CHECKOUT_WIRE);

/** `PlatformCheckoutStatusOut` for a state, as `_status_out` writes it. `expired` is `failed` + `checkout_expired`. */
export function statusWire(state: CheckoutState, overrides: Record<string, unknown> = {}): Record<string, unknown> {
  const failed = state === "failed" || state === "expired";
  return {
    charge_id: CHARGE_ID,
    provider: "razorpay",
    state: state === "expired" ? "failed" : state,
    paid: state === "captured",
    amount_minor: 499900,
    currency: "INR",
    expires_at: "2026-10-07T12:30:00Z",
    failure_reason: failed ? (state === "expired" ? "checkout_expired" : "payment_failed") : null,
    provider_checked: true,
    ...overrides,
  };
}

/** The mapped status the page sees for `statusWire(state, overrides)`. */
export function checkoutStatus(state: CheckoutState, overrides: Record<string, unknown> = {}): CheckoutStatus {
  return fromWire("status", statusWire(state, overrides));
}

const MESSAGES: Record<BusyCode | DetailsCode | UnpayableCode | "invoice_already_paid", { field?: string; message: string }> = {
  invoice_already_paid: { field: "invoice_id", message: "This invoice has already been paid" },
  checkout_in_progress: { message: "This invoice's checkout is being prepared; try again in a moment" },
  previous_checkout_not_closable: { message: "The previous checkout for this invoice could not be closed yet; try again shortly" },
  previous_checkout_payment_in_flight: {
    message: "A payment on this invoice's previous checkout is still being processed; try again in a few minutes",
  },
  checkout_not_supported: { message: "Online payment is not available with the configured provider" },
  phone_required: { field: "customer.phone", message: "Add a customer phone number to pay online" },
  customer_details_required: { field: "customer", message: "Add the customer's details to pay online" },
  currency_unsupported: { field: "invoice_id", message: "Only INR invoices can be paid online" },
  amount_below_minimum: { field: "invoice_id", message: "The amount is below the minimum that can be paid online (Rs 1)" },
  nothing_to_pay: { field: "invoice_id", message: "There is nothing to pay on this invoice" },
};

export type RefusalCodeName = keyof typeof MESSAGES;

/** The 409 codes; every other refusal code is a 422. */
const CONFLICT_CODES: ReadonlySet<RefusalCodeName> = new Set<RefusalCodeName>([
  "invoice_already_paid",
  "checkout_in_progress",
  "previous_checkout_not_closable",
  "previous_checkout_payment_in_flight",
  "checkout_not_supported",
]);

export function refusalMessage(code: RefusalCodeName): string {
  return MESSAGES[code].message;
}

/** A refusal exactly as `POST /payment/checkouts` sends it: `conflict(...)` for a 409, `refusal(...)` for a 422. */
export function checkoutProblem(code: RefusalCodeName): ApiError {
  const { field, message } = MESSAGES[code];
  const conflict = CONFLICT_CODES.has(code);
  const status = conflict ? 409 : 422;
  const title = conflict ? "Conflict" : "Validation failed";
  const body: unknown = {
    type: `https://errors.opsgrids.dev/${conflict ? "conflict" : "validation"}`,
    title,
    status,
    detail: message,
    instance: "/api/v1/payment/checkouts",
    request_id: "req-1",
    errors: [field === undefined ? { code, message } : { field, code, message }],
  };
  // `ProblemJson.errors` puts a `field` on every entry; the busy 409s carry none, as on the wire.
  return new ApiError(status, message, body as ProblemJson);
}

/** The framework's own 422 for a body the schema refuses (`RequestValidationError` → `errors[]`). */
export function frameworkProblem(field: string, code: string, message: string): ApiError {
  return new ApiError(422, "Request payload failed validation", {
    type: "https://errors.opsgrids.dev/validation",
    title: "Validation failed",
    status: 422,
    detail: "Request payload failed validation",
    instance: "/api/v1/payment/checkouts",
    request_id: "req-1",
    errors: [{ field, code, message }],
  });
}
