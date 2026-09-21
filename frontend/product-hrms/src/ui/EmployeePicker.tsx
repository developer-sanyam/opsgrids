/**
 * The person picker for a reporting line — `GET /employees?q=`, the same search the directory's
 * own search box uses (`api/client.ts::listEmployees`). Used by `EmployeeForm` for a reporting
 * manager and a dotted-line manager; every business rule (no self-reference, no cycle, no exited
 * manager) is the server's own 422/409, rendered on the field, never re-decided here.
 */

import { Combobox } from "@opsgrids/ui";
import type { ComboboxOption } from "@opsgrids/ui";

import { listEmployees } from "@/api/client";

const MANAGER_COPY = {
  idle: "Start typing a colleague's name.",
  error: "Couldn't load colleagues.",
};

interface EmployeePickerProps {
  label: string;
  value: ComboboxOption | null;
  onChange: (option: ComboboxOption | null) => void;
  helper?: string | undefined;
  error?: string | undefined;
}

export function EmployeePicker({ label, value, onChange, helper, error }: EmployeePickerProps) {
  return (
    <Combobox
      label={label}
      value={value}
      onChange={onChange}
      loadOptions={async (q) => {
        const result = await listEmployees({ q, size: 20 });
        return result.items.map((e) => ({ id: e.id, label: e.full_name, sublabel: e.employee_no }));
      }}
      placeholder="Search for a colleague…"
      copy={MANAGER_COPY}
      {...(helper !== undefined ? { helper } : {})}
      {...(error !== undefined ? { error } : {})}
    />
  );
}
