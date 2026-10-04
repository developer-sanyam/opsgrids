import { useEffect, useRef, useState } from "react";

import { postOpeningStock } from "@/api/tracking";
import type { OnSiteStatus, OpeningLineIn, StockMoveOut } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";
import { useWarehouseIdOptions } from "@/data/useWarehouseOptions";
import { checkQuantity } from "@/lib/qtyInput";
import { occurredOnError } from "@/lib/dateCheck";
import { today } from "@/lib/now";
import { STOCK_STATUS_OPTIONS } from "@/lib/trackingLabels";
import { DateAndNoteFields } from "@/tracking/DateAndNoteFields";
import { ItemPicker } from "@/tracking/ItemPicker";
import { useLocationOptions } from "@/tracking/useLocationOptions";
import { useStockOperation } from "@/tracking/useStockOperation";
import { useLotOptions, useRollOptions } from "@/tracking/useTrackedOptions";
import { FormDialog, Select, TextField } from "@/ui";

interface OpeningStockDialogProps {
  /** The item the stock is for. `null` asks for one. */
  item: InventoryItemOut | null;
  onClose: () => void;
  onSaved: (move: StockMoveOut) => void;
}

/**
 * Opening stock — `POST /operations/inventory/stock/opening` (`operations.inventory.stock.adjust`):
 * bring stock that already exists onto the books at a location, in a status, and — for a tracked
 * item — in a named lot and on a named roll. Opening creates quantity out of nothing, which is why
 * it needs the adjust permission rather than the transfer one. A tracked item must name its lot or
 * roll and an untracked one must not; both are checked here and again by the server, whose
 * `lot_id` / `roll_id` refusals land on those controls.
 */
export function OpeningStockDialog({ item: initialItem, onClose, onSaved }: OpeningStockDialogProps) {
  const [item, setItem] = useState<InventoryItemOut | null>(initialItem);
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [status, setStatus] = useState<OnSiteStatus>("AVAILABLE");
  const [lotId, setLotId] = useState("");
  const [rollId, setRollId] = useState("");
  const [quantity, setQuantity] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const warehouses = useWarehouseIdOptions();
  const locations = useLocationOptions(warehouseId);
  const lots = useLotOptions(item?.track_lot ? item.id : "");
  const rolls = useRollOptions(item?.track_roll ? item.id : "", lotId);
  const op = useStockOperation(postOpeningStock, onSaved, [
    ...(initialItem === null ? ["inventory_item_id"] : []),
    "location_id",
    ...(item?.track_lot ? ["lot_id"] : []),
    ...(item?.track_roll ? ["roll_id"] : []),
    "quantity",
    "status",
    "occurred_on",
    "note",
  ]);

  // The item's own warehouse is the likeliest answer; pre-select it once the directory is in, but
  // never override a choice the person already made.
  const chosenByDefault = useRef(false);
  useEffect(() => {
    if (chosenByDefault.current || warehouseId !== "" || warehouses.status !== "success" || !item) return;
    const match = warehouses.options.find((o) => o.label === item.warehouse);
    chosenByDefault.current = true;
    if (match) setWarehouseId(match.value);
  }, [warehouses.status, warehouses.options, warehouseId, item]);

  const dirty =
    (initialItem === null && item !== null) ||
    locationId !== "" ||
    lotId !== "" ||
    rollId !== "" ||
    quantity !== "" ||
    note !== "" ||
    status !== "AVAILABLE" ||
    occurredOn !== today();

  function submit() {
    const found: Record<string, string> = {};
    if (item === null) found["inventory_item_id"] = "Choose the item this stock is for.";
    if (warehouseId === "") found["warehouse"] = "Choose a warehouse.";
    else if (locationId === "") found["location_id"] = "Choose the location the stock is in.";
    if (item?.track_lot && lotId === "") found["lot_id"] = "Choose the lot this stock belongs to.";
    if (item?.track_roll && rollId === "") found["roll_id"] = "Choose the roll this stock belongs to.";
    const checked = checkQuantity(quantity, { uom: item?.uom ?? "" });
    if (!checked.ok) found["quantity"] = checked.message;
    const dateProblem = occurredOnError(occurredOn);
    if (dateProblem) found["occurred_on"] = dateProblem;
    setErrors(found);
    if (Object.keys(found).length > 0 || item === null || !checked.ok) return;

    const line: OpeningLineIn = {
      inventory_item_id: item.id,
      location_id: locationId,
      quantity: checked.value,
      status,
      ...(item.track_lot ? { lot_id: lotId } : {}),
      ...(item.track_roll ? { roll_id: rollId } : {}),
    };
    op.submit({
      lines: [line],
      occurred_on: occurredOn,
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  const err = (field: string): string | undefined => errors[field] ?? op.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Opening stock"
      description={
        initialItem
          ? `${initialItem.sku} · ${initialItem.name}`
          : "Bring stock that already exists onto the books."
      }
      submitLabel="Post opening stock"
      pendingLabel="Posting…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...op.dialogError}
    >
      {initialItem === null ? (
        <ItemPicker
          value={item}
          onChange={(next) => {
            setItem(next);
            setLotId("");
            setRollId("");
          }}
          error={err("inventory_item_id")}
        />
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          ref={firstFieldRef}
          label="Warehouse"
          value={warehouseId}
          onChange={(e) => {
            setWarehouseId(e.target.value);
            setLocationId("");
          }}
          placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
          options={warehouses.options}
          disabled={warehouses.status !== "success" || warehouses.options.length === 0}
          required
          error={err("warehouse")}
        />
        <Select
          label="Location"
          value={locationId}
          onChange={(e) => setLocationId(e.target.value)}
          placeholder={locations.status === "loading" ? "Loading locations…" : "Choose a location"}
          options={locations.options}
          disabled={locations.status !== "success" || locations.options.length === 0}
          required
          error={err("location_id")}
        />
      </div>
      {item?.track_lot ? (
        <Select
          label="Lot"
          value={lotId}
          onChange={(e) => {
            setLotId(e.target.value);
            setRollId("");
          }}
          placeholder={lots.status === "loading" ? "Loading lots…" : "Choose a lot"}
          options={lots.options}
          disabled={lots.status !== "success" || lots.options.length === 0}
          required
          helper={
            lots.status === "success" && lots.options.length === 0
              ? "This item has no lots yet. Create a lot first."
              : undefined
          }
          error={err("lot_id")}
        />
      ) : null}
      {item?.track_roll ? (
        <Select
          label="Roll"
          value={rollId}
          onChange={(e) => setRollId(e.target.value)}
          placeholder={rolls.status === "loading" ? "Loading rolls…" : "Choose a roll"}
          options={rolls.options}
          disabled={rolls.status !== "success" || rolls.options.length === 0}
          required
          helper={
            rolls.status === "success" && rolls.options.length === 0
              ? "No active roll to receive stock. Create a roll first."
              : undefined
          }
          error={err("roll_id")}
        />
      ) : null}
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label={item ? `Quantity (${item.uom})` : "Quantity"}
          inputMode="decimal"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          required
          helper="Up to 3 decimals."
          error={err("quantity")}
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as OnSiteStatus)}
          options={STOCK_STATUS_OPTIONS}
          error={err("status")}
        />
      </div>
      <DateAndNoteFields
        occurredOn={occurredOn}
        onOccurredOn={setOccurredOn}
        note={note}
        onNote={setNote}
        dateError={err("occurred_on")}
        noteError={err("note")}
      />
    </FormDialog>
  );
}
