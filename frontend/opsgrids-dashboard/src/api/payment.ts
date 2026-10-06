/**
 * Paying an OpsGrids invoice by card, UPI or netbanking — the dashboard's whole view of
 * payment-service's customer checkout (ADR-0029; docs/superpowers/specs/2026-10-06-dashboard-pay-now-design.md §3).
 *
 * The contract is `libs/contracts/openapi/payment.json`: `PlatformCheckoutIn`, `CheckoutCustomerIn`,
 * `PlatformCheckoutOut`, `PlatformCheckoutStatusOut`, `CheckoutConfirmIn`, and the coded refusals
 * of `POST /payment/checkouts` (`errors[0].code`, services/payment/app/checkout/services.py). The
 * wire is touched in exactly three places: the request bodies below, `fromWire` for every answer,
 * and `checkoutRefusal` for every way the server says no. Everything else in the app sees the
 * mapped types and nothing off the wire.
 *
 * Nothing here is a secret. The only provider identifier that ever reaches the browser is the
 * public Razorpay key id, and it arrives from the server inside a checkout — it is checked to be a
 * key id here, so a misconfigured server can never get a secret rendered into a page. Nothing is
 * persisted: a checkout lives in component state for as long as the screen that started it.
 */
import { ApiError, apiRequest, fieldErrorMap, problemMessage } from "@opsgrids/client";

/** The providers that hand the browser a `client_action` this page can open. */
export type CheckoutProvider = "razorpay" | "cashfree";

/**
 * `PlatformCheckoutStatusOut.state`, plus `expired`. An expiry is not a state on the wire: it is
 * `failed` with `failure_reason: "checkout_expired"` — nobody paid before the checkout closed —
 * and it is told apart here from a payment that failed.
 */
export type CheckoutState = "created" | "authorized" | "captured" | "failed" | "expired";

/** An openable `client_action` — what the browser opens, one variant per provider SDK. */
export type ClientAction =
  | { type: "razorpay_checkout"; keyId: string; orderId: string }
  | { type: "cashfree_checkout"; paymentSessionId: string; mode: "sandbox" | "production" };

interface CheckoutBase {
  chargeId: string;
  provider: CheckoutProvider;
  /** The invoice's own charge, never the page's figure. */
  amountMinor: number;
  currency: string;
  /** `expires_at`: when this checkout stops being payable. Always set on an opened checkout. */
  expiresAt: string;
}

/** A checkout whose `client_action.openable` is true: a real provider window can take it. */
export interface OpenableCheckout extends CheckoutBase {
  openable: true;
  action: ClientAction;
}

/**
 * `client_action.openable: false` — a stub provider's stand-in Order or session that no real
 * checkout would accept (a stub Razorpay's key id is even empty). There is nothing to launch.
 */
export interface StubCheckout extends CheckoutBase {
  openable: false;
  action: null;
}

/** `PlatformCheckoutOut`: a checkout the server opened (or reopened) for one invoice. */
export type CheckoutSession = OpenableCheckout | StubCheckout;

/**
 * `PlatformCheckoutStatusOut` — the only answer to "did it work?" this app ever acts on. It is what
 * the status read and the Razorpay confirm both return.
 */
export interface CheckoutStatus {
  chargeId: string;
  /** A plain string on the wire. Read, never branched on: a status needs no launcher. */
  provider: string;
  state: CheckoutState;
  /** True only for a captured payment. Checked to agree with `state`. */
  paid: boolean;
  amountMinor: number;
  currency: string;
  /** `expires_at`: nullable on a status read. */
  expiresAt: string | null;
  /** `failure_reason`: set only on a `failed` checkout. */
  failureReason: string | null;
  /** `provider_checked`: false when the provider could not be asked this time — `state` is then the last one recorded. */
  providerChecked: boolean;
}

/**
 * `CheckoutCustomerIn`, as this page sends it: only from the details prompt, which always carries
 * a phone. The provider gets it and nothing else does — the server never stores it.
 */
export interface CheckoutCustomer {
  name: string | null;
  email: string | null;
  phone: string;
}

/** `CheckoutConfirmIn`: the three fields Razorpay's Checkout hands its `handler`. Sent to `/confirm` and nowhere else. */
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

// ------------------------------------------------------------------------------ requests

/** `CheckoutCustomerIn`'s own limits. A value past one is sent as null rather than refused. */
const NAME_MAX = 100;
const EMAIL_MAX = 254;

interface CheckoutCustomerIn {
  name: string | null;
  email: string | null;
  phone: string | null;
}

interface PlatformCheckoutIn {
  invoice_id: string;
  customer?: CheckoutCustomerIn;
}

/** Blank, or longer than the server takes: null. A provider gets no name rather than a cut one. */
function optionalText(value: string | null, max: number): string | null {
  const trimmed = value?.trim() ?? "";
  return trimmed.length > 0 && trimmed.length <= max ? trimmed : null;
}

function customerIn(customer: CheckoutCustomer): CheckoutCustomerIn {
  return {
    name: optionalText(customer.name, NAME_MAX),
    email: optionalText(customer.email, EMAIL_MAX),
    phone: customer.phone,
  };
}

// ------------------------------------------------------------------------------- calls

/** `POST /payment/checkouts` — open (or re-open) the one checkout for this invoice. 201. */
export async function createCheckout(invoiceId: string, customer?: CheckoutCustomer): Promise<CheckoutSession> {
  const body: PlatformCheckoutIn = customer ? { invoice_id: invoiceId, customer: customerIn(customer) } : { invoice_id: invoiceId };
  const raw = await apiRequest<unknown>("/payment/checkouts", { method: "POST", body });
  return fromWire("checkout", raw);
}

/** `GET /payment/checkouts/{charge_id}` — read-through: the server asks the provider while it is open. */
export async function getCheckoutStatus(chargeId: string, signal?: AbortSignal): Promise<CheckoutStatus> {
  const raw = await apiRequest<unknown>(`/payment/checkouts/${encodeURIComponent(chargeId)}`, { signal });
  return fromWire("status", raw);
}

/**
 * `POST /payment/checkouts/{charge_id}/confirm` — Razorpay only. Hands the server the handler's
 * three fields so it can verify the signature and ask Razorpay itself. The answer is not used as
 * proof either: the caller polls the status read afterwards, whatever this returns.
 */
export async function confirmRazorpay(chargeId: string, fields: RazorpayHandlerFields): Promise<CheckoutStatus> {
  const raw = await apiRequest<unknown>(`/payment/checkouts/${encodeURIComponent(chargeId)}/confirm`, {
    method: "POST",
    body: fields,
  });
  return fromWire("status", raw);
}

// ----------------------------------------------------------------------------- wire mapping

type Wire = Record<string, unknown>;

const WIRE_STATES = ["created", "authorized", "captured", "failed"] as const;
const EXPIRED_REASON = "checkout_expired";
const PROVIDERS: readonly CheckoutProvider[] = ["razorpay", "cashfree"];
const ACTION_TYPES = { razorpay: "razorpay_checkout", cashfree: "cashfree_checkout" } as const;
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

/** A key the schema requires that may be null: missing is a contract break, null is not. */
function nullableText(source: Wire, key: string, what: string): string | null {
  const value = source[key];
  if (value === null) return null;
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

/** `PlatformCheckoutStatusOut`. `failure_reason` and `provider_checked` have server defaults (null, true). */
function statusFromWire(raw: Wire): CheckoutStatus {
  const what = "status";
  const wireState = oneOf(raw, "state", WIRE_STATES, what);
  const paid = raw.paid;
  // `paid` is `state == captured` on the server; a body where they disagree is not one it sends.
  if (typeof paid !== "boolean" || paid !== (wireState === "captured")) throw new PaymentContractError(what);
  const failureReason = raw.failure_reason ?? null;
  if (failureReason !== null && typeof failureReason !== "string") throw new PaymentContractError(what);
  const providerChecked = raw.provider_checked === undefined ? true : raw.provider_checked;
  if (typeof providerChecked !== "boolean") throw new PaymentContractError(what);
  return {
    chargeId: text(raw, "charge_id", what),
    provider: text(raw, "provider", what),
    state: wireState === "failed" && failureReason === EXPIRED_REASON ? "expired" : wireState,
    paid,
    ...minorUnits(raw, what),
    expiresAt: nullableText(raw, "expires_at", what),
    failureReason,
    providerChecked,
  };
}

/**
 * `client_action`, tagged by `type`, which must be its provider's. Only an openable one is read
 * further — strictly, since it is what a provider window is opened with. A stub's is never opened,
 * so its stand-in ids are not read at all.
 */
function actionFromWire(raw: unknown, provider: CheckoutProvider): ClientAction | null {
  const what = "checkout";
  if (!isObject(raw) || raw.type !== ACTION_TYPES[provider] || typeof raw.openable !== "boolean") {
    throw new PaymentContractError(what);
  }
  if (!raw.openable) return null;
  if (raw.type === "razorpay_checkout") {
    const keyId = text(raw, "key_id", what);
    if (!RAZORPAY_KEY_ID.test(keyId)) throw new PaymentContractError(what);
    return { type: "razorpay_checkout", keyId, orderId: text(raw, "order_id", what) };
  }
  return {
    type: "cashfree_checkout",
    paymentSessionId: text(raw, "payment_session_id", what),
    mode: oneOf(raw, "mode", ["sandbox", "production"] as const, what),
  };
}

/** `PlatformCheckoutOut`. Every key is required, and `expires_at` is never null on it. */
function checkoutFromWire(raw: Wire): CheckoutSession {
  const what = "checkout";
  const provider = oneOf(raw, "provider", PROVIDERS, what);
  const base: CheckoutBase = {
    chargeId: text(raw, "charge_id", what),
    provider,
    ...minorUnits(raw, what),
    expiresAt: text(raw, "expires_at", what),
  };
  const action = actionFromWire(raw.client_action, provider);
  return action ? { ...base, openable: true, action } : { ...base, openable: false, action: null };
}

/**
 * The one function that reads payment-service's checkout answers. Strict on purpose: an amount
 * that is not an integer, a currency that is not ISO-4217, a state or provider this page has never
 * heard of, a required key missing, or a client action that does not match its provider is a
 * refusal to proceed, never a best guess.
 */
export function fromWire(kind: "checkout", raw: unknown): CheckoutSession;
export function fromWire(kind: "status", raw: unknown): CheckoutStatus;
export function fromWire(kind: "checkout" | "status", raw: unknown): CheckoutSession | CheckoutStatus {
  if (!isObject(raw)) throw new PaymentContractError(kind);
  return kind === "checkout" ? checkoutFromWire(raw) : statusFromWire(raw);
}

// ---------------------------------------------------------------------------- refusals

/** 409s that mean "not now" — wait, then try again. None of them is a payment. */
const BUSY_CODES = ["checkout_in_progress", "previous_checkout_not_closable", "previous_checkout_payment_in_flight"] as const;
export type BusyCode = (typeof BUSY_CODES)[number];

/** 422s the details prompt answers: the provider needs who is paying. */
const DETAILS_CODES = ["phone_required", "customer_details_required"] as const;
export type DetailsCode = (typeof DETAILS_CODES)[number];

/** 422s for what can never be paid online, refused before any provider call. */
const UNPAYABLE_CODES = ["currency_unsupported", "amount_below_minimum", "nothing_to_pay"] as const;

/** Why online payment cannot take this invoice: the 409 `checkout_not_supported`, and the 422s above. */
export type UnpayableCode = "checkout_not_supported" | (typeof UNPAYABLE_CODES)[number];

export type CheckoutRefusal =
  /** 409 `invoice_already_paid` — the only refusal that means paid. */
  | { kind: "already_paid" }
  | { kind: "busy"; code: BusyCode; message: string }
  | { kind: "needs_details"; code: DetailsCode; message: string }
  | { kind: "unpayable"; code: UnpayableCode; message: string }
  /** A 409 with no code this page knows: the server's own sentence, and the invoice stays payable. */
  | { kind: "conflict"; message: string }
  | { kind: "not_found" }
  | { kind: "forbidden" }
  /** A field-level 422 with no refusal code — the framework's own, on `customer.phone` most likely. */
  | { kind: "invalid"; fields: Record<string, string>; message: string }
  | { kind: "other"; message: string };

function member<T extends string>(allowed: readonly T[], value: string | null): T | undefined {
  return allowed.find((candidate) => candidate === value);
}

/**
 * `errors[0].code`. Read here rather than through `fieldErrors`, which keeps only entries with a
 * `field` — and the busy 409s carry `{code, message}` with no field.
 */
function refusalCode(error: ApiError): string | null {
  const errors: unknown = error.problem?.errors;
  const first: unknown = Array.isArray(errors) ? errors[0] : undefined;
  if (!isObject(first)) return null;
  return typeof first.code === "string" ? first.code : null;
}

/**
 * Why the server would not open (or report on) a checkout. A code decides, and only at the status
 * the contract gives it: `invoice_already_paid` is paid only on a 409, never from a status or a
 * sentence. A status alone decides only 403 and 404.
 */
export function checkoutRefusal(error: unknown): CheckoutRefusal {
  if (!(error instanceof ApiError)) {
    return { kind: "other", message: error instanceof PaymentContractError ? error.message : problemMessage(error) };
  }
  if (error.status === 404) return { kind: "not_found" };
  if (error.status === 403) return { kind: "forbidden" };
  const message = problemMessage(error);
  const code = refusalCode(error);
  if (error.status === 409) {
    if (code === "invoice_already_paid") return { kind: "already_paid" };
    const busy = member(BUSY_CODES, code);
    if (busy) return { kind: "busy", code: busy, message };
    if (code === "checkout_not_supported") return { kind: "unpayable", code, message };
    return { kind: "conflict", message };
  }
  if (error.status === 422) {
    const details = member(DETAILS_CODES, code);
    if (details) return { kind: "needs_details", code: details, message };
    const unpayable = member(UNPAYABLE_CODES, code);
    if (unpayable) return { kind: "unpayable", code: unpayable, message };
    const fields = fieldErrorMap(error.problem);
    if (Object.keys(fields).length > 0) return { kind: "invalid", fields, message };
  }
  return { kind: "other", message };
}
