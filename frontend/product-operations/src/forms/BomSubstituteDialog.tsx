import { useState } from "react";

import { addBomSubstitute } from "@/api/client";
import type { BomComponentOut, BomVersionDetailOut } from "@/api/types";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useMutation } from "@opsgrids/client";
import { FormDialog, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface BomSubstituteDialogProps {
  bomId: string;
  versionId: string;
  component: BomComponentOut;
  onClose: () => void;
  onSaved: (version: BomVersionDetailOut) => void;
}

/**
 * `POST .../components/{id}/substitutes` — `operations.manufacturing.bom.write`. Declares an item
 * that may stand in for a component, with the ratio it replaces one unit at. A substitute that
 * would make the BOM contain itself is a 409, shown verbatim.
 */
export function BomSubstituteDialog({ bomId, versionId, component, onClose, onSaved }: BomSubstituteDialogProps) {
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [ratio, setRatio] = useState("1");
  const [priority, setPriority] = useState("1");
  const [errors, setErrors] = useState<Record<string, string>>({});

  const save = useMutation(
    (input: { substitute_item_id: string; ratio: string; priority: number }) =>
      addBomSubstitute(bomId, versionId, component.id, input),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    const next: Record<string, string> = {};
    if (item === null) next.substitute_item_id = "Choose the substitute item.";
    const ratioError = qtyFieldError(ratio);
    if (ratioError) next.ratio = ratioError;
    const priorityValue = Number.parseInt(priority, 10);
    if (!/^\d+$/.test(priority.trim()) || priorityValue < 1 || priorityValue > 99) {
      next.priority = "Enter a whole number from 1 to 99.";
    }
    setErrors(next);
    if (Object.keys(next).length > 0 || item === null) return;
    void save.mutate({ substitute_item_id: item.id, ratio: ratio.trim(), priority: priorityValue });
  }

  const fieldError = (field: string) => errors[field] ?? save.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Add substitute for ${component.sku}`}
      description="An order can use a declared substitute in place of this component."
      submitLabel="Add substitute"
      pendingLabel="Adding…"
      pending={save.pending}
      dirty={item !== null || ratio !== "1" || priority !== "1"}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <OrderItemPicker
        label="Substitute item"
        value={item}
        onChange={setItem}
        error={fieldError("substitute_item_id")}
      />
      <TextField
        label="Ratio"
        inputMode="decimal"
        value={ratio}
        onChange={(e) => setRatio(e.target.value)}
        helper={`How much substitute replaces one ${component.uom} of ${component.sku}: 1 is 1:1, 1.25 is 1.25.`}
        required
        error={fieldError("ratio")}
      />
      <TextField
        label="Priority"
        inputMode="numeric"
        value={priority}
        onChange={(e) => setPriority(e.target.value)}
        helper="1 is tried first."
        required
        error={fieldError("priority")}
      />
    </FormDialog>
  );
}
