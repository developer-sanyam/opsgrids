/**
 * The put-away screen's rules that need no React: what a bin label may look like, what quantity is
 * acceptable, which stock matches a search, and how a confirmed put-away becomes the request body
 * that the queue then freezes.
 */

import type { PutawayIn, PutawayOut, PutawaySuggestion, Qty, StockRowOut } from "@/api/types";
import type { GeoFix } from "@/lib/geo";
import type { PutawaySummary } from "@/lib/putawayQueue";
import type { QueuedReason } from "@/lib/putawaySync";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";

/** The shape of every barcode the server will store (`LocationIn.barcode`): printable ASCII with no
 *  space and no "/". A label outside it can never match a bin, so say so before sending it. */
const BARCODE_RE = /^[!-.0-~]{1,64}$/;
const QTY_RE = /^\d+(\.\d{1,3})?$/;

/** A scanned or typed bin label with the CR/LF (and stray spaces) a keyboard-wedge scanner or a
 *  paste leaves on the ends trimmed off. */
export function cleanBarcode(raw: string): string {
  return raw.trim();
}

/** Why a cleaned label can't be used, or `null` if it can. */
export function barcodeProblem(label: string): string | null {
  if (label === "") return "Scan or type the bin label.";
  if (!BARCODE_RE.test(label)) return "That doesn't look like a bin label. Check it and try again.";
  return null;
}

/** Why a typed quantity can't be put away from a bucket holding `available`, or `null` if it can.
 *  Compared in exact milli-units — never as a float. */
export function quantityProblem(typed: string, available: string, uom: string): string | null {
  const quantity = typed.trim();
  if (quantity === "") return "Enter how much you're putting away.";
  if (!QTY_RE.test(quantity)) return "Enter a number with up to 3 decimals.";
  const milli = qtyToMilli(quantity);
  if (milli <= 0) return "Enter more than zero.";
  if (milli > qtyToMilli(available)) return `Only ${formatQty(available)} ${uom} is available there.`;
  return null;
}

/** Only stock that can be put away: something actually available, not already promised. */
export function hasAvailableStock(row: StockRowOut): boolean {
  return QTY_RE.test(row.available_qty) && qtyToMilli(row.available_qty) > 0;
}

export function stockLabel(row: StockRowOut): string {
  return `${row.sku} · ${row.item_name}`;
}

/** Where it sits and what it is, in the words on the shelf label. */
export function stockDetail(row: StockRowOut): string {
  const parts = [`In ${row.location_code}`, row.warehouse_code];
  if (row.lot_no) parts.push(`Lot ${row.lot_no}`);
  if (row.roll_no) parts.push(`Roll ${row.roll_no}`);
  parts.push(`${formatQty(row.available_qty)} ${row.uom} available`);
  return parts.join(" · ");
}

/** Stock matching what the worker typed, anywhere a person might look for it. */
export function filterStock(rows: readonly StockRowOut[], query: string): StockRowOut[] {
  const needle = query.trim().toLowerCase();
  if (needle === "") return [...rows];
  return rows.filter((row) =>
    [row.sku, row.item_name, row.location_code, row.warehouse_code, row.lot_no, row.roll_no].some(
      (field) => field !== null && field.toLowerCase().includes(needle),
    ),
  );
}

/**
 * Does the label the worker scanned name the bin that was suggested? The suggestion carries the
 * bin's code, not its barcode, and a worker has no `location.read` to resolve a barcode with — so
 * this can only recognise a label that is the code. Where a barcode differs from its code the
 * screen asks for a confirmation it didn't strictly need; the server's own answer
 * (`matched_suggestion`) is the one that is recorded.
 */
export function matchesSuggestion(label: string, suggestion: PutawaySuggestion): boolean {
  return label.toLowerCase() === suggestion.code.toLowerCase();
}

/** The UTC calendar date of an ISO timestamp. `toISOString` is always UTC, so this is the same
 *  date for the same instant wherever the phone is — and a replay days later still names the day
 *  the stock actually moved. */
export function utcDate(isoTimestamp: string): string {
  return isoTimestamp.slice(0, 10);
}

export interface BuildPutawayInput {
  row: StockRowOut;
  /** Already validated with `quantityProblem`. */
  quantity: string;
  /** Already cleaned and validated with `barcodeProblem`. */
  toBarcode: string;
  suggestion: PutawaySuggestion | null;
  /** The moment the worker confirmed, as `toISOString()`. */
  capturedAt: string;
  geo: GeoFix | null;
}

/** The request as it will be frozen into the queue. Keys with no value are left out, not `null`. */
export function buildPutawayBody(input: BuildPutawayInput): PutawayIn {
  const { row, geo } = input;
  const body: PutawayIn = {
    inventory_item_id: row.inventory_item_id,
    from_location_id: row.location_id,
    to_barcode: input.toBarcode,
    quantity: milliToQty(qtyToMilli(input.quantity.trim())),
    captured_at: input.capturedAt,
    occurred_on: utcDate(input.capturedAt),
  };
  if (row.lot_id !== null) body.lot_id = row.lot_id;
  if (row.roll_id !== null) body.roll_id = row.roll_id;
  if (input.suggestion) body.suggested_location_id = input.suggestion.location_id;
  if (geo) {
    body.lat_micro = geo.lat_micro;
    body.lng_micro = geo.lng_micro;
    if (geo.accuracy_m !== undefined) body.accuracy_m = geo.accuracy_m;
  }
  return body;
}

/** What the screen shows once a confirmed put-away has either landed or been saved for later. */
export type PutawayCompletion =
  | {
      kind: "synced";
      result: PutawayOut;
      summary: PutawaySummary;
      quantity: Qty;
      /** Why no location went with it, when none did. Non-blocking. */
      geoNotice: string | null;
    }
  | {
      kind: "queued";
      reason: QueuedReason;
      summary: PutawaySummary;
      quantity: Qty;
      geoNotice: string | null;
    };

/** A server warning in the worker's words — or `null` for one this app has no sentence for. None
 *  of them means the put-away failed: the move is recorded in every case. */
export function warningNotice(warning: string): string | null {
  if (warning.startsWith("geo_dropped:")) {
    return "The server couldn't use your location reading, so it wasn't recorded.";
  }
  if (warning === "captured_at_adjusted") {
    return "Your phone's clock looks off, so the server recorded its own time for this put-away.";
  }
  if (warning === "geo_not_recorded") {
    return "Your location couldn't be saved. The put-away itself is recorded.";
  }
  return null;
}

/** Why a confirmed put-away is waiting on the phone rather than recorded. */
export function queuedNotice(reason: QueuedReason): string {
  switch (reason) {
    case "offline":
      return "You're offline. It will sync automatically when you're back online.";
    case "network":
      return "Couldn't reach the server. It will be retried automatically.";
    case "server":
      return "The server had a problem. It will be retried automatically.";
    case "session":
      return "Your session has ended. It will sync when you sign back in.";
  }
}
