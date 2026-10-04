import { useRef, useState } from "react";

import { ApiError, useCan } from "@opsgrids/client";

import { changeStockStatus } from "@/api/tracking";
import type { OnSiteStatus, StockMoveOut, StockRowOut } from "@/api/trackingTypes";
import { checkQuantity, movableMilli } from "@/lib/qtyInput";
import { formatQty, milliToQty } from "@/lib/quantity";
import { occurredOnError } from "@/lib/dateCheck";
import { today } from "@/lib/now";
import { STOCK_STATUS_LABEL, STOCK_STATUS_OPTIONS } from "@/lib/trackingLabels";
import { DateAndNoteFields } from "@/tracking/DateAndNoteFields";
import { ShortfallAlert } from "@/tracking/ShortfallAlert";
import { useStockOperation } from "@/tracking/useStockOperation";
import { FormDialog, Select, TextField } from "@/ui";

/** Statuses stock is held out of use in. Leaving one for a usable status is a release. */
const HOLD_STATUSES: readonly OnSiteStatus[] = ["REJECTED", "DAMAGED", "QUARANTINE", "QUALITY_HOLD", "BLOCKED"];
const RELEASE_TARGETS: readonly OnSiteStatus[] = ["AVAILABLE", "WIP"];
const ADJUST = "operations.inventory.stock.adjust";

interface StockStatusChangeDialogProps {
  /** The bucket whose stock changes status, where it sits. */
  row: StockRowOut & { status: OnSiteStatus };
  onClose: () => void;
  onSaved: (move: StockMoveOut) => void;
}

/**
 * Status change — `POST /operations/inventory/stock/status-changes`
 * (`operations.inventory.stock.transfer`): move stock between statuses where it sits, for example
 * Available to Quality hold. One paired move; the physical total does not change. Reserved stock
 * cannot be re-statused, so the quantity is capped at what is not promised.
 *
 * Releasing stock out of a hold (Rejected, Damaged, Quarantine, Quality hold, Blocked) into
 * Available or Work in progress also needs `operations.inventory.stock.adjust`; moving into a hold
 * or between holds needs only transfer. The release targets are offered only to a caller holding
 * adjust, with a hint saying why otherwise. That is presentation: the server answers 403 either way.
 */
export function StockStatusChangeDialog({ row, onClose, onSaved }: StockStatusChangeDialogProps) {
  const [toStatus, setToStatus] = useState<OnSiteStatus | "">("");
  const [quantity, setQuantity] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());
  const [note, setNote] = useState("");
  const [statusError, setStatusError] = useState<string | undefined>(undefined);
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const canAdjust = useCan(ADJUST, "ORG");
  const isRelease = HOLD_STATUSES.includes(row.status);
  const releaseLocked = isRelease && !canAdjust;
  const movable = movableMilli(row);
  const op = useStockOperation(changeStockStatus, onSaved, ["to_status", "quantity", "occurred_on", "note"]);
  const forbidden = op.error instanceof ApiError && op.error.status === 403;
  const dialogError = forbidden
    ? {
        ...op.dialogError,
        error: `You don't have permission to release stock from ${STOCK_STATUS_LABEL[row.status]}. Releasing it to Available or Work in progress needs ${ADJUST}.`,
      }
    : op.dialogError;
  const dirty = toStatus !== "" || quantity !== "" || note !== "" || occurredOn !== today();

  function submit() {
    setStatusError(undefined);
    setQuantityError(undefined);
    setDateError(undefined);
    let valid = true;
    if (toStatus === "") {
      setStatusError("Choose the new status.");
      firstFieldRef.current?.focus();
      valid = false;
    }
    const checked = checkQuantity(quantity, { uom: row.uom, maxMilli: movable, maxLabel: "that can change status" });
    if (!checked.ok) {
      setQuantityError(checked.message);
      valid = false;
    }
    const dateProblem = occurredOnError(occurredOn);
    if (dateProblem) {
      setDateError(dateProblem);
      valid = false;
    }
    if (!valid || !checked.ok || toStatus === "") return;
    op.submit({
      inventory_item_id: row.inventory_item_id,
      location_id: row.location_id,
      from_status: row.status,
      to_status: toStatus,
      quantity: checked.value,
      ...(row.lot_id ? { lot_id: row.lot_id } : {}),
      ...(row.roll_id ? { roll_id: row.roll_id } : {}),
      occurred_on: occurredOn,
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  const bucket = [
    row.location_code,
    STOCK_STATUS_LABEL[row.status],
    row.lot_no ? `lot ${row.lot_no}` : null,
    row.roll_no ? `roll ${row.roll_no}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Change status — ${row.sku}`}
      description={bucket}
      submitLabel="Change status"
      pendingLabel="Changing…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...dialogError}
    >
      <Select
        ref={firstFieldRef}
        label="New status"
        value={toStatus}
        onChange={(e) => setToStatus(e.target.value as OnSiteStatus)}
        placeholder="Choose a status"
        options={STOCK_STATUS_OPTIONS.filter(
          (o) => o.value !== row.status && !(releaseLocked && RELEASE_TARGETS.includes(o.value)),
        )}
        helper={
          releaseLocked
            ? `Releasing to Available or Work in progress needs the ${ADJUST} permission. You can still move this stock to another hold.`
            : undefined
        }
        required
        error={statusError ?? op.fieldErrors["to_status"]}
      />
      <TextField
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        required
        helper={`Up to ${formatQty(milliToQty(movable))} ${row.uom} can change status.`}
        error={quantityError ?? op.fieldErrors["quantity"]}
      />
      <DateAndNoteFields
        occurredOn={occurredOn}
        onOccurredOn={setOccurredOn}
        note={note}
        onNote={setNote}
        dateError={dateError ?? op.fieldErrors["occurred_on"]}
        noteError={op.fieldErrors["note"]}
      />
      <ShortfallAlert shortfall={op.shortfall} />
    </FormDialog>
  );
}
