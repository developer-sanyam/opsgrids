import type { TransferIn, TransferLineIn } from "@/api/movementTypes";
import { parseQty } from "./movementQty";
import { isUuid } from "./uuid";

const MAX_LINES = 100;

export interface TransferLineValues {
  /** A stable React key, never sent. */
  key: string;
  inventoryItemId: string;
  /** The item's unit — shown beside the quantity and decides whole-unit validation. */
  uom: string;
  fromLocationId: string;
  quantity: string;
  lotId: string;
  rollId: string;
}

export interface TransferFormValues {
  fromWarehouseId: string;
  toWarehouseId: string;
  lines: TransferLineValues[];
  note: string;
  expectedOn: string;
}

export interface TransferLineErrors {
  item?: string;
  location?: string;
  quantity?: string;
  lot?: string;
  roll?: string;
  line?: string;
}

export interface TransferFormErrors {
  fromWarehouse?: string;
  toWarehouse?: string;
  lines?: string;
  lineErrors?: Record<string, TransferLineErrors>;
}

export function blankTransferLine(key: string): TransferLineValues {
  return { key, inventoryItemId: "", uom: "", fromLocationId: "", quantity: "", lotId: "", rollId: "" };
}

function lineBody(line: TransferLineValues, quantity: string): TransferLineIn {
  return {
    inventory_item_id: line.inventoryItemId,
    from_location_id: line.fromLocationId,
    quantity,
    ...(line.lotId.trim() !== "" ? { lot_id: line.lotId.trim() } : {}),
    ...(line.rollId.trim() !== "" ? { roll_id: line.rollId.trim() } : {}),
  };
}

export type TransferFormResult =
  | { ok: true; payload: TransferIn }
  | { ok: false; errors: TransferFormErrors };

/**
 * Validate a new transfer the way `TransferIn` does, so the obvious mistakes are said beside the
 * control before a round trip. Quantities are positive decimals with at most three places, and
 * whole numbers where the item's unit is discrete. Two lines for the same item, bin, lot and roll
 * are refused with the server's own advice (combine them). The server still validates all of it.
 */
export function validateTransferForm(
  values: TransferFormValues,
  discreteUoms: ReadonlySet<string>,
): TransferFormResult {
  const errors: TransferFormErrors = {};

  if (values.fromWarehouseId === "") errors.fromWarehouse = "Choose the warehouse the stock leaves.";
  if (values.toWarehouseId === "") errors.toWarehouse = "Choose the warehouse the stock goes to.";
  if (
    values.fromWarehouseId !== "" &&
    values.toWarehouseId !== "" &&
    values.fromWarehouseId === values.toWarehouseId
  ) {
    errors.toWarehouse = "The destination must differ from the source warehouse.";
  }

  if (values.lines.length === 0) errors.lines = "Add at least one line.";
  if (values.lines.length > MAX_LINES) errors.lines = `A transfer holds at most ${MAX_LINES} lines.`;

  const lineErrors: Record<string, TransferLineErrors> = {};
  const lines: TransferLineIn[] = [];
  const seen = new Map<string, string>();

  for (const line of values.lines) {
    const problems: TransferLineErrors = {};
    if (line.inventoryItemId === "") problems.item = "Choose an item.";
    if (line.fromLocationId === "") problems.location = "Choose the bin it is taken from.";
    const quantity = parseQty(line.quantity, { whole: discreteUoms.has(line.uom), uom: line.uom });
    if (!quantity.ok) problems.quantity = quantity.message;
    if (line.lotId.trim() !== "" && !isUuid(line.lotId)) problems.lot = "A lot id looks like 3f2b8c1e-….";
    if (line.rollId.trim() !== "" && !isUuid(line.rollId)) problems.roll = "A roll id looks like 3f2b8c1e-….";

    if (line.inventoryItemId !== "" && line.fromLocationId !== "") {
      const fingerprint = [line.inventoryItemId, line.fromLocationId, line.lotId.trim(), line.rollId.trim()].join("|");
      if (seen.has(fingerprint)) problems.line = "This item is already on another line from the same bin. Combine the lines.";
      else seen.set(fingerprint, line.key);
    }

    if (Object.keys(problems).length > 0) lineErrors[line.key] = problems;
    else if (quantity.ok) lines.push(lineBody(line, quantity.value));
  }
  if (Object.keys(lineErrors).length > 0) errors.lineErrors = lineErrors;

  if (Object.keys(errors).length > 0) return { ok: false, errors };

  return {
    ok: true,
    payload: {
      from_warehouse_id: values.fromWarehouseId,
      to_warehouse_id: values.toWarehouseId,
      lines,
      note: values.note.trim(),
      ...(values.expectedOn !== "" ? { expected_on: values.expectedOn } : {}),
    },
  };
}
