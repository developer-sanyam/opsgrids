import { useRef, useState } from "react";

import { transferStock } from "@/api/tracking";
import type { OnSiteStatus, StockMoveOut, StockRowOut } from "@/api/trackingTypes";
import { checkQuantity, movableMilli } from "@/lib/qtyInput";
import { formatQty, milliToQty, qtyToMilli } from "@/lib/quantity";
import { occurredOnError } from "@/lib/dateCheck";
import { today } from "@/lib/now";
import { STOCK_STATUS_LABEL } from "@/lib/trackingLabels";
import { DateAndNoteFields } from "@/tracking/DateAndNoteFields";
import { ShortfallAlert } from "@/tracking/ShortfallAlert";
import { useLocationOptions } from "@/tracking/useLocationOptions";
import { useStockOperation } from "@/tracking/useStockOperation";
import { FormDialog, Select, TextField } from "@/ui";

interface StockTransferDialogProps {
  /** The bucket the stock leaves. The destination is chosen among its own warehouse's locations. */
  row: StockRowOut & { status: OnSiteStatus };
  onClose: () => void;
  onSaved: (move: StockMoveOut) => void;
}

/**
 * Bin-to-bin transfer — `POST /operations/inventory/stock/transfers`
 * (`operations.inventory.stock.transfer`). Same warehouse, immediate. Launched from the bucket the
 * stock leaves, so the item, source, status, lot and roll are fixed and the person only chooses
 * where it goes and how much. Reserved stock cannot move: the quantity is capped at what is not
 * promised, and the server's refusal (with the reservations in the way) is shown if it still says
 * no.
 */
export function StockTransferDialog({ row, onClose, onSaved }: StockTransferDialogProps) {
  const [toLocationId, setToLocationId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());
  const [note, setNote] = useState("");
  const [toError, setToError] = useState<string | undefined>(undefined);
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const destinations = useLocationOptions(row.warehouse_id, row.location_id);
  const movable = movableMilli(row);
  const op = useStockOperation(transferStock, onSaved, ["to_location_id", "quantity", "occurred_on", "note"]);

  const dirty = toLocationId !== "" || quantity !== "" || note !== "" || occurredOn !== today();

  function submit() {
    setToError(undefined);
    setQuantityError(undefined);
    setDateError(undefined);
    let valid = true;
    if (toLocationId === "") {
      setToError("Choose where the stock goes.");
      firstFieldRef.current?.focus();
      valid = false;
    }
    const checked = checkQuantity(quantity, {
      uom: row.uom,
      maxMilli: movable,
      maxLabel: "that can be moved",
    });
    if (!checked.ok) {
      setQuantityError(checked.message);
      valid = false;
    }
    const dateProblem = occurredOnError(occurredOn);
    if (dateProblem) {
      setDateError(dateProblem);
      valid = false;
    }
    if (!valid || !checked.ok) return;
    op.submit({
      inventory_item_id: row.inventory_item_id,
      from_location_id: row.location_id,
      to_location_id: toLocationId,
      quantity: checked.value,
      status: row.status,
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
      title={`Transfer stock — ${row.sku}`}
      description={bucket}
      submitLabel="Transfer stock"
      pendingLabel="Transferring…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...op.dialogError}
    >
      <Select
        ref={firstFieldRef}
        label="To location"
        value={toLocationId}
        onChange={(e) => setToLocationId(e.target.value)}
        placeholder={destinations.status === "loading" ? "Loading locations…" : "Choose a location"}
        options={destinations.options}
        disabled={destinations.status !== "success" || destinations.options.length === 0}
        required
        helper={
          destinations.status === "success" && destinations.options.length === 0
            ? "This warehouse has no other location. Add one on the Warehouses screen."
            : "Only locations in the same warehouse. Moving between warehouses is a later feature."
        }
        error={toError ?? op.fieldErrors["to_location_id"]}
      />
      <TextField
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        required
        helper={`Up to ${formatQty(milliToQty(movable))} ${row.uom} can move${
          qtyToMilli(row.reserved_qty) > 0 ? ` (${formatQty(row.reserved_qty)} ${row.uom} is reserved)` : ""
        }.`}
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
