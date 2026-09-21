/**
 * The "New purchase order" form's state, its rupees→minor money conversion, and its validation,
 * as pure functions — separated from `NewPurchaseOrderDialog.tsx` because this is the part worth
 * testing and the part that has to **mirror the server's rules without replacing them**
 * (`PurchaseOrderIn` / `PoLineIn` in `services/operations/app/schemas.py`): the server is still
 * the authority, and every payload this module builds is re-checked there.
 *
 * Money never leaves this file as anything but integer minor units of the PO currency (ADR-0000
 * §8) — `@/lib/format`'s `formatMoney` is the only place a minor amount becomes a display string,
 * so this module only ever returns numbers, never a formatted one.
 */

import type { PoStatus, PurchaseOrderCreateIn } from "@/api/types";

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

/** `PurchaseOrderIn.status` narrowed to what a client may raise a PO as — `received` and
 *  `closed` only ever happen through the separate receiving endpoint. */
export type CreatableStatus = Extract<PoStatus, "draft" | "approved">;

export interface PurchaseOrderFormValues {
  number: string;
  supplierCode: string | null;
  supplierName: string | null;
  status: CreatableStatus;
  warehouse: string;
  currency: string;
  expectedDate: string;
  notes: string;
  lines: LineDraft[];
}

export type LineField = "sku" | "quantity" | "unitPrice";
export type LineErrors = Partial<Record<LineField, string>>;

export interface PurchaseOrderErrors {
  number?: string;
  supplier?: string;
  expectedDate?: string;
  /** Set only when there are no lines at all — a single row's own problem lives in `lineErrors`. */
  lines?: string;
  /** Keyed by `LineDraft.key`, so a row keeps its own errors as siblings are added or removed. */
  lineErrors?: Record<string, LineErrors>;
}

export function emptyLine(key: string): LineDraft {
  return { key, sku: "", description: "", quantity: "", unitPrice: "", uom: "" };
}

export function emptyPurchaseOrder(firstLineKey: string): PurchaseOrderFormValues {
  return {
    number: "",
    supplierCode: null,
    supplierName: null,
    status: "draft",
    warehouse: "",
    currency: "INR",
    expectedDate: "",
    notes: "",
    lines: [emptyLine(firstLineKey)],
  };
}

/* --------------------------------- money --------------------------------- */

export type MoneyParse = { ok: true; minor: number } | { ok: false; message: string };

/**
 * Rupees, typed by a person, to integer minor units — the inverse of `formatMoney`. Parsed off
 * the digits of the string and never by multiplying a float by 100: `2250.35 * 100` is
 * `225034.99999999997` in IEEE 754, and a `Math.round` papering over that works until the amount
 * is large enough that it doesn't. A unit price of `2250` rupees becomes `225000` minor units —
 * exactly the number the API contract's own example carries for ₹2,250.00.
 */
export function parseRupeesToMinor(input: string): MoneyParse {
  const cleaned = input.trim().replace(/[\s,₹]/g, "");
  if (cleaned === "") return { ok: false, message: "Enter a unit price." };

  const match = /^(\d+)(?:\.(\d{1,2}))?$/.exec(cleaned);
  if (!match) {
    return { ok: false, message: "Enter an amount in rupees, like 2250 or 2250.50." };
  }
  const whole = match[1] ?? "0";
  const fraction = (match[2] ?? "").padEnd(2, "0");
  const minor = Number(whole) * 100 + Number(fraction);
  if (!Number.isSafeInteger(minor)) return { ok: false, message: "That amount is too large." };
  return { ok: true, minor };
}

export type QuantityParse = { ok: true; value: number } | { ok: false; message: string };

/** A line's quantity — a whole number greater than zero, matching `PoLineIn.quantity` (`int`,
 *  `gt=0`) exactly: the server has no notion of a fractional unit. */
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
  const price = parseRupeesToMinor(line.unitPrice);
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

/**
 * Validate the whole form, mirroring `PurchaseOrderIn` / `PoLineIn` — never replacing the
 * server's own checks, which run again regardless. `warehouse` and a line's `description` /
 * `uom` carry no rule here on purpose: the schema defaults every one of them to `""`, so nothing
 * in this form can be stricter than a field the server itself accepts blank.
 */
export function validatePurchaseOrder(values: PurchaseOrderFormValues): PurchaseOrderErrors {
  const errors: PurchaseOrderErrors = {};

  if (values.number.trim() === "") errors.number = "Give the purchase order a number.";
  if (!values.supplierCode) errors.supplier = "Choose a supplier.";
  if (values.expectedDate.trim() === "") errors.expectedDate = "Pick an expected delivery date.";

  if (values.lines.length === 0) {
    errors.lines = "Add at least one line item.";
    return errors;
  }

  const lineErrors: Record<string, LineErrors> = {};
  for (const line of values.lines) {
    const rowErrors: LineErrors = {};
    if (line.sku.trim() === "") rowErrors.sku = "Enter a SKU.";
    const quantity = parseQuantity(line.quantity);
    if (!quantity.ok) rowErrors.quantity = quantity.message;
    const price = parseRupeesToMinor(line.unitPrice);
    if (!price.ok) rowErrors.unitPrice = price.message;
    if (Object.keys(rowErrors).length > 0) lineErrors[line.key] = rowErrors;
  }
  if (Object.keys(lineErrors).length > 0) errors.lineErrors = lineErrors;

  return errors;
}

export function hasErrors(errors: PurchaseOrderErrors): boolean {
  return (
    errors.number !== undefined ||
    errors.supplier !== undefined ||
    errors.expectedDate !== undefined ||
    errors.lines !== undefined ||
    (errors.lineErrors !== undefined && Object.keys(errors.lineErrors).length > 0)
  );
}

/* --------------------------------- payload -------------------------------- */

/**
 * Turn a validated form into `POST /operations/purchase-orders`'s request body. Only ever call
 * this once `validatePurchaseOrder` returns no errors — a line's quantity and unit price fall
 * back to `0` rather than throwing, which is a defensive last line, not a substitute for
 * validating first.
 *
 * Nothing the server derives and refuses is here: no PO total, no line `amount_minor`, no
 * `category`, no `raised_by_id`.
 */
export function buildPurchaseOrderPayload(values: PurchaseOrderFormValues): PurchaseOrderCreateIn {
  const lines = values.lines.map((line) => {
    const quantity = parseQuantity(line.quantity);
    const price = parseRupeesToMinor(line.unitPrice);
    return {
      sku: line.sku.trim(),
      description: line.description.trim(),
      quantity: quantity.ok ? quantity.value : 0,
      unit_price_minor: price.ok ? price.minor : 0,
      uom: line.uom.trim(),
    };
  });

  const payload: PurchaseOrderCreateIn = {
    number: values.number.trim(),
    status: values.status,
    warehouse: values.warehouse.trim(),
    currency: values.currency,
    expected_date: values.expectedDate,
    lines,
  };
  if (values.supplierCode) payload.supplier_code = values.supplierCode;
  const notes = values.notes.trim();
  if (notes !== "") payload.notes = notes;
  return payload;
}
