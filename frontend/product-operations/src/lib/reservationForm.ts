import type { Qty } from "@/api/types";
import type { ReservationIn, ReservationReference } from "@/api/movementTypes";
import { compareQty, parseQty } from "./movementQty";
import { isUuid } from "./uuid";

export interface ReservationFormValues {
  inventoryItemId: string;
  uom: string;
  locationId: string;
  quantity: string;
  referenceType: ReservationReference;
  referenceId: string;
  /** An instant in UTC, or `null` for a hold that stays. */
  expiresAt: string | null;
  note: string;
  lotId: string;
  rollId: string;
}

export interface ReservationFormErrors {
  item?: string;
  location?: string;
  quantity?: string;
  referenceId?: string;
  expiresAt?: string;
  lot?: string;
  roll?: string;
}

export type ReservationFormResult =
  | { ok: true; payload: ReservationIn }
  | { ok: false; errors: ReservationFormErrors };

/**
 * Validate "Reserve stock" the way `ReservationIn` does: a positive quantity, a document id when
 * the reservation is for an order or production order (a manual hold needs none), and an expiry
 * that is still in the future. `now` is passed in so the rule is testable.
 */
export function validateReservationForm(
  values: ReservationFormValues,
  discreteUoms: ReadonlySet<string>,
  now: Date,
): ReservationFormResult {
  const errors: ReservationFormErrors = {};
  if (values.inventoryItemId === "") errors.item = "Choose an item.";
  if (values.locationId === "") errors.location = "Choose the bin the stock is held in.";

  const quantity = parseQty(values.quantity, { whole: discreteUoms.has(values.uom), uom: values.uom });
  if (!quantity.ok) errors.quantity = quantity.message;

  const reference = values.referenceId.trim();
  if (values.referenceType !== "manual") {
    if (reference === "") errors.referenceId = "Enter the id of the order this is held for.";
    else if (!isUuid(reference)) errors.referenceId = "An order id looks like 3f2b8c1e-….";
  } else if (reference !== "" && !isUuid(reference)) {
    errors.referenceId = "A reference id looks like 3f2b8c1e-….";
  }

  if (values.expiresAt !== null && Date.parse(values.expiresAt) <= now.getTime()) {
    errors.expiresAt = "The expiry has to be in the future.";
  }
  if (values.lotId.trim() !== "" && !isUuid(values.lotId)) errors.lot = "A lot id looks like 3f2b8c1e-….";
  if (values.rollId.trim() !== "" && !isUuid(values.rollId)) errors.roll = "A roll id looks like 3f2b8c1e-….";

  if (Object.keys(errors).length > 0 || !quantity.ok) return { ok: false, errors };

  return {
    ok: true,
    payload: {
      inventory_item_id: values.inventoryItemId,
      location_id: values.locationId,
      quantity: quantity.value,
      reference_type: values.referenceType,
      ...(values.referenceType !== "manual" || reference !== "" ? { reference_id: reference } : {}),
      ...(values.expiresAt !== null ? { expires_at: values.expiresAt } : {}),
      note: values.note.trim(),
      ...(values.lotId.trim() !== "" ? { lot_id: values.lotId.trim() } : {}),
      ...(values.rollId.trim() !== "" ? { roll_id: values.rollId.trim() } : {}),
    },
  };
}

export type ReleaseResult = { ok: true; quantity: Qty | null } | { ok: false; message: string };

/**
 * A release is either everything still open (`quantity: null`, an empty body) or a part of it. A
 * part must be a positive decimal, whole where the unit is, and no more than what remains.
 */
export function validateRelease(
  mode: "all" | "part",
  input: string,
  remaining: Qty,
  uom: string,
  discreteUoms: ReadonlySet<string>,
): ReleaseResult {
  if (mode === "all") return { ok: true, quantity: null };
  const parsed = parseQty(input, { whole: discreteUoms.has(uom), uom });
  if (!parsed.ok) return { ok: false, message: parsed.message };
  if (compareQty(parsed.value, remaining) > 0) {
    return { ok: false, message: `Only ${remaining} ${uom} is still reserved.` };
  }
  return { ok: true, quantity: parsed.value };
}
