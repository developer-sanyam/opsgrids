/**
 * The goods-receipt form's rules, free of React.
 *
 * Quantities are decimal strings all the way to the request (ADR-0014 §2). Arithmetic goes
 * through integer milli-units (`lib/quantity.ts`), and the one place a product can leave the safe
 * integer range — an entered quantity times a container ratio — is done in `BigInt`. Nothing here
 * turns a quantity into a float.
 *
 * This mirrors the server's validation so a wrong figure is caught beside the field; the server
 * re-checks all of it under the purchase order's lock and is the authority.
 */

import type { GoodsReceiptIn, ReceiptLineIn, ReceiptRollIn, ReceivingLineOut } from "@/api/types";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";

/** The server's `NonNegQty` pattern — a non-negative decimal with at most three decimals. */
export const NON_NEG_QTY = /^\d+(\.\d{1,3})?$/;
const CODE = /^[A-Za-z0-9._/-]+$/;
const WHOLE = /^\d+$/;

/* --------------------------------- drafts --------------------------------- */

export interface RollDraft {
  key: string;
  rollNo: string;
  primary: string;
  secondary: string;
  secondaryUom: string;
  widthMm: string;
  gsm: string;
  shade: string;
  grade: string;
}

export interface ReceiptLineDraft {
  /** `purchase_order_line_id`. */
  lineId: string;
  quantity: string;
  /** The entered unit; `""` means the item's stock unit. */
  uom: string;
  /** `""` means the item's default location. */
  locationId: string;
  hold: boolean;
  closeShort: boolean;
  lotNo: string;
  supplierLotNo: string;
  manufacturedOn: string;
  expiresOn: string;
  rolls: RollDraft[];
}

export function emptyLineDraft(lineId: string): ReceiptLineDraft {
  return {
    lineId,
    quantity: "",
    uom: "",
    locationId: "",
    hold: false,
    closeShort: false,
    lotNo: "",
    supplierLotNo: "",
    manufacturedOn: "",
    expiresOn: "",
    rolls: [],
  };
}

let rollSeq = 0;
/** A stable identity for a roll row, so removing an earlier one never moves a later one's errors. */
export function nextRollKey(): string {
  rollSeq += 1;
  return `roll-${rollSeq}`;
}

export function emptyRollDraft(key: string): RollDraft {
  return { key, rollNo: "", primary: "", secondary: "", secondaryUom: "", widthMm: "", gsm: "", shade: "", grade: "" };
}

/* ------------------------------- arithmetic ------------------------------- */

/** An entered quantity as milli-units, or `null` when it is not a non-negative decimal string. */
export function enteredMilli(value: string): number | null {
  const trimmed = value.trim();
  return NON_NEG_QTY.test(trimmed) ? qtyToMilli(trimmed) : null;
}

/**
 * `entered` milli-units of a container unit, as milli-units of the stock unit
 * (`1 box = numerator / denominator` stock units). `exact` is false when the ratio leaves a
 * fraction of a milli-unit, which the server refuses rather than rounds.
 */
export function toStockMilli(
  entered: number,
  ratio: { numerator: number; denominator: number } | null,
): { milli: number; exact: boolean } {
  if (ratio === null) return { milli: entered, exact: true };
  const product = BigInt(entered) * BigInt(ratio.numerator);
  const divisor = BigInt(ratio.denominator);
  return { milli: Number(product / divisor), exact: product % divisor === 0n };
}

function ratioFor(line: ReceivingLineOut, uom: string): { numerator: number; denominator: number } | null {
  if (uom === "" || uom === line.stock_uom) return null;
  const found = line.conversions.find((c) => c.uom === uom);
  return found ? { numerator: found.numerator, denominator: found.denominator } : null;
}

function show(milli: number, uom: string | null): string {
  return `${formatQty(milliToQty(milli))}${uom ? ` ${uom}` : ""}`;
}

/* -------------------------------- analysis -------------------------------- */

export interface RollErrors {
  rollNo?: string;
  primary?: string;
  secondary?: string;
  secondaryUom?: string;
  widthMm?: string;
  gsm?: string;
}

export interface LineErrors {
  quantity?: string;
  expiresOn?: string;
  manufacturedOn?: string;
  lotNo?: string;
  /** About the rolls as a set: none entered, or they do not add up to the line quantity. */
  rolls?: string;
  roll: Record<string, RollErrors>;
}

export interface LineAnalysis {
  /** Does this line take part in the receipt (a quantity was entered)? */
  included: boolean;
  errors: LineErrors;
  /** Non-blocking hints: short, over tolerance-free, closing short. */
  hints: string[];
  /** The line's quantity in the stock unit, when it is a valid figure. */
  stockMilli: number | null;
  rollsMilli: number;
}

export function hasLineErrors(errors: LineErrors): boolean {
  return (
    errors.quantity !== undefined ||
    errors.expiresOn !== undefined ||
    errors.manufacturedOn !== undefined ||
    errors.lotNo !== undefined ||
    errors.rolls !== undefined ||
    Object.values(errors.roll).some((r) => Object.keys(r).length > 0)
  );
}

function analyseRolls(line: ReceiptLineDraft, errors: LineErrors): number {
  let total = 0;
  const seen = new Set<string>();
  for (const roll of line.rolls) {
    const e: RollErrors = {};
    const no = roll.rollNo.trim();
    if (no !== "") {
      if (!CODE.test(no) || no.length > 60) e.rollNo = "Letters, numbers, dots, slashes and hyphens only.";
      else if (seen.has(no)) e.rollNo = "Two rolls share this number.";
      seen.add(no);
    }
    const primary = enteredMilli(roll.primary);
    if (primary === null) e.primary = "Enter the length, like 82.5.";
    else if (primary === 0) e.primary = "A roll has to hold something.";
    else total += primary;

    const secondary = roll.secondary.trim();
    const secondaryUom = roll.secondaryUom.trim();
    if (secondary !== "" && !NON_NEG_QTY.test(secondary)) e.secondary = "Enter the weight, like 14.8.";
    else if (secondary !== "" && secondaryUom === "") e.secondaryUom = "Name the unit it was weighed in.";
    else if (secondary === "" && secondaryUom !== "") e.secondary = "Enter the measured weight, or clear the unit.";
    if (roll.widthMm.trim() !== "" && (!WHOLE.test(roll.widthMm.trim()) || Number(roll.widthMm) < 1 || Number(roll.widthMm) > 100000)) {
      e.widthMm = "A whole number of millimetres.";
    }
    if (roll.gsm.trim() !== "" && (!WHOLE.test(roll.gsm.trim()) || Number(roll.gsm) < 1 || Number(roll.gsm) > 100000)) {
      e.gsm = "A whole number.";
    }
    if (Object.keys(e).length > 0) errors.roll[roll.key] = e;
  }
  return total;
}

/**
 * Everything the form can say about one line before the server does. `ctx` is the receiving
 * context's own figures for the line (ordered, received, remaining, the tolerance ceiling).
 */
export function analyseLine(line: ReceiptLineDraft, ctx: ReceivingLineOut): LineAnalysis {
  const errors: LineErrors = { roll: {} };
  const hints: string[] = [];
  const quantity = line.quantity.trim();
  const unit = ctx.stock_uom;

  if (quantity === "") {
    return { included: false, errors, hints, stockMilli: null, rollsMilli: 0 };
  }

  const entered = enteredMilli(quantity);
  let stockMilli: number | null = null;
  if (entered === null) {
    errors.quantity = "Enter a quantity with at most three decimals, like 82.5.";
  } else {
    const converted = toStockMilli(entered, ratioFor(ctx, line.uom));
    stockMilli = converted.milli;
    if (!converted.exact) {
      errors.quantity = `That doesn't convert exactly into ${unit ?? "the stock unit"}. Enter a quantity that does.`;
    } else if (entered === 0 && !line.closeShort) {
      errors.quantity = "Enter a quantity above zero, or tick Close short to stop expecting more.";
    } else {
      const max = qtyToMilli(ctx.max_receivable_qty);
      const remaining = qtyToMilli(ctx.remaining_qty);
      if (stockMilli > max) {
        errors.quantity = `That is more than can be received: at most ${show(max, unit)} (the order plus ${ctx.tolerance_pct}% tolerance).`;
      } else if (stockMilli > remaining) {
        hints.push(`${show(stockMilli - remaining, unit)} over what the order still expects, within the ${ctx.tolerance_pct}% tolerance.`);
      } else if (line.closeShort && entered > 0) {
        hints.push(`${show(remaining - stockMilli, unit)} will no longer be expected on this line.`);
      } else if (stockMilli < remaining && entered > 0) {
        hints.push(`${show(remaining - stockMilli, unit)} would still be expected after this.`);
      }
    }
  }

  const receiving = stockMilli !== null && stockMilli > 0;

  if (receiving && (ctx.track_lot || ctx.track_expiry)) {
    const lotNo = line.lotNo.trim();
    if (lotNo !== "" && (!CODE.test(lotNo) || lotNo.length > 60)) {
      errors.lotNo = "Letters, numbers, dots, slashes and hyphens only.";
    }
    if (ctx.track_expiry && line.expiresOn === "") errors.expiresOn = "This item expires: enter the expiry date.";
    if (line.manufacturedOn !== "" && line.expiresOn !== "" && line.manufacturedOn > line.expiresOn) {
      errors.manufacturedOn = "Manufactured after it expires?";
    }
  }

  let rollsMilli = 0;
  if (receiving && ctx.track_roll) {
    if (line.rolls.length === 0) {
      errors.rolls = "Add the rolls in this delivery.";
    } else {
      rollsMilli = analyseRolls(line, errors);
      if (stockMilli !== null && rollsMilli !== stockMilli && errors.quantity === undefined) {
        errors.rolls = `The rolls add up to ${show(rollsMilli, unit)}, but this line receives ${show(stockMilli, unit)}.`;
      }
    }
  }

  return { included: true, errors, hints, stockMilli, rollsMilli };
}

/** The effective hold: an item that tracks quality is always received into hold. */
export function effectiveHold(line: ReceiptLineDraft, ctx: ReceivingLineOut): boolean {
  return ctx.track_quality || line.hold;
}

/* --------------------------------- payload -------------------------------- */

function text(value: string): string | null {
  const trimmed = value.trim();
  return trimmed === "" ? null : trimmed;
}

function rollPayload(roll: RollDraft): ReceiptRollIn {
  const secondary = text(roll.secondary);
  return {
    ...(text(roll.rollNo) !== null ? { roll_no: roll.rollNo.trim() } : {}),
    primary_qty: roll.primary.trim(),
    ...(secondary !== null ? { secondary_qty: secondary, secondary_uom: roll.secondaryUom.trim() } : {}),
    ...(text(roll.widthMm) !== null ? { width_mm: Number(roll.widthMm) } : {}),
    ...(text(roll.gsm) !== null ? { gsm: Number(roll.gsm) } : {}),
    ...(text(roll.shade) !== null ? { shade: roll.shade.trim() } : {}),
    ...(text(roll.grade) !== null ? { grade: roll.grade.trim() } : {}),
  };
}

function linePayload(line: ReceiptLineDraft, ctx: ReceivingLineOut, stockMilli: number): ReceiptLineIn {
  const receiving = stockMilli > 0;
  const lotFields = {
    ...(text(line.lotNo) !== null ? { lot_no: line.lotNo.trim() } : {}),
    ...(text(line.supplierLotNo) !== null ? { supplier_lot_no: line.supplierLotNo.trim() } : {}),
    ...(text(line.manufacturedOn) !== null ? { manufactured_on: line.manufacturedOn } : {}),
    ...(text(line.expiresOn) !== null ? { expires_on: line.expiresOn } : {}),
  };
  const withLot = receiving && (ctx.track_lot || ctx.track_expiry) && Object.keys(lotFields).length > 0;
  return {
    purchase_order_line_id: line.lineId,
    quantity: line.quantity.trim(),
    ...(line.uom !== "" && line.uom !== ctx.stock_uom ? { uom: line.uom } : {}),
    ...(receiving && line.locationId !== "" ? { location_id: line.locationId } : {}),
    ...(receiving && effectiveHold(line, ctx) ? { hold: true } : {}),
    ...(line.closeShort ? { close_short: true } : {}),
    ...(withLot ? { lot: lotFields } : {}),
    ...(receiving && ctx.track_roll ? { rolls: line.rolls.map(rollPayload) } : {}),
  };
}

export interface ReceiptHeaderDraft {
  receivedOn: string;
  deliveryNote: string;
  note: string;
}

export interface ReceiptFormResult {
  /** Present only when nothing is wrong. */
  payload: GoodsReceiptIn | null;
  /** Per purchase-order line id. */
  analyses: Record<string, LineAnalysis>;
  /** The line ids that are part of the request, in request order (`lines.N` on the wire). */
  includedLineIds: string[];
  /** A problem with the form as a whole. */
  formError: string | null;
}

/** Validates the whole form and, when clean, builds the request. */
export function buildReceipt(
  purchaseOrderId: string,
  header: ReceiptHeaderDraft,
  lines: ReceiptLineDraft[],
  context: ReceivingLineOut[],
): ReceiptFormResult {
  const analyses: Record<string, LineAnalysis> = {};
  const includedLineIds: string[] = [];
  const bodies: ReceiptLineIn[] = [];
  let invalid = false;

  for (const draft of lines) {
    const ctx = context.find((c) => c.purchase_order_line_id === draft.lineId);
    if (!ctx) continue;
    const analysis = analyseLine(draft, ctx);
    analyses[draft.lineId] = analysis;
    if (!analysis.included) continue;
    includedLineIds.push(draft.lineId);
    if (hasLineErrors(analysis.errors) || analysis.stockMilli === null) {
      invalid = true;
      continue;
    }
    bodies.push(linePayload(draft, ctx, analysis.stockMilli));
  }

  if (includedLineIds.length === 0) {
    return {
      payload: null,
      analyses,
      includedLineIds,
      formError: "Enter the quantity received on at least one line.",
    };
  }
  if (invalid) return { payload: null, analyses, includedLineIds, formError: null };

  return {
    payload: {
      purchase_order_id: purchaseOrderId,
      ...(header.receivedOn !== "" ? { received_on: header.receivedOn } : {}),
      ...(text(header.deliveryNote) !== null ? { delivery_note: header.deliveryNote.trim() } : {}),
      ...(text(header.note) !== null ? { note: header.note.trim() } : {}),
      lines: bodies,
    },
    analyses,
    includedLineIds,
    formError: null,
  };
}

/* ----------------------------- server problems ----------------------------- */

export interface ServerLineProblems {
  /** Sub-field `quantity`, shown on the quantity control. */
  quantity?: string;
  /** Everything else the server said about the line, in order. */
  other: string[];
}

/**
 * Sorts a problem document's field errors onto the lines they name. The wire spells a nested path
 * `lines.2.rolls.0.primary_qty`; `lines[2].rolls[0]` is read the same way. Index N is the Nth
 * line *of the request*, so it maps through `includedLineIds` back to the purchase order line.
 */
export function serverLineProblems(
  fieldErrors: Record<string, string>,
  includedLineIds: string[],
): { byLine: Record<string, ServerLineProblems>; header: Record<string, string> } {
  const byLine: Record<string, ServerLineProblems> = {};
  const header: Record<string, string> = {};
  for (const [field, message] of Object.entries(fieldErrors)) {
    const match = /^lines(?:\.|\[)(\d+)\]?(?:\.(.+))?$/.exec(field);
    if (!match) {
      header[field] = message;
      continue;
    }
    const lineId = includedLineIds[Number(match[1])];
    if (lineId === undefined) {
      header[field] = message;
      continue;
    }
    const entry = (byLine[lineId] ??= { other: [] });
    const sub = match[2] ?? "";
    if (sub === "quantity") entry.quantity = message;
    else entry.other.push(message);
  }
  return { byLine, header };
}
