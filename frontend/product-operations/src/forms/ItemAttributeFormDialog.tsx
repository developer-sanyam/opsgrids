import { useRef, useState } from "react";

import { setItemAttribute } from "@/api/client";
import type { ItemAttributeIn, ItemAttributeOut, ItemAttributeType } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, RadioGroup, TextField } from "@/ui";

const TYPE_OPTIONS: { value: ItemAttributeType; label: string }[] = [
  { value: "text", label: "Text" },
  { value: "number", label: "Number" },
  { value: "boolean", label: "Yes / no" },
  { value: "date", label: "Date" },
];

interface ItemAttributeFormDialogProps {
  itemId: string;
  /** `null` creates. */
  attribute: ItemAttributeOut | null;
  onClose: () => void;
  onSaved: (attribute: ItemAttributeOut) => void;
}

function currentValue(attribute: ItemAttributeOut | null): string {
  if (!attribute) return "";
  switch (attribute.value_type) {
    case "text":
      return attribute.value_text ?? "";
    case "number":
      return attribute.value_number !== null ? String(attribute.value_number) : "";
    case "boolean":
      return attribute.value_boolean ? "true" : "false";
    case "date":
      return attribute.value_date ?? "";
  }
}

/**
 * Set (create or overwrite) one typed attribute — `PUT
 * /operations/inventory/{id}/attributes/{key}`. Typed values only (ADR-0014 Phase 1): the form
 * always sends exactly the one `value_*` field matching the chosen type, mirroring the API's own
 * refusal of anything else.
 */
export function ItemAttributeFormDialog({
  itemId,
  attribute,
  onClose,
  onSaved,
}: ItemAttributeFormDialogProps) {
  const [key, setKey] = useState(attribute?.key ?? "");
  const [valueType, setValueType] = useState<ItemAttributeType>(attribute?.value_type ?? "text");
  const [value, setValue] = useState(currentValue(attribute));

  const [keyError, setKeyError] = useState<string | undefined>(undefined);
  const [valueError, setValueError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `attribute` never changes under it.
  const dirty =
    key !== (attribute?.key ?? "") ||
    valueType !== (attribute?.value_type ?? "text") ||
    value !== currentValue(attribute);

  const save = useMutation(
    (input: ItemAttributeIn) => setItemAttribute(itemId, key.trim(), input),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setKeyError(undefined);
    setValueError(undefined);

    const trimmedKey = key.trim();
    if (trimmedKey.length < 1) {
      setKeyError("Give the attribute a key, like gsm or shade.");
      firstFieldRef.current?.focus();
      return;
    }

    const trimmedValue = value.trim();
    if (trimmedValue.length < 1 && valueType !== "boolean") {
      setValueError("Give it a value.");
      return;
    }

    let input: ItemAttributeIn;
    switch (valueType) {
      case "text":
        input = { value_type: "text", value_text: trimmedValue };
        break;
      case "number": {
        const parsed = Number(trimmedValue);
        if (!Number.isFinite(parsed) || !Number.isInteger(parsed)) {
          setValueError("Numeric attributes are whole numbers — GSM, mm — never a fraction.");
          return;
        }
        input = { value_type: "number", value_number: parsed };
        break;
      }
      case "boolean":
        input = { value_type: "boolean", value_boolean: trimmedValue === "true" };
        break;
      case "date":
        input = { value_type: "date", value_date: trimmedValue };
        break;
    }
    void save.mutate(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={attribute ? `Edit ${attribute.key}` : "Add attribute"}
      submitLabel={attribute ? "Save changes" : "Add attribute"}
      pendingLabel={attribute ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Key"
        value={key}
        onChange={(e) => setKey(e.target.value)}
        disabled={attribute !== null}
        required
        helper={attribute ? "A key can't change once set — remove and re-add instead." : "gsm, width, shade, composition…"}
        {...((keyError ?? save.fieldErrors.key) !== undefined ? { error: keyError ?? save.fieldErrors.key } : {})}
      />
      <RadioGroup
        legend="Type"
        name="attribute-type"
        options={TYPE_OPTIONS}
        value={valueType}
        onChange={(next) => {
          setValueType(next as ItemAttributeType);
          setValue("");
        }}
      />
      {valueType === "boolean" ? (
        <RadioGroup
          legend="Value"
          name="attribute-boolean-value"
          options={[
            { value: "true", label: "Yes" },
            { value: "false", label: "No" },
          ]}
          value={value || "false"}
          onChange={setValue}
        />
      ) : (
        <TextField
          label="Value"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          type={valueType === "date" ? "date" : "text"}
          inputMode={valueType === "number" ? "numeric" : undefined}
          helper={valueType === "number" ? "A whole number, e.g. 180." : undefined}
          required
          {...((valueError ?? save.fieldErrors[`value_${valueType}`]) !== undefined
            ? { error: valueError ?? save.fieldErrors[`value_${valueType}`] }
            : {})}
        />
      )}
    </FormDialog>
  );
}
