import { useRef, useState } from "react";

import { createProductionOrder, updateProductionOrder } from "@/api/client";
import type { ProductionOrderDetailOut, ProductionOrderIn, ProductionOrderPatch } from "@/api/types";
import { useLocationOptions } from "@/data/useLocationOptions";
import { BomPicker } from "@/forms/BomPicker";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { useMutation } from "@opsgrids/client";
import { FormDialog, RadioGroup, Select, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface ProductionOrderFormDialogProps {
  /** Present when editing a draft order; absent when planning a new one. */
  order?: ProductionOrderDetailOut;
  onClose: () => void;
  onSaved: (order: ProductionOrderDetailOut) => void;
}

type Source = "bom" | "item";

const NO_WIP = "";

/**
 * Plan (`POST .../orders`, `...production_order.write`) or edit a draft (`PATCH .../orders/{id}`).
 * A new order names a BOM, or the item to produce when that item has one live BOM; the server pins
 * the version in force on the planned start (or today). Quantities travel as typed text.
 */
export function ProductionOrderFormDialog({ order, onClose, onSaved }: ProductionOrderFormDialogProps) {
  const editing = order !== undefined;
  const [source, setSource] = useState<Source>("bom");
  const [bom, setBom] = useState<ComboboxOption | null>(null);
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [quantity, setQuantity] = useState(order?.planned_quantity ?? "");
  const [outputLocation, setOutputLocation] = useState(order?.output_location_id ?? "");
  const [wipLocation, setWipLocation] = useState(order?.wip_location_id ?? NO_WIP);
  const [plannedStart, setPlannedStart] = useState(order?.planned_start ?? "");
  const [dueDate, setDueDate] = useState(order?.due_date ?? "");
  const [notes, setNotes] = useState(order?.notes ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const keyFor = useIdempotencyKeys();
  const locations = useLocationOptions();

  const dirty = editing
    ? quantity !== order.planned_quantity ||
      outputLocation !== order.output_location_id ||
      wipLocation !== (order.wip_location_id ?? NO_WIP) ||
      plannedStart !== (order.planned_start ?? "") ||
      dueDate !== (order.due_date ?? "") ||
      notes !== order.notes
    : bom !== null || item !== null || quantity !== "" || outputLocation !== "" || wipLocation !== NO_WIP || plannedStart !== "" || dueDate !== "" || notes !== "";

  const save = useMutation(
    (input: { create: ProductionOrderIn } | { patch: ProductionOrderPatch }) =>
      "create" in input
        ? createProductionOrder(input.create, { idempotencyKey: keyFor(input.create) })
        : updateProductionOrder(order?.id ?? "", input.patch),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    const next: Record<string, string> = {};
    if (!editing) {
      if (source === "bom" && bom === null) next.bom_id = "Choose the BOM to produce from.";
      if (source === "item" && item === null) next.item_id = "Choose the item to produce.";
    }
    const qtyError = qtyFieldError(quantity);
    if (qtyError) next.quantity = qtyError;
    if (outputLocation === "") next.output_location_id = "Choose where the finished goods go.";
    if (plannedStart !== "" && dueDate !== "" && dueDate < plannedStart) {
      next.due_date = "The due date can't be before the planned start.";
    }
    setErrors(next);
    if (Object.keys(next).length > 0) {
      if (next.quantity) firstFieldRef.current?.focus();
      return;
    }

    if (editing) {
      void save.mutate({
        patch: {
          quantity: quantity.trim(),
          output_location_id: outputLocation,
          wip_location_id: wipLocation === NO_WIP ? null : wipLocation,
          planned_start: plannedStart === "" ? null : plannedStart,
          due_date: dueDate === "" ? null : dueDate,
          notes: notes.trim(),
        },
      });
      return;
    }
    void save.mutate({
      create: {
        ...(source === "bom" && bom ? { bom_id: bom.id } : {}),
        ...(source === "item" && item ? { item_id: item.id } : {}),
        quantity: quantity.trim(),
        output_location_id: outputLocation,
        ...(wipLocation !== NO_WIP ? { wip_location_id: wipLocation } : {}),
        ...(plannedStart !== "" ? { planned_start: plannedStart } : {}),
        ...(dueDate !== "" ? { due_date: dueDate } : {}),
        notes: notes.trim(),
      },
    });
  }

  const fieldError = (field: string) => errors[field] ?? save.fieldErrors[field];
  const locationsLoading = locations.status === "loading";
  const locationPlaceholder = locationsLoading ? "Loading locations…" : "Choose a location";
  const locationsError = locations.status === "error" ? "Couldn't load locations. Close this dialog and try again." : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${order.number}` : "New production order"}
      description={
        editing
          ? "Only a draft order can be edited."
          : "Plans an order from a BOM. The BOM version in force on the planned start is pinned to it."
      }
      submitLabel={editing ? "Save changes" : "Create order"}
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      size="lg"
      {...formDialogError(save.error, save.fieldErrors)}
    >
      {editing ? (
        <p className="text-sm text-ink">
          <span className="font-semibold">{order.sku}</span>
          <span className="text-muted">
            {" "}
            · {order.name} · BOM {order.bom_code} version {order.bom_version_no}
          </span>
        </p>
      ) : (
        <>
          <RadioGroup
            legend="Plan from"
            name="production-source"
            value={source}
            onChange={(v) => setSource(v as Source)}
            options={[
              { value: "bom", label: "A BOM", description: "Choose the recipe directly." },
              { value: "item", label: "The item to produce", description: "Uses the item's one live BOM." },
            ]}
          />
          {source === "bom" ? (
            <BomPicker value={bom} onChange={setBom} error={fieldError("bom_id")} />
          ) : (
            <OrderItemPicker label="Item to produce" value={item} onChange={setItem} error={fieldError("item_id")} />
          )}
        </>
      )}
      <TextField
        ref={firstFieldRef}
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        helper={editing ? `In ${order.uom}.` : "How much of the produced item to make."}
        required
        error={fieldError("quantity")}
      />
      <Select
        label="Output location"
        value={outputLocation}
        onChange={(e) => setOutputLocation(e.target.value)}
        options={locations.options}
        placeholder={locationPlaceholder}
        disabled={locations.status !== "success"}
        helper="Where the finished goods are received."
        required
        error={fieldError("output_location_id") ?? locationsError}
      />
      <Select
        label="WIP location"
        value={wipLocation}
        onChange={(e) => setWipLocation(e.target.value)}
        options={[{ value: NO_WIP, label: "None — use the default" }, ...locations.options]}
        disabled={locations.status !== "success"}
        helper="Where issued material sits while it is being worked."
        error={fieldError("wip_location_id")}
      />
      <TextField
        label="Planned start"
        type="date"
        value={plannedStart}
        onChange={(e) => setPlannedStart(e.target.value)}
        optional
        error={fieldError("planned_start")}
      />
      <TextField
        label="Due date"
        type="date"
        value={dueDate}
        onChange={(e) => setDueDate(e.target.value)}
        optional
        error={fieldError("due_date")}
      />
      <TextArea
        label="Notes"
        rows={3}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={fieldError("notes")}
      />
    </FormDialog>
  );
}
