import { useRef, useState } from "react";

import { createLot } from "@/api/tracking";
import type { LotIn, LotOut, LotQualityStatus } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";
import { LOT_QUALITY_OPTIONS } from "@/lib/trackingLabels";
import { ItemPicker } from "@/tracking/ItemPicker";
import { useStockOperation } from "@/tracking/useStockOperation";
import { FormDialog, InlineAlert, Select, TextField } from "@/ui";

const LOT_NO_PATTERN = /^[A-Za-z0-9._/-]+$/;

interface LotFormDialogProps {
  /** The item the lot is for. `null` asks for one, among lot-tracked items. */
  item: InventoryItemOut | null;
  onClose: () => void;
  onSaved: (lot: LotOut) => void;
}

/**
 * Register a lot — `POST /operations/inventory/lots` (`operations.inventory.lot.write`). A lot
 * holds no stock; it is the batch an opening or receipt later posts into. Only a lot-tracked item
 * can have one, so the picker offers only those and the server's `not_tracked` refusal is mapped
 * onto the item field if one slips through. The lot number is generated when left blank.
 */
export function LotFormDialog({ item: initialItem, onClose, onSaved }: LotFormDialogProps) {
  const [item, setItem] = useState<InventoryItemOut | null>(initialItem);
  const [lotNo, setLotNo] = useState("");
  const [supplierLotNo, setSupplierLotNo] = useState("");
  const [manufacturedOn, setManufacturedOn] = useState("");
  const [receivedOn, setReceivedOn] = useState("");
  const [expiresOn, setExpiresOn] = useState("");
  const [quality, setQuality] = useState<LotQualityStatus>("PENDING");

  const [itemError, setItemError] = useState<string | undefined>(undefined);
  const [lotNoError, setLotNoError] = useState<string | undefined>(undefined);
  const [expiresError, setExpiresError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const op = useStockOperation(createLot, onSaved, [
    ...(initialItem === null ? ["inventory_item_id"] : []),
    "lot_no",
    "supplier_lot_no",
    "manufactured_on",
    "received_on",
    "expires_on",
    "quality_status",
  ]);

  const dirty =
    (initialItem === null && item !== null) ||
    lotNo !== "" ||
    supplierLotNo !== "" ||
    manufacturedOn !== "" ||
    receivedOn !== "" ||
    expiresOn !== "" ||
    quality !== "PENDING";

  function submit() {
    setItemError(undefined);
    setLotNoError(undefined);
    setExpiresError(undefined);
    let valid = true;
    if (item === null) {
      setItemError("Choose the item this lot is for.");
      valid = false;
    } else if (!item.track_lot) {
      setItemError(`${item.sku} isn't tracked by lot. Turn on lot tracking for the item first.`);
      valid = false;
    }
    const trimmedLotNo = lotNo.trim();
    if (trimmedLotNo !== "" && (trimmedLotNo.length > 60 || !LOT_NO_PATTERN.test(trimmedLotNo))) {
      setLotNoError("Letters, numbers, dots, slashes, hyphens and underscores only, up to 60.");
      firstFieldRef.current?.focus();
      valid = false;
    }
    if (manufacturedOn !== "" && expiresOn !== "" && expiresOn < manufacturedOn) {
      setExpiresError("Expiry can't be before the manufacturing date.");
      valid = false;
    }
    if (!valid || item === null) return;
    const input: LotIn = {
      inventory_item_id: item.id,
      quality_status: quality,
      ...(trimmedLotNo ? { lot_no: trimmedLotNo } : {}),
      ...(supplierLotNo.trim() ? { supplier_lot_no: supplierLotNo.trim() } : {}),
      ...(manufacturedOn ? { manufactured_on: manufacturedOn } : {}),
      ...(receivedOn ? { received_on: receivedOn } : {}),
      ...(expiresOn ? { expires_on: expiresOn } : {}),
    };
    op.submit(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Create lot"
      description={initialItem ? `${initialItem.sku} · ${initialItem.name}` : undefined}
      submitLabel="Create lot"
      pendingLabel="Creating…"
      pending={op.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...op.dialogError}
    >
      {initialItem !== null && !initialItem.track_lot ? (
        <InlineAlert tone="warning" title={`${initialItem.sku} isn't tracked by lot`}>
          Turn on lot tracking for the item first. A lot can only be registered for a lot-tracked item.
        </InlineAlert>
      ) : null}
      {initialItem === null ? (
        <ItemPicker
          value={item}
          onChange={setItem}
          requires="track_lot"
          helper="Only items with lot tracking turned on."
          error={itemError ?? op.fieldErrors["inventory_item_id"]}
        />
      ) : null}
      <TextField
        ref={firstFieldRef}
        label="Lot number"
        value={lotNo}
        onChange={(e) => setLotNo(e.target.value)}
        optional
        helper="Leave blank to generate one."
        error={lotNoError ?? op.fieldErrors["lot_no"]}
      />
      <TextField
        label="Supplier lot number"
        value={supplierLotNo}
        onChange={(e) => setSupplierLotNo(e.target.value)}
        optional
        error={op.fieldErrors["supplier_lot_no"]}
      />
      <div className="grid gap-4 sm:grid-cols-3">
        <TextField
          label="Manufactured on"
          type="date"
          value={manufacturedOn}
          onChange={(e) => setManufacturedOn(e.target.value)}
          optional
          error={op.fieldErrors["manufactured_on"]}
        />
        <TextField
          label="Received on"
          type="date"
          value={receivedOn}
          onChange={(e) => setReceivedOn(e.target.value)}
          optional
          error={op.fieldErrors["received_on"]}
        />
        <TextField
          label="Expires on"
          type="date"
          value={expiresOn}
          onChange={(e) => setExpiresOn(e.target.value)}
          optional
          error={expiresError ?? op.fieldErrors["expires_on"]}
        />
      </div>
      <Select
        label="Quality status"
        value={quality}
        onChange={(e) => setQuality(e.target.value as LotQualityStatus)}
        options={LOT_QUALITY_OPTIONS}
        error={op.fieldErrors["quality_status"]}
      />
    </FormDialog>
  );
}
