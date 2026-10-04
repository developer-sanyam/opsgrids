import { useRef, useState } from "react";

import { recordProductionOutput } from "@/api/client";
import type { OutputIn, OutputStatus, ProductionActionOut, ProductionOrderDetailOut } from "@/api/types";
import { useLocationOptions } from "@/data/useLocationOptions";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextArea, TextField } from "@/ui";

interface OutputDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

const STATUS_OPTIONS: { value: OutputStatus; label: string }[] = [
  { value: "AVAILABLE", label: "Available" },
  { value: "QUALITY_HOLD", label: "Quality hold" },
];

/**
 * `POST .../orders/{id}/outputs` — `...production_order.complete`. Records good output: consumes
 * WIP at standard and receives finished goods into the chosen location, as available stock or on
 * quality hold. Lot and roll ids are optional and only name what was produced.
 */
export function OutputDialog({ order, onClose, onDone }: OutputDialogProps) {
  const [quantity, setQuantity] = useState("");
  const [locationId, setLocationId] = useState(order.output_location_id);
  const [lotId, setLotId] = useState("");
  const [rollId, setRollId] = useState("");
  const [status, setStatus] = useState<OutputStatus>("AVAILABLE");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const locations = useLocationOptions();

  const record = useOrderAction<OutputIn>((input, options) => recordProductionOutput(order.id, input, options), onDone);

  function submit() {
    const next: Record<string, string> = {};
    const qtyError = qtyFieldError(quantity);
    if (qtyError) next.quantity = qtyError;
    if (locationId === "") next.location_id = "Choose where the output is received.";
    setErrors(next);
    if (Object.keys(next).length > 0) {
      if (next.quantity) firstFieldRef.current?.focus();
      return;
    }
    void record.mutate({
      quantity: quantity.trim(),
      location_id: locationId,
      status,
      note: note.trim(),
      ...(lotId.trim() !== "" ? { lot_id: lotId.trim() } : {}),
      ...(rollId.trim() !== "" ? { roll_id: rollId.trim() } : {}),
    });
  }

  const fieldError = (field: string) => errors[field] ?? record.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Record output"
      description={`Finished ${order.sku} received into stock. Planned ${order.planned_quantity} ${order.uom}, produced so far ${order.produced_quantity}.`}
      submitLabel="Record output"
      pendingLabel="Recording…"
      pending={record.pending}
      initialFocusRef={firstFieldRef}
      dirty={quantity !== "" || lotId !== "" || rollId !== "" || note !== "" || status !== "AVAILABLE" || locationId !== order.output_location_id}
      {...formDialogError(record.error, record.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        helper={`In ${order.uom}.`}
        required
        error={fieldError("quantity")}
      />
      <Select
        label="Location"
        value={locationId}
        onChange={(e) => setLocationId(e.target.value)}
        options={locations.options}
        placeholder={locations.status === "loading" ? "Loading locations…" : "Choose a location"}
        disabled={locations.status !== "success"}
        required
        error={fieldError("location_id")}
      />
      <Select
        label="Stock status"
        value={status}
        onChange={(e) => setStatus(e.target.value as OutputStatus)}
        options={STATUS_OPTIONS}
        error={fieldError("status")}
      />
      <TextField label="Lot id" value={lotId} onChange={(e) => setLotId(e.target.value)} optional error={fieldError("lot_id")} />
      <TextField label="Roll id" value={rollId} onChange={(e) => setRollId(e.target.value)} optional error={fieldError("roll_id")} />
      <TextArea
        label="Note"
        rows={2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={300}
        counterFrom={250}
        error={fieldError("note")}
      />
    </FormDialog>
  );
}
