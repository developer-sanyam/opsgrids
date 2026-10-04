/**
 * Reading the refusals a stock operation can carry.
 *
 * Two shapes, both RFC 7807 with an `errors[]`:
 *
 * - **Validation / conflict** (422, and 409 for a duplicate): entries are `{field, code, message}`.
 *   The screen puts each on the control named by `field`, in words chosen for the `field/code`
 *   pair where the server's own sentence is too terse to act on, and the server's sentence
 *   verbatim where it already carries the numbers (`children/split_total`, `roll_ids/not_mergeable`).
 * - **Insufficient stock** (409, problem type `insufficient-stock`): one entry with
 *   `{sku, location, status, requested, on_hand, reserved, available, reservations[]}` — not a
 *   field error. `reservations` names the promises standing in the way (ADR-0014 §11), so the
 *   operator can see what to release rather than guess.
 */

import { ApiError, fieldErrors, problemSlug } from "@opsgrids/client";

/** One active reservation holding stock back (`ledger._blocking_reservations`). */
export interface BlockingReservation {
  reservation_id: string;
  reference_type: string | null;
  reference_id: string | null;
  quantity: string;
}

export interface StockShortfall {
  sku: string;
  location: string;
  status: string;
  uom: string;
  requested: string;
  on_hand: string;
  reserved: string;
  available: string;
  reservations: BlockingReservation[];
}

function isReservation(value: unknown): value is BlockingReservation {
  if (typeof value !== "object" || value === null) return false;
  const entry = value as Record<string, unknown>;
  return typeof entry["reservation_id"] === "string" && typeof entry["quantity"] === "string";
}

/** The shortfall a 409 `insufficient-stock` named (its first entry), or `null` for any other error. */
export function stockShortfall(error: unknown): StockShortfall | null {
  if (!(error instanceof ApiError) || problemSlug(error.problem) !== "insufficient-stock") return null;
  const raw: unknown = error.problem?.errors?.[0];
  if (typeof raw !== "object" || raw === null) return null;
  const entry = raw as Record<string, unknown>;
  const text = (key: string): string => (typeof entry[key] === "string" ? (entry[key] as string) : "");
  const reservations = Array.isArray(entry["reservations"]) ? entry["reservations"].filter(isReservation) : [];
  return {
    sku: text("sku"),
    location: text("location"),
    status: text("status"),
    uom: text("uom"),
    requested: text("requested"),
    on_hand: text("on_hand"),
    reserved: text("reserved"),
    available: text("available"),
    reservations,
  };
}

/** Words for the `field/code` pairs the Phase 1 routes raise, where the server's `message` is a
 *  fragment ("lot required"). Anything not listed shows the server's own sentence. */
const COPY: Record<string, string> = {
  "lot_id/required": "Choose the lot this stock belongs to.",
  "roll_id/required": "Choose the roll this stock belongs to.",
  "lot_id/not_tracked": "This item isn't tracked by lot. Turn on lot tracking for the item first.",
  "roll_id/not_tracked": "This item isn't tracked by roll. Turn on roll tracking for the item first.",
  "inventory_item_id/not_tracked": "This item isn't tracked this way. Turn on tracking for the item first.",
  "to_location_id/different_warehouse":
    "Bin-to-bin transfers stay inside one warehouse. Choose a location in the same warehouse.",
};

const CONSOLIDATE_GUIDANCE =
  "Consolidate the roll into one bucket by transfer or status change first, then try again.";
const TOO_LONG_CHILD_ROLL_NO = /^children(?:\[\d+\]|\.\d+)\.roll_no$/;

/** The sentence for one entry where it needs the whole problem, not just the entry. */
function entrySentence(error: ApiError, entry: { field: string; code: string; message: string }): string | undefined {
  if (entry.code === "roll_in_several_buckets") {
    // The entry's own message is terse; the problem's detail says where the rest of the stock sits.
    const where = (error.problem?.detail ?? entry.message).trim().replace(/[.\s]+$/, "");
    return `${where}. ${CONSOLIDATE_GUIDANCE}`;
  }
  if (entry.code === "too_long" && TOO_LONG_CHILD_ROLL_NO.test(entry.field)) {
    return "The automatic roll number for this child is too long. Enter explicit roll numbers for the children.";
  }
  return undefined;
}

/**
 * `{field: sentence}` for every field-level entry on the error, first entry per field winning.
 *
 * A line-scoped field (`lines.0.quantity`, how the opening-stock body names it) is also reported
 * under its bare name (`quantity`), because the dialogs post one line and have one `quantity`
 * control. Empty for an error with no field detail — including `insufficient-stock`, whose entry
 * is not a field error.
 */
export function operationFieldErrors(error: unknown): Record<string, string> {
  if (!(error instanceof ApiError)) return {};
  const map: Record<string, string> = {};
  for (const entry of fieldErrors(error.problem)) {
    const sentence = entrySentence(error, entry) ?? COPY[`${entry.field}/${entry.code}`] ?? entry.message;
    if (!(entry.field in map)) map[entry.field] = sentence;
    const bare = entry.field.replace(/^lines\.\d+\./, "");
    if (bare !== entry.field && !(bare in map)) map[bare] = sentence;
  }
  return map;
}

/** A human line for a reservation's origin: "purchase order 3f2a…" or "a reservation". */
export function describeReservation(reservation: BlockingReservation): string {
  const kind = reservation.reference_type?.replace(/_/g, " ");
  if (!kind) return "A reservation";
  const reference = reservation.reference_id ? ` ${reservation.reference_id.slice(0, 8)}` : "";
  return `${kind.charAt(0).toUpperCase()}${kind.slice(1)}${reference}`;
}
