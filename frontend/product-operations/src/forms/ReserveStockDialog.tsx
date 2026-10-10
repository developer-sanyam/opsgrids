import { useRef, useState } from "react";

import { createReservation } from "@/api/movements";
import type { ReservationDetailOut, ReservationIn, ReservationReference } from "@/api/movementTypes";
import type { InventoryItemOut } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { validateReservationForm } from "@/lib/reservationForm";
import type { ReservationFormErrors } from "@/lib/reservationForm";
import { stockRefusals } from "@/lib/stockRefusal";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { errorProp } from "@/movements/errorProp";
import { MovementItemPicker } from "@/movements/MovementItemPicker";
import { StockRefusalAlert } from "@/movements/StockRefusalAlert";
import { TrackingFields } from "@/movements/TrackingFields";
import { useDiscreteUoms } from "@/movements/useMovementReference";
import { WarehouseBinFields } from "@/movements/WarehouseBinFields";
import { FormDialog, Select, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { DateTimeField } from "@opsgrids/ui";

const REFERENCE_OPTIONS: { value: ReservationReference; label: string }[] = [
  { value: "manual", label: "Manual hold" },
  { value: "order", label: "Sales order" },
  { value: "production_order", label: "Production order" },
];

const REFERENCE_LABEL: Record<ReservationReference, string> = {
  manual: "Reference id",
  order: "Order id",
  production_order: "Production order id",
};

interface ReserveStockDialogProps {
  onClose: () => void;
  onReserved: (reservation: ReservationDetailOut) => void;
}

/**
 * `POST /operations/inventory/reservations` — `operations.inventory.stock.reserve`. Promises
 * available stock in one bin to a demand without moving it. A refusal for lack of stock shows what
 * was asked for against what is available, and the holds in the way. One `Idempotency-Key` per
 * distinct request: a double submit or a retry after a lost answer is a replay, not a second hold.
 */
export function ReserveStockDialog({ onClose, onReserved }: ReserveStockDialogProps) {
  const [itemOption, setItemOption] = useState<ComboboxOption | null>(null);
  const [item, setItem] = useState<InventoryItemOut | null>(null);
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [referenceType, setReferenceType] = useState<ReservationReference>("manual");
  const [referenceId, setReferenceId] = useState("");
  const [expiresAt, setExpiresAt] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const [lotId, setLotId] = useState("");
  const [rollId, setRollId] = useState("");
  const [errors, setErrors] = useState<ReservationFormErrors>({});
  const quantityRef = useRef<HTMLInputElement>(null);

  const warehouses = useWarehouseIdOptions();
  const discrete = useDiscreteUoms();
  const keyFor = useSubmissionKeys();

  const save = useMutation(
    (input: ReservationIn) => createReservation(input, keyFor(input)),
    { onSuccess: (saved) => onReserved(saved) },
  );
  const refusals = stockRefusals(save.error);
  const field = save.fieldErrors;

  function onPick(picked: InventoryItemOut | null) {
    setItem(picked);
    setLotId("");
    setRollId("");
    if (picked === null) return;
    const home = warehouses.options.find((w) => w.label === picked.warehouse);
    if (home) {
      setWarehouseId(home.value);
      setLocationId("");
    }
  }

  const dirty = itemOption !== null || quantity !== "" || referenceId !== "" || note !== "" || expiresAt !== null;

  function submit() {
    const result = validateReservationForm(
      {
        inventoryItemId: itemOption?.id ?? "",
        uom: item?.uom ?? "",
        locationId,
        quantity,
        referenceType,
        referenceId,
        expiresAt,
        note,
        lotId,
        rollId,
      },
      discrete,
      new Date(),
    );
    if (!result.ok) {
      setErrors(result.errors);
      return;
    }
    setErrors({});
    void save.mutate(result.payload);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Reserve stock"
      description="Hold available stock for an order, a production order, or by hand. Nothing moves; it just can't be promised twice."
      submitLabel="Reserve stock"
      pendingLabel="Reserving…"
      pending={save.pending}
      initialFocusRef={quantityRef}
      dirty={dirty}
      {...(refusals.length > 0 ? {} : formDialogError(save.error, save.fieldErrors))}
    >
      <MovementItemPicker
        value={itemOption}
        onChange={setItemOption}
        onPick={onPick}
        {...errorProp(errors.item, field.inventory_item_id)}
      />
      <WarehouseBinFields
        warehouseOptions={warehouses.options}
        warehousesLoading={warehouses.status === "loading"}
        warehouseId={warehouseId}
        onWarehouseChange={setWarehouseId}
        locationId={locationId}
        onLocationChange={setLocationId}
        locationLabel="Bin to hold from"
        {...(errors.location !== undefined || field.location_id !== undefined
          ? { locationError: errors.location ?? field.location_id }
          : {})}
      />
      <TextField
        ref={quantityRef}
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        helper={item ? `In ${item.uom}, up to 3 decimals. Available: ${item.available_qty} ${item.uom}.` : "Up to 3 decimals."}
        required
        {...errorProp(errors.quantity, field.quantity)}
      />
      <TrackingFields
        tracksLot={item?.track_lot ?? false}
        tracksRoll={item?.track_roll ?? false}
        lotId={lotId}
        rollId={rollId}
        onLotChange={setLotId}
        onRollChange={setRollId}
        lotError={errors.lot ?? field.lot_id}
        rollError={errors.roll ?? field.roll_id}
      />
      <Select
        label="Held for"
        value={referenceType}
        onChange={(e) => setReferenceType(e.target.value as ReservationReference)}
        options={REFERENCE_OPTIONS}
      />
      <TextField
        label={REFERENCE_LABEL[referenceType]}
        value={referenceId}
        onChange={(e) => setReferenceId(e.target.value)}
        required={referenceType !== "manual"}
        optional={referenceType === "manual"}
        helper={
          referenceType === "manual"
            ? "Optional. Leave blank for a hold with no document behind it."
            : "The document's id, as shown on its page."
        }
        {...errorProp(errors.referenceId, field.reference_id)}
      />
      <DateTimeField
        label="Expires (optional)"
        value={expiresAt}
        onChange={setExpiresAt}
        zone={Intl.DateTimeFormat().resolvedOptions().timeZone}
        min={new Date().toISOString()}
        helper="Leave empty for a hold that stays until released. Times are in your local zone."
        {...errorProp(errors.expiresAt, field.expires_at)}
      />
      <TextArea
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        {...errorProp(field.note)}
      />
      <StockRefusalAlert refusals={refusals} action="reserve this" />
    </FormDialog>
  );
}
