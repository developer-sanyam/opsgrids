/**
 * Reading the 409 a stock-moving write answers with when the stock is not there.
 *
 * `insufficient-stock` (a reservation, an adjustment, a transfer dispatch) is a distinct problem
 * `type`, and its `errors[]` entries are not the platform's `{field, code, message}` shape: each
 * carries the numbers — `requested`, `on_hand`, `reserved`, `available` — and, when stock exists
 * but is promised, `reservations`: the holds standing in the way. A screen says those numbers in
 * plain words rather than echoing "Not enough stock" and inviting a blind retry.
 */

import { ApiError, problemSlug } from "@opsgrids/client";

export interface BlockingReservation {
  reservation_id: string;
  reference_type: string | null;
  reference_id: string | null;
  quantity: string;
}

export interface StockRefusal {
  sku: string;
  location: string;
  uom: string;
  requested: string;
  on_hand: string;
  reserved: string;
  available: string;
  reservations: BlockingReservation[];
}

function text(entry: Record<string, unknown>, key: string): string | undefined {
  const value = entry[key];
  return typeof value === "string" ? value : undefined;
}

function blocker(value: unknown): BlockingReservation | null {
  if (typeof value !== "object" || value === null) return null;
  const entry = value as Record<string, unknown>;
  const reservationId = text(entry, "reservation_id");
  const quantity = text(entry, "quantity");
  if (reservationId === undefined || quantity === undefined) return null;
  return {
    reservation_id: reservationId,
    reference_type: text(entry, "reference_type") ?? null,
    reference_id: text(entry, "reference_id") ?? null,
    quantity,
  };
}

function refusalOf(value: unknown): StockRefusal | null {
  if (typeof value !== "object" || value === null) return null;
  const entry = value as Record<string, unknown>;
  const sku = text(entry, "sku");
  const requested = text(entry, "requested");
  const onHand = text(entry, "on_hand");
  const reserved = text(entry, "reserved");
  const available = text(entry, "available");
  if (
    sku === undefined ||
    requested === undefined ||
    onHand === undefined ||
    reserved === undefined ||
    available === undefined
  ) {
    return null;
  }
  const raw = entry.reservations;
  const reservations = Array.isArray(raw)
    ? raw.map(blocker).filter((b): b is BlockingReservation => b !== null)
    : [];
  return {
    sku,
    location: text(entry, "location") ?? "",
    uom: text(entry, "uom") ?? "",
    requested,
    on_hand: onHand,
    reserved,
    available,
    reservations,
  };
}

/** The shortfalls an `insufficient-stock` 409 names, or `[]` for any other error. */
export function stockRefusals(error: unknown): StockRefusal[] {
  if (!(error instanceof ApiError)) return [];
  if (problemSlug(error.problem) !== "insufficient-stock") return [];
  const raw: unknown = error.problem?.errors;
  if (!Array.isArray(raw)) return [];
  return raw.map(refusalOf).filter((r): r is StockRefusal => r !== null);
}

function withUnit(qty: string, uom: string): string {
  return uom === "" ? qty : `${qty} ${uom}`;
}

/** One shortfall in plain words: what was asked for, what is there, what is promised elsewhere. */
export function describeRefusal(refusal: StockRefusal): string {
  const where = refusal.location === "" ? "" : ` at ${refusal.location}`;
  return (
    `${refusal.sku}${where}: you asked for ${withUnit(refusal.requested, refusal.uom)}, but only ` +
    `${withUnit(refusal.available, refusal.uom)} is available ` +
    `(${withUnit(refusal.on_hand, refusal.uom)} on hand, ${withUnit(refusal.reserved, refusal.uom)} already reserved).`
  );
}

/** A hold standing in the way, as a short phrase: "20 reserved for an order". */
export function describeBlocker(blocking: BlockingReservation, uom: string): string {
  const reference =
    blocking.reference_type === "order"
      ? "an order"
      : blocking.reference_type === "production_order"
        ? "a production order"
        : "a manual hold";
  return `${withUnit(blocking.quantity, uom)} reserved for ${reference}`;
}
