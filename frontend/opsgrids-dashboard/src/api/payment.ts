/**
 * Paying an OpsGrids invoice by card, UPI or netbanking — the dashboard's whole view of
 * payment-service's customer checkout (docs/superpowers/specs/2026-10-06-dashboard-pay-now-design.md).
 *
 * The three wire shapes are read in exactly one place, `fromWire`, and the three ways the server
 * says no are read in exactly one place, `checkoutRefusal`. Everything else in the app sees the
 * mapped types below and nothing off the wire. When the service's real shapes are reconciled with
 * the contract this was written against, these two functions are the whole of the change.
 *
 * Nothing here is a secret. The only provider identifier that ever reaches the browser is the
 * public Razorpay key id, and it arrives from the server inside a checkout — it is checked to be a
 * key id here, so a misconfigured server can never get a secret rendered into a page. Nothing is
 * persisted: a checkout lives in component state for as long as the screen that started it.
 */
import { ApiError, apiRequest, fieldErrorMap, fieldErrors, problemMessage, problemSlug } from "@opsgrids/client";

export type CheckoutProvider = "razorpay" | "cashfree";

/** The charge's lifecycle as payment-service reports it. Only the server moves it. */
export type CheckoutState = "created" | "authorized" | "captured" | "failed" | "expired";

/** What the browser must do to let the customer pay — one variant per provider SDK. */
export type ClientAction =
  | { type: "razorpay_checkout"; keyId: string; orderId: string }
  | { type: "cashfree_checkout"; paymentSessionId: string; mode: "sandbox" | "production" };

/** A checkout the server opened for one invoice. The amount is the server's, never the page's. */
export interface CheckoutSession {
  chargeId: string;
  provider: CheckoutProvider;
  action: ClientAction;
  /**
   * `client_action.openable`: false when the server's provider is a stub, whose Order or session
   * no real checkout would accept. Nothing is launched on one.
   */
  openable: boolean;
  amountMinor: number;
  currency: string;
  expiresAt: string | null;
}

/** A status read. The only answer to "did it work?" this app ever acts on. */
export interface CheckoutStatus {
  chargeId: string;
  state: CheckoutState;
  provider: CheckoutProvider;
  amountMinor: number;
  currency: string;
}

/** Contact details the provider needs for some payments (Cashfree requires a phone number). */
export interface CheckoutCustomer {
  name: string;
  email: string;
  phone: string;
}

/** The three fields Razorpay's Checkout hands its `handler`. Sent to `/confirm` and nowhere else. */
export interface RazorpayHandlerFields {
  razorpay_order_id: string;
  razorpay_payment_id: string;
  razorpay_signature: string;
}

export const TERMINAL_STATES: ReadonlySet<CheckoutState> = new Set<CheckoutState>(["captured", "failed", "expired"]);

/** The server answered in a shape this page does not understand. Nothing is opened on it. */
export class PaymentContractError extends Error {
  constructor(what: string) {
    super(`The payment service sent an unexpected ${what}. Nothing was charged by this page; try again later.`);
    this.name = "PaymentContractError";
  }
}

// ------------------------------------------------------------------------------------- calls

/** `POST /payment/checkouts` — open (or re-open) the one checkout for this invoice. */
export async function createCheckout(invoiceId: string, customer?: CheckoutCustomer): Promise<CheckoutSession> {
  const raw = await apiRequest<unknown>("/payment/checkouts", {
    method: "POST",
    body: customer ? { invoice_id: invoiceId, customer } : { invoice_id: invoiceId },
  });
  return fromWire("checkout", raw);
}

/** `GET /payment/checkouts/{charge_id}` — read-through: the server asks the provider when open. */
export async function getCheckoutStatus(chargeId: string, signal?: AbortSignal): Promise<CheckoutStatus> {
  const raw = await apiRequest<unknown>(`/payment/checkouts/${encodeURIComponent(chargeId)}`, { signal });
  return fromWire("status", raw);
}

/**
 * `POST /payment/checkouts/{charge_id}/confirm` — Razorpay only. Hands the server the handler's
 * three fields so it can verify the signature and fetch the payment itself. The answer is not
 * used as proof either: the caller polls the status read afterwards, whatever this returns.
 */
export async function confirmRazorpay(chargeId: string, fields: RazorpayHandlerFields): Promise<CheckoutState> {
  const raw = await apiRequest<unknown>(`/payment/checkouts/${encodeURIComponent(chargeId)}/confirm`, {
    method: "POST",
    body: fields,
  });
  return fromWire("confirm", raw).state;
}

// ----------------------------------------------------------------------------- wire mapping

type Wire = Record<string, unknown>;

/**
 * The states payment-service sends (`PlatformCheckoutStatusOut.state`). An expiry is not a state on
 * the wire: it is `failed` with `failure_reason: "checkout_expired"`, mapped to `expired` below.
 */
const WIRE_STATES = ["created", "authorized", "captured", "failed"] as const;
const EXPIRED_REASON = "checkout_expired";
const PROVIDERS: readonly CheckoutProvider[] = ["razorpay", "cashfree"];
/** A Razorpay *public* key id. A key secret has no such prefix, so it can never pass this. */
const RAZORPAY_KEY_ID = /^rzp_(test|live)_[A-Za-z0-9]+$/;
const ISO_CURRENCY = /^[A-Z]{3}$/;

function isObject(value: unknown): value is Wire {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function text(source: Wire, key: string, what: string): string {
  const value = source[key];
  if (typeof value !== "string" || value.length === 0) throw new PaymentContractError(what);
  return value;
}

function oneOf<T extends string>(source: Wire, key: string, allowed: readonly T[], what: string): T {
  const value = source[key];
  const found = allowed.find((candidate) => candidate === value);
  if (found === undefined) throw new PaymentContractError(what);
  return found;
}

/** Integer minor units only (ADR-0000 §8): a float, a string or a negative is refused, not rounded. */
function minorUnits(source: Wire, what: string): { amountMinor: number; currency: string } {
  const amount = source.amount_minor;
  const currency = source.currency;
  if (typeof amount !== "number" || !Number.isSafeInteger(amount) || amount < 0) throw new PaymentContractError(what);
  if (typeof currency !== "string" || !ISO_CURRENCY.test(currency)) throw new PaymentContractError(what);
  return { amountMinor: amount, currency };
}

/** The wire state, with a checkout nobody paid before it expired told apart from a failed payment. */
function checkoutState(source: Wire, what: string): CheckoutState {
  const state = oneOf(source, "state", WIRE_STATES, what);
  return state === "failed" && source.failure_reason === EXPIRED_REASON ? "expired" : state;
}

function openable(raw: unknown): boolean {
  if (!isObject(raw) || typeof raw.openable !== "boolean") throw new PaymentContractError("checkout");
  return raw.openable;
}

function clientAction(raw: unknown, provider: CheckoutProvider): ClientAction {
  if (!isObject(raw)) throw new PaymentContractError("checkout");
  if (raw.type === "razorpay_checkout" && provider === "razorpay") {
    const keyId = text(raw, "key_id", "checkout");
    if (!RAZORPAY_KEY_ID.test(keyId)) throw new PaymentContractError("checkout");
    return { type: "razorpay_checkout", keyId, orderId: text(raw, "order_id", "checkout") };
  }
  if (raw.type === "cashfree_checkout" && provider === "cashfree") {
    return {
      type: "cashfree_checkout",
      paymentSessionId: text(raw, "payment_session_id", "checkout"),
      mode: oneOf(raw, "mode", ["sandbox", "production"] as const, "checkout"),
    };
  }
  throw new PaymentContractError("checkout");
}

/**
 * The one function that reads payment-service's checkout responses. Strict on purpose: an amount
 * that is not an integer, a currency that is not ISO-4217, a state or provider this page has
 * never heard of, or a client action that does not match its provider is a refusal to proceed,
 * never a best guess.
 */
export function fromWire(kind: "checkout", raw: unknown): CheckoutSession;
export function fromWire(kind: "status", raw: unknown): CheckoutStatus;
export function fromWire(kind: "confirm", raw: unknown): { state: CheckoutState };
export function fromWire(
  kind: "checkout" | "status" | "confirm",
  raw: unknown,
): CheckoutSession | CheckoutStatus | { state: CheckoutState } {
  if (!isObject(raw)) throw new PaymentContractError(kind === "checkout" ? "checkout" : "status");
  if (kind === "confirm") return { state: checkoutState(raw, "status") };
  if (kind === "status") {
    return {
      chargeId: text(raw, "charge_id", "status"),
      state: checkoutState(raw, "status"),
      provider: oneOf(raw, "provider", PROVIDERS, "status"),
      ...minorUnits(raw, "status"),
    };
  }
  const provider = oneOf(raw, "provider", PROVIDERS, "checkout");
  const expires = raw.expires_at;
  return {
    chargeId: text(raw, "charge_id", "checkout"),
    provider,
    action: clientAction(raw.client_action, provider),
    openable: openable(raw.client_action),
    ...minorUnits(raw, "checkout"),
    expiresAt: typeof expires === "string" && expires.length > 0 ? expires : null,
  };
}

// ---------------------------------------------------------------------------- refusals

/** The coded 422 refusals this page has its own words (or its own prompt) for. */
export type RefusalCode = "phone_required" | "currency_unsupported" | "amount_below_minimum";

/**
 * Every spelling of a refusal this page acts on, as a machine code in `errors[].code` or as the
 * problem `type` slug (hyphens read as underscores). The `customer_*` and `unsupported_currency`
 * names are payment-service's own (`_REQUEST_CODES`, services/payment/app/checkout/services.py);
 * `customer_details_required` is the provider asking for contact details, which the phone prompt
 * collects.
 */
const REFUSAL_CODES: ReadonlyMap<string, RefusalCode> = new Map([
  ["phone_required", "phone_required"],
  ["customer_phone_required", "phone_required"],
  ["customer_details_required", "phone_required"],
  ["currency_unsupported", "currency_unsupported"],
  ["unsupported_currency", "currency_unsupported"],
  ["amount_below_minimum", "amount_below_minimum"],
]);

/**
 * payment-service raises these refusals as a bare `ValidationProblem(message)` — type `validation`,
 * no `errors[]`, no code — so today the sentence is the only thing that tells them apart. Each is
 * matched whole against `_REQUEST_CODES` in services/payment/app/checkout/services.py, and only on
 * an uncoded `validation` 422. A reworded sentence falls through to the server's own words
 * (`other`), never to a wrong prompt; once the service attaches a code, `REFUSAL_CODES` wins first.
 */
const UNCODED_REFUSALS: ReadonlyMap<string, RefusalCode> = new Map([
  ["Add a customer phone number to pay online", "phone_required"],
  ["Add the customer's details to pay online", "phone_required"],
  ["Only INR invoices can be paid online", "currency_unsupported"],
  ["The amount is below the minimum that can be paid online (Rs 1)", "amount_below_minimum"],
]);

/**
 * The only machine codes that mean "this invoice is already paid". payment-service answers other
 * 409s from the same route — a checkout another click is preparing, an expired checkout not yet
 * closed, a provider without checkout — and none of those is a payment. So a 409 is read as paid
 * only when it says so in a code, never from its status or its sentence.
 */
const ALREADY_PAID_CODES: ReadonlySet<string> = new Set(["invoice_already_paid", "already_paid"]);

export type CheckoutRefusal =
  | { kind: "already_paid" }
  /** A 409 that is not a coded "paid": the server's own sentence, and the invoice stays payable. */
  | { kind: "conflict"; message: string }
  | { kind: "not_found" }
  | { kind: "forbidden" }
  | { kind: "coded"; code: RefusalCode; message: string }
  /** A field-level 422 — the phone number the customer typed, most likely. */
  | { kind: "invalid"; fields: Record<string, string>; message: string }
  | { kind: "other"; message: string };

/** Every machine code a problem carries: its `errors[].code`s, then its `type` slug. */
function problemCodes(error: ApiError): string[] {
  const codes = fieldErrors(error.problem).map((entry) => entry.code.replace(/-/g, "_"));
  const slug = problemSlug(error.problem).replace(/-/g, "_");
  return slug ? [...codes, slug] : codes;
}

/**
 * Why the server would not open (or report on) a checkout. Machine codes decide: `errors[].code`
 * first, the problem `type` slug second (`phone-required` and `phone_required` are the same code),
 * so either convention the service settles on lands here. A status alone decides only 403 and 404.
 */
export function checkoutRefusal(error: unknown): CheckoutRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: "other", message: error instanceof PaymentContractError ? error.message : problemMessage(error) };
  }
  if (error.status === 404) return { kind: "not_found" };
  if (error.status === 403) return { kind: "forbidden" };
  const message = problemMessage(error);
  const codes = problemCodes(error);
  if (error.status === 409) {
    return codes.some((code) => ALREADY_PAID_CODES.has(code)) ? { kind: "already_paid" } : { kind: "conflict", message };
  }
  if (error.status === 422) {
    const coded = codes.map((code) => REFUSAL_CODES.get(code)).find((code) => code !== undefined);
    if (coded) return { kind: "coded", code: coded, message };
    if (fieldErrors(error.problem).length > 0) return { kind: "invalid", fields: fieldErrorMap(error.problem), message };
    const detail = error.problem?.detail;
    const uncoded = detail && problemSlug(error.problem) === "validation" ? UNCODED_REFUSALS.get(detail) : undefined;
    if (uncoded) return { kind: "coded", code: uncoded, message };
  }
  return { kind: "other", message };
}
