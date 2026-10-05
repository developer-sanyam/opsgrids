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

const STATES: readonly CheckoutState[] = ["created", "authorized", "captured", "failed", "expired"];
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
  if (kind === "confirm") return { state: oneOf(raw, "state", STATES, "status") };
  if (kind === "status") {
    return {
      chargeId: text(raw, "charge_id", "status"),
      state: oneOf(raw, "state", STATES, "status"),
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
    ...minorUnits(raw, "checkout"),
    expiresAt: typeof expires === "string" && expires.length > 0 ? expires : null,
  };
}

// ---------------------------------------------------------------------------- refusals

/** The coded 422 refusals this page has its own words (or its own prompt) for. */
export type RefusalCode = "phone_required" | "currency_unsupported" | "amount_below_minimum";

const KNOWN_CODES: readonly RefusalCode[] = ["phone_required", "currency_unsupported", "amount_below_minimum"];

export type CheckoutRefusal =
  | { kind: "already_paid" }
  | { kind: "not_found" }
  | { kind: "forbidden" }
  | { kind: "coded"; code: RefusalCode; message: string }
  /** A field-level 422 — the phone number the customer typed, most likely. */
  | { kind: "invalid"; fields: Record<string, string>; message: string }
  | { kind: "other"; message: string };

/**
 * Why the server would not open (or report on) a checkout. The coded refusal is read from the
 * problem's `errors[].code` first and from the problem `type` slug second (`phone-required` and
 * `phone_required` are the same code), so either convention the service settles on lands here.
 */
export function checkoutRefusal(error: unknown): CheckoutRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: "other", message: error instanceof PaymentContractError ? error.message : problemMessage(error) };
  }
  if (error.status === 409) return { kind: "already_paid" };
  if (error.status === 404) return { kind: "not_found" };
  if (error.status === 403) return { kind: "forbidden" };
  const message = problemMessage(error);
  if (error.status === 422) {
    const entries = fieldErrors(error.problem);
    const fromEntries = entries.map((entry) => entry.code);
    const fromSlug = problemSlug(error.problem).replace(/-/g, "_");
    const code = KNOWN_CODES.find((known) => fromEntries.includes(known) || fromSlug === known);
    if (code) return { kind: "coded", code, message };
    if (entries.length > 0) return { kind: "invalid", fields: fieldErrorMap(error.problem), message };
  }
  return { kind: "other", message };
}
