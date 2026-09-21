/**
 * The purchase order form's state, its money and quantity parsing, its validation, and the
 * payload it builds — as pure functions, separated from `PurchaseOrderForm.tsx` because this is
 * the part worth testing and the part that has to **mirror the server's rules without replacing
 * them** (`PurchaseOrderIn` / `PurchaseOrderPatch` / `PoLineIn` in
 * `services/operations/app/schemas.py`): the server is still the authority, and every payload
 * this module builds is re-checked there.
 *
 * One form, two payload shapes: raising a purchase order posts a full `PurchaseOrderIn` (the
 * server assigns `number`, `status` and `raised_by_id`), while editing a draft sends a
 * `PurchaseOrderPatch` — and when that patch carries `lines` at all, it is a **full replacement**
 * of the order's lines, never a merge, so the form always sends every line back.
 *
 * Money never leaves this file as anything but integer minor units of the PO currency (ADR-0000
 * §8) — `@/lib/format`'s `formatMoney` is the only place a minor amount becomes a display string,
 * and `@/lib/money`'s `parseMinor` is the only place a typed amount becomes one, so this module
 * only ever returns numbers, never a formatted one.
 */

import type { PoLineIn, PurchaseOrderIn, PurchaseOrderPatch } from "@/api/types";
import { parseMinor } from "@/lib/money";
import type { MoneyParse } from "@/lib/money";

/** One line row's form state — every value a string, because that is what an input holds. */
export interface LineDraft {
  /** A stable identity for the row, independent of its position in the array — assigned once
   *  when the row is created (`crypto.randomUUID()` in the component) so removing an earlier row
   *  never makes a later row's error, or the focus target after a failed validation, jump to the
   *  wrong fields. */
  key: string;
  sku: string;
  description: string;
  quantity: string;
  unitPrice: string;
  uom: string;
}

export interface PurchaseOrderFormValues {
  /** `null` until a supplier is chosen. Required to raise an order; fixed once a draft exists —
   *  the form disables the picker and this module never puts `supplier_id` on a patch. */
  supplierId: string | null;
  supplierName: string | null;
  warehouse: string;
  /** Blank leaves the currency alone on an edit, or lets the server fall back to the supplier's
   *  currency on create — either way, blank means "don't send this field", never `"INR"` assumed
   *  here. */
  currency: string;
  expectedDate: string;
  notes: string;
  lines: LineDraft[];
}

export type LineField = "sku" | "quantity" | "unitPrice";
export type LineErrors = Partial<Record<LineField, string>>;

export interface PurchaseOrderErrors {
  supplier?: string;
  warehouse?: string;
  expectedDate?: string;
  /** Set when there are no lines at all, or more than the server's 200-line cap — a single row's
   *  own problem lives in `lineErrors`. */
  lines?: string;
  /** Keyed by `LineDraft.key`, so a row keeps its own errors as siblings are added or removed. */
  lineErrors?: Record<string, LineErrors>;
}

export function emptyLine(key: string): LineDraft {
  return { key, sku: "", description: "", quantity: "", unitPrice: "", uom: "" };
}

export function emptyPurchaseOrder(firstLineKey: string): PurchaseOrderFormValues {
  return {
    supplierId: null,
    supplierName: null,
    warehouse: "",
    currency: "",
    expectedDate: "",
    notes: "",
    lines: [emptyLine(firstLineKey)],
  };
}

/* --------------------------------- money --------------------------------- */

/** A line's unit price is required — `PoLineIn.unit_price_minor` defaults to 0 on the wire, but a
 *  procurement line with no price typed is an unfinished row, not a free one. */
function parseUnitPrice(input: string): MoneyParse {
  return parseMinor(input, {
    required: true,
    emptyMessage: "Enter a unit price.",
    invalidMessage: "Enter an amount in rupees, like 2250 or 2250.50.",
  });
}

export type QuantityParse = { ok: true; value: number } | { ok: false; message: string };

/** A line's quantity — a whole number greater than zero, matching `PoLineIn.quantity` (`int`,
 *  `ge=1`) exactly: the server has no notion of a fractional unit. */
export function parseQuantity(input: string): QuantityParse {
  const trimmed = input.trim();
  if (trimmed === "" || !/^\d+$/.test(trimmed)) {
    return { ok: false, message: "Quantity is a whole number greater than zero." };
  }
  const value = Number(trimmed);
  if (!Number.isSafeInteger(value) || value <= 0) {
    return { ok: false, message: "Quantity is a whole number greater than zero." };
  }
  return { ok: true, value };
}

/** A line's amount in minor units — `quantity * unit_price_minor`, exactly how the server derives
 *  it (`PoLineItem`'s own docstring). `null` while the row is not yet a valid line, so a
 *  half-typed row contributes nothing to the order total rather than a wrong number. */
export function lineAmountMinor(line: LineDraft): number | null {
  const quantity = parseQuantity(line.quantity);
  const price = parseUnitPrice(line.unitPrice);
  if (!quantity.ok || !price.ok) return null;
  return quantity.value * price.minor;
}

/** The order total in minor units — the sum of every line's amount, computed the same way the
 *  server sums into `PurchaseOrderOut.line_items`, so what the form shows before submitting is
 *  what the server will store. */
export function orderTotalMinor(lines: readonly LineDraft[]): number {
  return lines.reduce((sum, line) => sum + (lineAmountMinor(line) ?? 0), 0);
}

/* ------------------------------- validation ------------------------------- */

const MAX_LINES = 200;

export interface ValidateOptions {
  /** `"create"` requires a supplier and refuses an expected date in the past — both rules
   *  `PurchaseOrderIn` enforces itself. `"edit"` has neither: a draft's supplier can't be changed
   *  from this form, and `PurchaseOrderPatch` carries no such date rule. */
  mode: "create" | "edit";
  /** Today, as an ISO calendar date — threaded in rather than read from the clock so this stays a
   *  pure function. Pass `@/lib/now`'s `today()`. Unused outside `mode: "create"`. */
  today: string;
}

/**
 * Validate the whole form, mirroring `PurchaseOrderIn` / `PurchaseOrderPatch` / `PoLineIn` —
 * never replacing the server's own checks, which run again regardless. A line's `description` /
 * `uom` carry no rule here on purpose: the schema defaults both to `""`, so nothing in this form
 * can be stricter than a field the server itself accepts blank.
 */
export function validatePurchaseOrder(
  values: PurchaseOrderFormValues,
  options: ValidateOptions,
): PurchaseOrderErrors {
  const errors: PurchaseOrderErrors = {};

  if (options.mode === "create" && !values.supplierId) {
    errors.supplier = "Choose a supplier.";
  }
  if (values.warehouse.trim() === "") errors.warehouse = "Choose a warehouse.";

  if (values.expectedDate.trim() === "") {
    errors.expectedDate = "Pick an expected delivery date.";
  } else if (options.mode === "create" && values.expectedDate < options.today) {
    errors.expectedDate = "The expected date can't be in the past.";
  }

  if (values.lines.length === 0) {
    errors.lines = "Add at least one line item.";
    return errors;
  }
  if (values.lines.length > MAX_LINES) {
    errors.lines = `A purchase order can hold at most ${MAX_LINES} lines.`;
    return errors;
  }

  const lineErrors: Record<string, LineErrors> = {};
  for (const line of values.lines) {
    const rowErrors: LineErrors = {};
    if (line.sku.trim() === "") rowErrors.sku = "Enter a SKU.";
    const quantity = parseQuantity(line.quantity);
    if (!quantity.ok) rowErrors.quantity = quantity.message;
    const price = parseUnitPrice(line.unitPrice);
    if (!price.ok) rowErrors.unitPrice = price.message;
    if (Object.keys(rowErrors).length > 0) lineErrors[line.key] = rowErrors;
  }
  if (Object.keys(lineErrors).length > 0) errors.lineErrors = lineErrors;

  return errors;
}

export function hasErrors(errors: PurchaseOrderErrors): boolean {
  return (
    errors.supplier !== undefined ||
    errors.warehouse !== undefined ||
    errors.expectedDate !== undefined ||
    errors.lines !== undefined ||
    (errors.lineErrors !== undefined && Object.keys(errors.lineErrors).length > 0)
  );
}

/* --------------------------------- payload -------------------------------- */

function toLineIn(line: LineDraft): PoLineIn {
  const quantity = parseQuantity(line.quantity);
  const price = parseUnitPrice(line.unitPrice);
  return {
    sku: line.sku.trim(),
    description: line.description.trim(),
    quantity: quantity.ok ? quantity.value : 0,
    unit_price_minor: price.ok ? price.minor : 0,
    uom: line.uom.trim(),
  };
}

export interface BuildCreateOptions {
  mode: "create";
  /** `useAuth().user.full_name` — display name only, never sent when blank. */
  raisedBy: string;
}

export interface BuildEditOptions {
  mode: "edit";
}

/**
 * Turn a validated form into the request body for `POST /operations/purchase-orders` or
 * `PATCH /operations/purchase-orders/{id}`. Only ever call this once `validatePurchaseOrder`
 * returns no errors — a line's quantity and unit price fall back to `0` rather than throwing,
 * which is a defensive last line, not a substitute for validating first.
 *
 * Nothing the server derives and refuses is here: no PO number, no `status`, no PO total, no
 * line `amount_minor`, no `category`, no `raised_by_id`. An edit never resends `supplier_id` —
 * the form has no control that can change it — and always resends every line, since a patch's
 * `lines` is a full replacement.
 */
export function buildPurchaseOrderPayload(
  values: PurchaseOrderFormValues,
  options: BuildCreateOptions,
): PurchaseOrderIn;
export function buildPurchaseOrderPayload(
  values: PurchaseOrderFormValues,
  options: BuildEditOptions,
): PurchaseOrderPatch;
export function buildPurchaseOrderPayload(
  values: PurchaseOrderFormValues,
  options: BuildCreateOptions | BuildEditOptions,
): PurchaseOrderIn | PurchaseOrderPatch {
  const lines = values.lines.map(toLineIn);
  const warehouse = values.warehouse.trim();
  const currency = values.currency.trim().toUpperCase();
  const notes = values.notes.trim();

  if (options.mode === "create") {
    const payload: PurchaseOrderIn = {
      supplier_id: values.supplierId ?? "",
      warehouse,
      expected_date: values.expectedDate,
      lines,
    };
    if (currency !== "") payload.currency = currency;
    if (notes !== "") payload.notes = notes;
    const raisedBy = options.raisedBy.trim();
    if (raisedBy !== "") payload.raised_by = raisedBy;
    return payload;
  }

  const payload: PurchaseOrderPatch = {
    warehouse,
    expected_date: values.expectedDate,
    lines,
  };
  if (currency !== "") payload.currency = currency;
  if (notes !== "") payload.notes = notes;
  return payload;
}
