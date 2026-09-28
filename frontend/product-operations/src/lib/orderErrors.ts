/**
 * Reading the two shapes an order transition's 409 can carry.
 *
 * Every mutating order call sends the caller's last-known `version`; a mismatch is a 409 whose
 * `detail` names the order's current version. `accept` additionally can 409 with
 * `InsufficientStock` (ADR-0014 §11): a distinct problem `type` (`insufficient-stock`, not the
 * platform's generic `conflict`) carrying one `errors[]` entry per short line — `{sku, requested,
 * on_hand, reserved, available, …}`, **not** the platform's ordinary `{field, code, message}`
 * field-error shape `fieldErrors()` reads, so this module reads the raw array itself.
 *
 * Both are 409s a screen should never toast raw and invite a blind retry on: a stale version or a
 * status that moved under the caller means "reload and look again"; a stock shortfall means "not
 * yet — here's exactly what's short."
 */

import { ApiError, problemSlug } from "@opsgrids/client";

/** One short line from `InsufficientStock.errors` (ADR-0014 §11). Extra keys (`location`,
 *  `status`, `reservations`) may be present; only the ones every screen needs are typed here. */
export interface StockShortfall {
  sku: string;
  uom?: string;
  requested: string;
  on_hand: string;
  reserved: string;
  available: string;
}

function isStockShortfall(value: unknown): value is StockShortfall {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return (
    typeof entry.sku === "string" &&
    typeof entry.requested === "string" &&
    typeof entry.on_hand === "string" &&
    typeof entry.reserved === "string" &&
    typeof entry.available === "string"
  );
}

/** The shortfalls an `accept` 409 named, or `[]` when this error is not an `InsufficientStock`
 *  one — a version conflict or an ordinary status refusal carry no `errors[]` in this shape. */
export function stockShortfalls(error: unknown): StockShortfall[] {
  if (!(error instanceof ApiError)) return [];
  if (problemSlug(error.problem) !== "insufficient-stock") return [];
  const raw: unknown = error.problem?.errors;
  if (!Array.isArray(raw)) return [];
  return raw.filter(isStockShortfall);
}

/**
 * A 409 that is not a stock shortfall — a stale `version`, or an order whose status moved to one
 * this action no longer applies to. Both mean the same thing to the operator (the order changed
 * since it was loaded), and the fix is the same either way: refetch and try again from what is
 * actually there now, never a retry of the exact same request.
 */
export function isStaleOrderConflict(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && problemSlug(error.problem) !== "insufficient-stock";
}
