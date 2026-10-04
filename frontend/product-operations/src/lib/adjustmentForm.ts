import type { AdjustmentIn, AdjustmentReason, ControlSettingsIn } from "@/api/movementTypes";
import { parseQty } from "./movementQty";
import { isUuid } from "./uuid";

export const ADJUSTMENT_REASONS: { value: AdjustmentReason; label: string }[] = [
  { value: "damaged", label: "Damaged" },
  { value: "lost", label: "Lost" },
  { value: "found", label: "Found" },
  { value: "consumed", label: "Consumed" },
  { value: "returned", label: "Returned" },
  { value: "other", label: "Other" },
];

export interface AdjustmentFormValues {
  inventoryItemId: string;
  uom: string;
  locationId: string;
  delta: string;
  reason: AdjustmentReason | "";
  note: string;
  evidenceDocumentId: string;
  occurredOn: string;
  lotId: string;
  rollId: string;
}

export interface AdjustmentFormErrors {
  item?: string;
  location?: string;
  delta?: string;
  reason?: string;
  evidence?: string;
  occurredOn?: string;
  lot?: string;
  roll?: string;
}

export type AdjustmentFormResult =
  | { ok: true; payload: AdjustmentIn }
  | { ok: false; errors: AdjustmentFormErrors };

/**
 * Validate an adjustment request the way `AdjustmentIn` does. The delta is signed and can't be
 * zero. Whether this size needs approval, and so evidence, is the organization's threshold — the
 * server's call — so evidence is optional here and the server's 422 on `evidence_document_id`
 * lands on that field when it is needed.
 */
export function validateAdjustmentForm(
  values: AdjustmentFormValues,
  discreteUoms: ReadonlySet<string>,
  today: string,
): AdjustmentFormResult {
  const errors: AdjustmentFormErrors = {};
  if (values.inventoryItemId === "") errors.item = "Choose an item.";
  if (values.locationId === "") errors.location = "Choose the bin being adjusted.";

  const delta = parseQty(values.delta, { signed: true, whole: discreteUoms.has(values.uom), uom: values.uom });
  if (!delta.ok) errors.delta = delta.message;
  if (values.reason === "") errors.reason = "Choose a reason.";

  const evidence = values.evidenceDocumentId.trim();
  if (evidence !== "" && !isUuid(evidence)) errors.evidence = "A document id looks like 3f2b8c1e-….";
  if (values.occurredOn === "" || values.occurredOn > today) errors.occurredOn = "The date can't be in the future.";
  if (values.lotId.trim() !== "" && !isUuid(values.lotId)) errors.lot = "A lot id looks like 3f2b8c1e-….";
  if (values.rollId.trim() !== "" && !isUuid(values.rollId)) errors.roll = "A roll id looks like 3f2b8c1e-….";

  if (Object.keys(errors).length > 0 || !delta.ok || values.reason === "") return { ok: false, errors };

  return {
    ok: true,
    payload: {
      inventory_item_id: values.inventoryItemId,
      location_id: values.locationId,
      quantity_delta: delta.value,
      reason: values.reason,
      note: values.note.trim(),
      occurred_on: values.occurredOn,
      ...(evidence !== "" ? { evidence_document_id: evidence } : {}),
      ...(values.lotId.trim() !== "" ? { lot_id: values.lotId.trim() } : {}),
      ...(values.rollId.trim() !== "" ? { roll_id: values.rollId.trim() } : {}),
    },
  };
}

export interface ThresholdValues {
  transfer: string;
  adjustment: string;
}

export interface ThresholdErrors {
  transfer?: string;
  adjustment?: string;
}

export type ThresholdResult =
  | { ok: true; payload: ControlSettingsIn }
  | { ok: false; errors: ThresholdErrors };

function threshold(text: string): { ok: true; value: string | null } | { ok: false; message: string } {
  if (text.trim() === "") return { ok: true, value: null };
  const parsed = parseQty(text);
  if (!parsed.ok) return { ok: false, message: parsed.message };
  return { ok: true, value: parsed.value };
}

/** Both thresholds, each blank (no threshold) or a positive decimal; they replace each other's
 *  server-side as a pair, so both are always sent. */
export function validateThresholds(values: ThresholdValues): ThresholdResult {
  const transfer = threshold(values.transfer);
  const adjustment = threshold(values.adjustment);
  const errors: ThresholdErrors = {};
  if (!transfer.ok) errors.transfer = transfer.message;
  if (!adjustment.ok) errors.adjustment = adjustment.message;
  if (!transfer.ok || !adjustment.ok) return { ok: false, errors };
  return {
    ok: true,
    payload: { transfer_approval_threshold: transfer.value, adjustment_approval_threshold: adjustment.value },
  };
}
