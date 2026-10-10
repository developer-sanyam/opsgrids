/**
 * The purchase requisition form's state, validation and payload, as pure functions.
 *
 * A requisition line's quantity is in the item's own stock unit and is a decimal **string** with
 * at most three decimals, above zero — the same pattern the server enforces. A discrete unit (a
 * piece, a box) cannot be held in fractions, so for those the form asks for a whole number; the
 * server refuses the fraction too (and refuses to convert one into a purchase order, whose lines
 * are whole units), so this only moves the message next to the field.
 */

import type { RequisitionIn, RequisitionLineIn, RequisitionPatch } from "@/api/types";
import { parseMinor } from "@/lib/money";

export interface RequisitionLineDraft {
  key: string;
  itemId: string | null;
  sku: string;
  name: string;
  /** The item's stock unit, from the picked item. */
  uom: string;
  quantity: string;
  estPrice: string;
  note: string;
}

export interface RequisitionFormValues {
  warehouseId: string;
  neededBy: string;
  reason: string;
  lines: RequisitionLineDraft[];
}

export interface RequisitionLineErrors {
  item?: string;
  quantity?: string;
  estPrice?: string;
}

export interface RequisitionErrors {
  neededBy?: string;
  lines?: string;
  lineErrors?: Record<string, RequisitionLineErrors>;
}

export function emptyRequisitionLine(key: string): RequisitionLineDraft {
  return { key, itemId: null, sku: "", name: "", uom: "", quantity: "", estPrice: "", note: "" };
}

export function emptyRequisition(key: string): RequisitionFormValues {
  return { warehouseId: "", neededBy: "", reason: "", lines: [emptyRequisitionLine(key)] };
}

const DECIMAL = /^\d+(\.\d{1,3})?$/;
const WHOLE = /^\d+$/;

/** Returns the message for a bad quantity, or `undefined` when it is a valid requested quantity. */
export function quantityError(quantity: string, uom: string, discrete: ReadonlySet<string>): string | undefined {
  const value = quantity.trim();
  if (value === "") return "Enter how much is needed.";
  if (!DECIMAL.test(value)) return "Enter a quantity with at most three decimals, like 82.5.";
  if (/^0+(\.0+)?$/.test(value)) return "Enter a quantity above zero.";
  if (discrete.has(uom) && !WHOLE.test(value)) return `${uom} come in whole units — enter a whole number.`;
  return undefined;
}

export function validateRequisition(
  values: RequisitionFormValues,
  options: { today: string; discreteUoms: ReadonlySet<string> },
): RequisitionErrors {
  const errors: RequisitionErrors = {};
  if (values.neededBy !== "" && values.neededBy < options.today) {
    errors.neededBy = "The needed-by date can't be in the past.";
  }
  if (values.lines.length === 0) errors.lines = "Add at least one line.";
  if (values.lines.length > 200) errors.lines = "A requisition holds at most 200 lines.";

  const lineErrors: Record<string, RequisitionLineErrors> = {};
  for (const line of values.lines) {
    const e: RequisitionLineErrors = {};
    if (line.itemId === null) e.item = "Choose the item to request.";
    const quantity = quantityError(line.quantity, line.uom, options.discreteUoms);
    if (quantity !== undefined) e.quantity = quantity;
    const price = parseMinor(line.estPrice);
    if (!price.ok) e.estPrice = price.message;
    if (Object.keys(e).length > 0) lineErrors[line.key] = e;
  }
  if (Object.keys(lineErrors).length > 0) errors.lineErrors = lineErrors;
  return errors;
}

export function hasRequisitionErrors(errors: RequisitionErrors): boolean {
  return errors.neededBy !== undefined || errors.lines !== undefined || errors.lineErrors !== undefined;
}

function linePayload(line: RequisitionLineDraft): RequisitionLineIn {
  const price = parseMinor(line.estPrice);
  return {
    inventory_item_id: line.itemId ?? "",
    quantity: line.quantity.trim(),
    ...(line.estPrice.trim() !== "" && price.ok ? { est_unit_price_minor: price.minor } : {}),
    ...(line.note.trim() !== "" ? { note: line.note.trim() } : {}),
  };
}

export function buildRequisition(values: RequisitionFormValues, requestedBy: string): RequisitionIn {
  return {
    requested_by: requestedBy,
    ...(values.warehouseId !== "" ? { warehouse_id: values.warehouseId } : {}),
    ...(values.neededBy !== "" ? { needed_by: values.neededBy } : {}),
    ...(values.reason.trim() !== "" ? { reason: values.reason.trim() } : {}),
    lines: values.lines.map(linePayload),
  };
}

/** A draft edit: the lines are a full replacement, and a cleared field is sent as `null`. */
export function buildRequisitionPatch(values: RequisitionFormValues): RequisitionPatch {
  return {
    warehouse_id: values.warehouseId === "" ? null : values.warehouseId,
    needed_by: values.neededBy === "" ? null : values.neededBy,
    reason: values.reason.trim() === "" ? null : values.reason.trim(),
    lines: values.lines.map(linePayload),
  };
}
