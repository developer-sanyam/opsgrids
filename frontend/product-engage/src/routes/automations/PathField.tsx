import { useId } from "react";

import type { TriggerVariable } from "@/api/automations";
import { TextField } from "@/ui";

/**
 * A field that reads a value from the event, like `payload.customer.name`.
 *
 * The trigger's variables are what the service knows about that event, and it says so is best
 * effort, so this is a text field with those paths as suggestions and not a closed list: a path
 * the catalogue missed can still be typed, and one the service rejects comes back as a problem on
 * the step. The suggestions carry the human label, so a person picks "Customer name" without
 * having to know its path.
 */
export function PathField({
  label,
  value,
  onChange,
  variables,
  helper,
  error,
  required = false,
  placeholder = "payload.…",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  variables: TriggerVariable[];
  helper?: string;
  error?: string;
  required?: boolean;
  placeholder?: string;
}) {
  const listId = useId();
  return (
    <>
      <TextField
        label={label}
        value={value}
        list={listId}
        required={required}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        {...(helper !== undefined ? { helper } : {})}
        {...(error !== undefined ? { error } : {})}
      />
      <datalist id={listId}>
        {variables.map((variable) => (
          <option key={variable.path} value={variable.path} label={variable.label} />
        ))}
      </datalist>
    </>
  );
}
