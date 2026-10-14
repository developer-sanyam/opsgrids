import { useRef, useState } from "react";

import {
  createInspection,
  listLocations,
  listQcTemplates,
  listWarehouses,
  searchInventory,
} from "@/api/client";
import type { InspectionIn, InspectionOut, InventoryItemOut, QcKind } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { isPositiveDecimal, useIdempotencyKey } from "@/lib/quality";
import { Combobox, FormDialog, Select, Switch, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface OpenInspectionDialogProps {
  onClose: () => void;
  onOpened: (inspection: InspectionOut) => void;
}

const KIND_OPTIONS: { value: QcKind; label: string }[] = [
  { value: "incoming", label: "Incoming (goods receipt)" },
  { value: "in-process", label: "In-process" },
  { value: "finished", label: "Finished goods" },
];

/**
 * `POST /operations/quality/inspections` — `operations.quality.inspection.inspect:ORG`. By default
 * the quantity is moved Available → Quality hold in the same step, so nothing can sell or issue
 * it while it is being inspected; a 409 means that much is not free (reserved, or already held).
 */
export function OpenInspectionDialog({ onClose, onOpened }: OpenInspectionDialogProps) {
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [warehouseId, setWarehouseId] = useState("");
  const [locationId, setLocationId] = useState("");
  const [kind, setKind] = useState<QcKind>("incoming");
  const [quantity, setQuantity] = useState("");
  const [templateId, setTemplateId] = useState("");
  const [holdStock, setHoldStock] = useState(true);
  const [notes, setNotes] = useState("");
  const [localError, setLocalError] = useState<string | undefined>(undefined);
  const itemCache = useRef<Map<string, InventoryItemOut>>(new Map());

  const warehouses = useAsync(() => listWarehouses({ size: 200 }), []);
  const locations = useAsync(
    () => (warehouseId ? listLocations(warehouseId) : Promise.resolve([])),
    [warehouseId],
  );
  const templates = useAsync(() => listQcTemplates({ kind, active: true, size: 200 }), [kind]);

  const payload: InspectionIn | null =
    item && locationId
      ? {
          kind,
          item_id: item.id,
          location_id: locationId,
          quantity: quantity.trim(),
          hold_stock: holdStock,
          ...(templateId ? { template_id: templateId } : {}),
          ...(notes.trim() ? { notes: notes.trim() } : {}),
        }
      : null;
  const key = useIdempotencyKey(JSON.stringify(payload));

  const open = useMutation((input: InspectionIn) => createInspection(input, key), {
    onSuccess: (saved) => onOpened(saved),
  });

  function submit() {
    setLocalError(undefined);
    if (!item) return setLocalError("Choose the item to inspect.");
    if (!locationId) return setLocalError("Choose where the stock is.");
    if (!isPositiveDecimal(quantity)) {
      return setLocalError("Enter a quantity above zero, with at most three decimals.");
    }
    if (payload) void open.mutate(payload);
  }

  const warehouseOptions =
    warehouses.status === "success"
      ? warehouses.data.items.map((w) => ({ value: w.id, label: `${w.code} · ${w.name}` }))
      : [];
  const locationOptions =
    locations.status === "success" ? locations.data.map((l) => ({ value: l.id, label: l.code })) : [];
  const templateOptions =
    templates.status === "success"
      ? templates.data.items
          .filter((t) => t.item_id === null || t.item_id === item?.id)
          .map((t) => ({ value: t.id, label: `${t.code} · ${t.name}` }))
      : [];
  const picked = item ? itemCache.current.get(item.id) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Open an inspection"
      description="Choose the stock to inspect. It is held aside from sale while it is being checked."
      submitLabel="Open inspection"
      pendingLabel="Opening…"
      pending={open.pending}
      size="lg"
      {...formDialogError(open.error, open.fieldErrors)}
    >
      <Combobox
        label="Item"
        value={item}
        onChange={setItem}
        loadOptions={async (q) => {
          const items = await searchInventory(q);
          for (const it of items) itemCache.current.set(it.id, it);
          return items.map((it) => ({ id: it.id, label: it.sku, sublabel: it.name }));
        }}
        placeholder="Look up a SKU or item name…"
        {...(open.fieldErrors.item_id !== undefined ? { error: open.fieldErrors.item_id } : {})}
      />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Warehouse"
          value={warehouseId}
          onChange={(e) => {
            setWarehouseId(e.target.value);
            setLocationId("");
          }}
          options={warehouseOptions}
          placeholder={warehouses.status === "loading" ? "Loading warehouses…" : "Choose a warehouse"}
          disabled={warehouses.status !== "success"}
          required
        />
        <Select
          label="Location"
          value={locationId}
          onChange={(e) => setLocationId(e.target.value)}
          options={locationOptions}
          placeholder={warehouseId ? "Choose a location" : "Choose a warehouse first"}
          disabled={!warehouseId || locations.status !== "success"}
          required
        />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Kind"
          value={kind}
          onChange={(e) => {
            setKind(e.target.value as QcKind);
            setTemplateId("");
          }}
          options={KIND_OPTIONS}
        />
        <TextField
          label={picked?.uom ? `Quantity (${picked.uom})` : "Quantity"}
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          inputMode="decimal"
          required
          {...(open.fieldErrors.quantity !== undefined ? { error: open.fieldErrors.quantity } : {})}
        />
      </div>
      <Select
        label="Template"
        value={templateId}
        onChange={(e) => setTemplateId(e.target.value)}
        options={templateOptions}
        placeholder="No template — record pass / fail observations"
        helper="A template lists the readings to take and the limits they are judged against."
        disabled={templates.status !== "success"}
      />
      <Switch
        label="Move to quality hold now"
        checked={holdStock}
        onChange={setHoldStock}
        description={
          holdStock
            ? "The quantity leaves Available and sits in Quality hold until released or rejected."
            : "The stock is already in Quality hold (received there) — nothing moves."
        }
      />
      <TextArea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        maxLength={500}
        optional
      />
      {localError ? (
        <p role="alert" className="text-sm text-danger">
          {localError}
        </p>
      ) : null}
    </FormDialog>
  );
}
