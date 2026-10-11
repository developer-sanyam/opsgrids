import { useRef, useState } from "react";

import { recordProductionWastage } from "@/api/client";
import type { ProductionActionOut, ProductionOrderDetailOut, WastageIn, WastageType } from "@/api/types";
import { MANUAL_WASTAGE_OPTIONS } from "@/data/wastageTypes";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { formatQty, qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextArea, TextField } from "@/ui";

interface WastageDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

/**
 * `POST .../orders/{id}/wastage` — `...production_order.issue`. Writes off material that is in WIP
 * as wastage of a type. LEFTOVER is never offered: it is written by the system when an order is
 * completed with "scrap". Crossing a configured threshold raises an alert on the server and does
 * not block this. The server's 409 (more than is in WIP) is shown as the dialog's message.
 */
export function WastageDialog({ order, onClose, onDone }: WastageDialogProps) {
  const [materialId, setMaterialId] = useState("");
  const [type, setType] = useState<WastageType | "">("");
  const [quantity, setQuantity] = useState("");
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  const record = useOrderAction<WastageIn>((input, options) => recordProductionWastage(order.id, input, options), onDone);

  function submit() {
    const next: Record<string, string> = {};
    if (materialId === "") next.material_id = "Choose the material that was lost.";
    if (type === "") next.wastage_type = "Choose the kind of wastage.";
    const qtyError = qtyFieldError(quantity);
    if (qtyError) next.quantity = qtyError;
    setErrors(next);
    if (Object.keys(next).length > 0 || type === "") return;
    void record.mutate({ material_id: materialId, wastage_type: type, quantity: quantity.trim(), note: note.trim() });
  }

  const fieldError = (field: string) => errors[field] ?? record.fieldErrors[field];
  const materialOptions = order.materials.map((m) => ({
    value: m.id,
    label: `${m.sku} — ${formatQty(m.wip)} ${m.uom} in WIP`,
  }));

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Record wastage"
      description="Writes off material that is in work in progress."
      submitLabel="Record wastage"
      pendingLabel="Recording…"
      pending={record.pending}
      initialFocusRef={firstFieldRef}
      dirty={materialId !== "" || type !== "" || quantity !== "" || note !== ""}
      {...formDialogError(record.error, record.fieldErrors)}
    >
      <Select
        ref={firstFieldRef}
        label="Material"
        value={materialId}
        onChange={(e) => setMaterialId(e.target.value)}
        options={materialOptions}
        placeholder="Choose a material"
        required
        error={fieldError("material_id")}
      />
      <Select
        label="Wastage type"
        value={type}
        onChange={(e) => setType(e.target.value as WastageType)}
        options={MANUAL_WASTAGE_OPTIONS}
        placeholder="Choose a type"
        required
        error={fieldError("wastage_type")}
      />
      <TextField
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        required
        error={fieldError("quantity")}
      />
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
