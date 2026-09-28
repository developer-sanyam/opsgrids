/**
 * The new-order form's state, its quantity/price parsing, its validation, and the payload it
 * builds — as pure functions, separated from `OrderForm.tsx` the same way `purchaseOrderForm.ts`
 * is split from `PurchaseOrderForm.tsx`. This **mirrors** `OrderIn`/`OrderLineIn`
 * (`services/operations/app/schemas.py`) without replacing the server's own checks, which run
 * again regardless.
 *
 * Three entry modes converge on the same `POST /orders` shape, differing only in `channel` and in
 * what the customer fields mean to the operator filling them in (Fulfilment F2's frontend bullet):
 * **phone** (a caller, phoned in), **manual** (typed directly at the counter, no CRM lookup — the
 * plan's own words), and **sales** (an operator-authored order). None of the three drives a real
 * CRM phone lookup: `services/customer`'s phone index is internal-only, never reachable from a
 * browser (`services/customer/app/api/internal.py`'s own docstring — service-to-service, behind
 * an allow-list) and the service's one public contact search (`GET /contacts?q=`) is documented
 * as name/title/email only, not phone. So all three modes take the same typed name/phone pair;
 * `OrderIn` already accepts that as its own first-class shape (a manual pair beside, or instead
 * of, a CRM `customer_id`) — there is no gap this module papers over, just no lookup to wire yet.
 * See `OrderForm.tsx`'s phone-mode note for what is shown about this in the UI.
 */

import type { OrderChannel, OrderIn, OrderLineIn, Qty } from "@/api/types";
import { qtyToMilli } from "@/lib/quantity";

export type OrderEntryMode = "phone" | "manual" | "sales";

export const MODE_CHANNEL: Record<OrderEntryMode, OrderChannel> = {
  phone: "phone",
  manual: "manual",
  sales: "sales",
};

/** One line row's form state — every value a string, because that is what an input holds.
 *  `inventoryItemId` is the wire value (`OrderLineIn.inventory_item_id`, a UUID); `sku`/`itemName`
 *  are display-only, cached off the picked item. */
export interface OrderLineDraft {
  key: string;
  inventoryItemId: string;
  sku: string;
  itemName: string;
  quantity: string;
  /** A typed override in rupees. Blank leaves pricing to the item's own `selling_price_minor` —
   *  which `place` fills in, and refuses the whole order over if the item has none either. */
  priceOverride: string;
}

export interface OrderFormValues {
  mode: OrderEntryMode;
  warehouseId: string;
  customerName: string;
  customerPhone: string;
  lines: OrderLineDraft[];
}

export function emptyOrderLine(key: string): OrderLineDraft {
  return { key, inventoryItemId: "", sku: "", itemName: "", quantity: "", priceOverride: "" };
}

export function emptyOrderForm(mode: OrderEntryMode, firstLineKey: string): OrderFormValues {
  return {
    mode,
    warehouseId: "",
    customerName: "",
    customerPhone: "",
    lines: [emptyOrderLine(firstLineKey)],
  };
}

export type OrderLineField = "item" | "quantity" | "price";
export type OrderLineErrors = Partial<Record<OrderLineField, string>>;

export interface OrderFormErrors {
  warehouse?: string;
  /** Set when neither a name nor a phone number was entered — `OrderIn`'s own rule: an order for
   *  nobody is not a real order. */
  customer?: string;
  /** Set when there are no lines at all, or more than the server's 200-line cap — a single row's
   *  own problem lives in `lineErrors`. */
  lines?: string;
  lineErrors?: Record<string, OrderLineErrors>;
}

const MAX_LINES = 200;

export type OrderQuantityParse = { ok: true; value: Qty } | { ok: false; message: string };

/** A line's quantity — a positive decimal with at most three places, the same shape
 *  `app.inventory.quantity.parse_qty` accepts on the wire (`Qty`). Whether a given fraction is
 *  legal for this particular item's UOM is still the server's call; this only rejects what could
 *  never be a quantity at all. */
export function parseOrderQuantity(input: string): OrderQuantityParse {
  const trimmed = input.trim();
  const match = /^\d+(?:\.\d{1,3})?$/.exec(trimmed);
  if (!match) {
    return { ok: false, message: "Quantity is a positive number, up to 3 decimal places." };
  }
  if (qtyToMilli(trimmed) <= 0) {
    return { ok: false, message: "Quantity must be greater than zero." };
  }
  return { ok: true, value: trimmed };
}

interface PriceOverrideParse {
  ok: boolean;
  minor?: number;
  message?: string;
}

/** A line's optional price override, in rupees — never required, unlike a PO line's unit price:
 *  most items already carry their own `selling_price_minor`, and a blank override just uses it. */
function parsePriceOverride(input: string): PriceOverrideParse {
  const cleaned = input.trim();
  if (cleaned === "") return { ok: true };
  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) return { ok: false, message: "Enter an amount in rupees, like 250 or 250.50." };
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  return { ok: true, minor: Number(whole) * 100 + Number(fraction) };
}

/** Validate the whole form, mirroring `OrderIn`/`OrderLineIn`. */
export function validateOrderForm(values: OrderFormValues): OrderFormErrors {
  const errors: OrderFormErrors = {};

  if (values.warehouseId.trim() === "") errors.warehouse = "Choose a warehouse.";
  if (values.customerName.trim() === "" && values.customerPhone.trim() === "") {
    errors.customer = "Enter the customer's name or phone number.";
  }

  if (values.lines.length === 0) {
    errors.lines = "Add at least one line item.";
    return errors;
  }
  if (values.lines.length > MAX_LINES) {
    errors.lines = `An order can hold at most ${MAX_LINES} lines.`;
    return errors;
  }

  const lineErrors: Record<string, OrderLineErrors> = {};
  for (const line of values.lines) {
    const rowErrors: OrderLineErrors = {};
    if (line.inventoryItemId.trim() === "") rowErrors.item = "Look up an item.";
    const quantity = parseOrderQuantity(line.quantity);
    if (!quantity.ok) rowErrors.quantity = quantity.message;
    const price = parsePriceOverride(line.priceOverride);
    if (!price.ok) rowErrors.price = price.message ?? "Enter a valid amount.";
    if (Object.keys(rowErrors).length > 0) lineErrors[line.key] = rowErrors;
  }
  if (Object.keys(lineErrors).length > 0) errors.lineErrors = lineErrors;

  return errors;
}

export function hasOrderFormErrors(errors: OrderFormErrors): boolean {
  return (
    errors.warehouse !== undefined ||
    errors.customer !== undefined ||
    errors.lines !== undefined ||
    (errors.lineErrors !== undefined && Object.keys(errors.lineErrors).length > 0)
  );
}

function toOrderLineIn(line: OrderLineDraft): OrderLineIn {
  const quantity = parseOrderQuantity(line.quantity);
  const price = parsePriceOverride(line.priceOverride);
  const body: OrderLineIn = {
    inventory_item_id: line.inventoryItemId,
    quantity: quantity.ok ? quantity.value : "0",
  };
  if (price.ok && price.minor !== undefined) body.selling_price_minor = price.minor;
  return body;
}

/**
 * Turn a validated form into `POST /orders`'s body. Only ever call once `validateOrderForm`
 * returns no errors — an incomplete line falls back to `"0"`/no override rather than throwing,
 * which is a defensive last line, not a substitute for validating first.
 *
 * Nothing the server derives is here: no `number`, no `status`, no `version`, no line price or
 * total the item itself already prices at.
 */
export function buildOrderPayload(values: OrderFormValues): OrderIn {
  const payload: OrderIn = {
    channel: MODE_CHANNEL[values.mode],
    warehouse_id: values.warehouseId,
    lines: values.lines.map(toOrderLineIn),
  };
  const name = values.customerName.trim();
  const phone = values.customerPhone.trim();
  if (name !== "") payload.customer_name = name;
  if (phone !== "") payload.customer_phone = phone;
  return payload;
}

/**
 * The lines complete enough to price — fed to `quoteOrder` as the operator edits the form, so a
 * half-typed row (no item picked yet, or an unparseable quantity) contributes nothing to the live
 * preview rather than breaking it. `[]` when nothing is quotable yet, which the screen reads as
 * "add a line to see pricing" rather than calling the endpoint with nothing to price.
 */
export function quotableOrderLines(values: OrderFormValues): OrderLineIn[] {
  return values.lines
    .filter((line) => line.inventoryItemId.trim() !== "" && parseOrderQuantity(line.quantity).ok)
    .map(toOrderLineIn);
}
