import { useState } from "react";

import { setWastageThreshold } from "@/api/client";
import type { WastageThresholdIn, WastageThresholdOut, WastageType } from "@/api/types";
import { ALL_WASTAGE_OPTIONS } from "@/data/wastageTypes";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { pctFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface WastageThresholdDialogProps {
  onClose: () => void;
  onSaved: (threshold: WastageThresholdOut) => void;
}

const ANY_TYPE = "";

/**
 * `POST /operations/manufacturing/wastage-thresholds` — `operations.manufacturing.bom.write`. A cap,
 * as a percentage, on the wastage of one produced item and/or one wastage type; leave either blank
 * to cover everything. Crossing it raises an alert on the server, it does not block recording.
 */
export function WastageThresholdDialog({ onClose, onSaved }: WastageThresholdDialogProps) {
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [type, setType] = useState<string>(ANY_TYPE);
  const [percent, setPercent] = useState("");
  const [percentError, setPercentError] = useState<string | undefined>(undefined);

  const save = useMutation((input: WastageThresholdIn) => setWastageThreshold(input), {
    onSuccess: (saved) => onSaved(saved),
  });

  function submit() {
    const error = pctFieldError(percent);
    setPercentError(error);
    if (error) return;
    void save.mutate({
      max_percent: percent.trim(),
      ...(item ? { item_id: item.id } : {}),
      ...(type !== ANY_TYPE ? { wastage_type: type as WastageType } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Add wastage threshold"
      description="Raises an alert when wastage goes past this share of what was consumed. Setting the same item and type again replaces its limit."
      submitLabel="Save threshold"
      pendingLabel="Saving…"
      pending={save.pending}
      dirty={item !== null || type !== ANY_TYPE || percent !== ""}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <OrderItemPicker
        label="Produced item"
        value={item}
        onChange={setItem}
        helper="Leave empty to cover every product."
        error={save.fieldErrors.item_id}
      />
      <Select
        label="Wastage type"
        value={type}
        onChange={(e) => setType(e.target.value)}
        options={[{ value: ANY_TYPE, label: "Every type" }, ...ALL_WASTAGE_OPTIONS]}
        error={save.fieldErrors.wastage_type}
      />
      <TextField
        label="Maximum percent"
        inputMode="decimal"
        value={percent}
        onChange={(e) => setPercent(e.target.value)}
        helper="0 to 100, up to two decimals."
        required
        error={percentError ?? save.fieldErrors.max_percent}
      />
    </FormDialog>
  );
}
