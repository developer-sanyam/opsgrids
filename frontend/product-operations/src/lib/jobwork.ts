/**
 * Pure rules for the job-work screens (Inventory plan Phase 6) — no React, no network.
 *
 * Quantities are decimal strings end to end. They are compared and summed as integer
 * **milli-units** (`qtyToMilli`), exact for anything a warehouse can hold, and turned back into
 * strings with `milliToQty`. A `number` is never sent to the server and never parsed from a
 * quantity with `parseFloat`/`Number("82.5")` — `0.1 + 0.2 !== 0.3` is not a property a stock
 * reconciliation can have.
 */

import type {
  JobWorkMaterialOut,
  JobWorkReceiptLineIn,
  JobWorkStatus,
  Qty,
  JobWorkReconciliationOut,
} from "@/api/types";
import { milliToQty, qtyToMilli } from "@/lib/quantity";

/** One idempotency key per distinct submission; reuse it when retrying that same submission. */
export function newIdempotencyKey(): string {
  return crypto.randomUUID();
}

/** Units the server refuses a fraction of (`app.inventory.quantity.is_discrete`). */
const DISCRETE_UNITS = new Set(["pcs", "box", "carton", "bundle", "set", "roll"]);

const INPUT = /^\d+(\.\d{1,3})?$/;

export type QtyInput = { ok: true; milli: number; qty: Qty } | { ok: false; error: string };

/**
 * Validate one quantity a person typed. Blank is `0` (an unfilled receipt cell means "none of
 * this kind"); anything that is not digits with at most three decimals is refused with a message
 * for the field, never silently rounded or truncated.
 */
export function parseQtyInput(text: string, uom = ""): QtyInput {
  const trimmed = text.trim();
  if (trimmed === "") return { ok: true, milli: 0, qty: "0" };
  if (!INPUT.test(trimmed)) {
    return { ok: false, error: "Use digits with at most three decimals, e.g. 82.5" };
  }
  const milli = qtyToMilli(trimmed);
  if (DISCRETE_UNITS.has(uom) && milli % 1000 !== 0) {
    return { ok: false, error: `${uom} is counted in whole units` };
  }
  return { ok: true, milli, qty: milliToQty(milli) };
}

export interface ReceiptDraftLine {
  received: string;
  rejected: string;
  shortage: string;
}

export interface ReceiptValidation {
  /** Per material id: a message for that row. */
  errors: Record<string, string>;
  /** The lines to send — only rows with something in them. Empty when invalid. */
  lines: JobWorkReceiptLineIn[];
  /** A message for the form as a whole (nothing entered). */
  formError?: string;
}

/**
 * Check a receipt before sending it: every filled cell parses, and no material is asked for more
 * than is still at the vendor (`received + rejected + shortage <= outstanding`). The server
 * checks the same thing under a lock — this is for telling the person before the round trip, not
 * a substitute for it.
 */
export function validateReceipt(
  materials: JobWorkMaterialOut[],
  draft: Record<string, ReceiptDraftLine>,
  locationFor: (materialId: string) => string | undefined = () => undefined,
): ReceiptValidation {
  const errors: Record<string, string> = {};
  const lines: JobWorkReceiptLineIn[] = [];
  for (const material of materials) {
    const row = draft[material.id];
    if (!row) continue;
    const received = parseQtyInput(row.received, material.uom);
    const rejected = parseQtyInput(row.rejected, material.uom);
    const shortage = parseQtyInput(row.shortage, material.uom);
    const bad = [received, rejected, shortage].find((p) => !p.ok);
    if (bad && !bad.ok) {
      errors[material.id] = bad.error;
      continue;
    }
    if (!received.ok || !rejected.ok || !shortage.ok) continue;
    const total = received.milli + rejected.milli + shortage.milli;
    if (total === 0) continue;
    const outstanding = qtyToMilli(material.outstanding);
    if (total > outstanding) {
      errors[material.id] =
        `${milliToQty(total)} ${material.uom} is more than the ${material.outstanding} ` +
        `${material.uom} still at the vendor`;
      continue;
    }
    const location = locationFor(material.id);
    lines.push({
      material_id: material.id,
      received: received.qty,
      rejected: rejected.qty,
      shortage: shortage.qty,
      ...(location ? { location_id: location } : {}),
    });
  }
  if (Object.keys(errors).length > 0) return { errors, lines: [] };
  if (lines.length === 0) {
    return {
      errors,
      lines: [],
      formError: "Enter a received, rejected or shortage quantity for at least one material.",
    };
  }
  return { errors, lines };
}

/** What is left at the vendor for one material after what has been typed so far, as a decimal
 *  string — negative when the entry is too large (the row shows it, the submit refuses it). */
export function outstandingAfter(material: JobWorkMaterialOut, row: ReceiptDraftLine | undefined): Qty {
  const outstanding = qtyToMilli(material.outstanding);
  if (!row) return material.outstanding;
  const parts = [row.received, row.rejected, row.shortage].map((t) => parseQtyInput(t, material.uom));
  if (parts.some((p) => !p.ok)) return material.outstanding;
  const entered = parts.reduce((sum, p) => sum + (p.ok ? p.milli : 0), 0);
  return milliToQty(outstanding - entered);
}

export interface OrderActions {
  approve: boolean;
  cancel: boolean;
  issue: boolean;
  receive: boolean;
  close: boolean;
}

/** Which actions the order's own status allows (`app.jobwork.domain.JOB_WORK_TRANSITIONS`).
 *  Permission is a separate question, answered per button by `useCan`. */
export function orderActions(status: JobWorkStatus): OrderActions {
  return {
    approve: status === "DRAFT",
    cancel: status === "DRAFT" || status === "APPROVED",
    issue: status === "APPROVED",
    receive: status === "ISSUED" || status === "PARTIALLY_RECEIVED",
    // Offered from the moment material is out: the server answers with the numbers when it does
    // not balance, which is the point of showing the reconciliation next to it.
    close: status === "ISSUED" || status === "PARTIALLY_RECEIVED" || status === "RECEIVED",
  };
}

/** Why `:close` would be refused right now, from the reconciliation the page already holds. */
export function closeBlockReason(reconciliation: JobWorkReconciliationOut): string | null {
  if (!reconciliation.issued) return "Nothing has been issued yet.";
  if (!reconciliation.documents_settled) {
    return `${reconciliation.total_outstanding} is still unaccounted for at the vendor.`;
  }
  if (!reconciliation.ledger_consistent) {
    return "The documents and the stock ledger disagree. Do not close; investigate first.";
  }
  return null;
}

/** `sent = received + rejected + shortage + outstanding`, checked on the exact strings the server
 *  sent — a screen that shows a reconciliation which does not foot must say so. */
export function reconciliationFoots(r: JobWorkReconciliationOut): boolean {
  return (
    qtyToMilli(r.total_sent) ===
    qtyToMilli(r.total_received) +
      qtyToMilli(r.total_rejected) +
      qtyToMilli(r.total_shortage) +
      qtyToMilli(r.total_outstanding)
  );
}
