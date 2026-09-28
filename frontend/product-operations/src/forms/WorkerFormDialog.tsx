import { useRef, useState } from "react";

import { createWorker, listWarehouses, updateWorker } from "@/api/client";
import type { WorkerIn, WorkerOut, WorkerPatch } from "@/api/types";
import { useAsync, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, Switch, TextField } from "@/ui";

const UUID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

interface WorkerFormDialogProps {
  /** `null` creates. */
  worker: WorkerOut | null;
  onClose: () => void;
  onSaved: (worker: WorkerOut) => void;
}

/**
 * Create or edit a worker directory row — `POST` / `PATCH /operations/fulfilment/workers`
 * (`operations.fulfilment.worker.write:ORG`, plan F3). `employee_id` names an
 * `services/employee` `Employee.id` (ADR-0000 §1) — a plain UUID, never a cross-schema lookup
 * from `product-operations`, so this is a typed field, not a picker: whoever runs the roster
 * already has the employee id from the HR console.
 */
export function WorkerFormDialog({ worker, onClose, onSaved }: WorkerFormDialogProps) {
  const [employeeId, setEmployeeId] = useState(worker?.employee_id ?? "");
  const [warehouseId, setWarehouseId] = useState(worker?.warehouse_id ?? "");
  const [displayName, setDisplayName] = useState(worker?.display_name ?? "");
  const [isActive, setIsActive] = useState(worker?.is_active ?? true);
  const [skillsText, setSkillsText] = useState((worker?.skills ?? []).join(", "));

  const [employeeIdError, setEmployeeIdError] = useState<string | undefined>(undefined);
  const [warehouseIdError, setWarehouseIdError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouseOptions =
    warehousesState.status === "success"
      ? warehousesState.data.items.map((w) => ({ value: w.id, label: w.name }))
      : [];

  const dirty =
    employeeId !== (worker?.employee_id ?? "") ||
    warehouseId !== (worker?.warehouse_id ?? "") ||
    displayName !== (worker?.display_name ?? "") ||
    isActive !== (worker?.is_active ?? true) ||
    skillsText !== (worker?.skills ?? []).join(", ");

  const save = useMutation(
    (input: WorkerIn | WorkerPatch) =>
      worker ? updateWorker(worker.id, input as WorkerPatch) : createWorker(input as WorkerIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setEmployeeIdError(undefined);
    setWarehouseIdError(undefined);

    const trimmedEmployeeId = employeeId.trim();
    if (!worker && !UUID_PATTERN.test(trimmedEmployeeId)) {
      setEmployeeIdError("Paste the employee's id from the HR console.");
      firstFieldRef.current?.focus();
      return;
    }
    if (warehouseId.trim().length < 1) {
      setWarehouseIdError("Choose the warehouse this worker is assigned to.");
      return;
    }
    const skills = skillsText
      .split(",")
      .map((s) => s.trim())
      .filter((s) => s.length > 0);

    if (worker) {
      void save.mutate({
        warehouse_id: warehouseId,
        display_name: displayName.trim(),
        is_active: isActive,
        skills,
      });
      return;
    }
    void save.mutate({
      employee_id: trimmedEmployeeId,
      warehouse_id: warehouseId,
      display_name: displayName.trim(),
      is_active: isActive,
      skills,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={worker ? `Edit ${worker.display_name || "worker"}` : "New worker"}
      submitLabel={worker ? "Save changes" : "Add worker"}
      pendingLabel={worker ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Employee id"
        value={employeeId}
        onChange={(e) => setEmployeeId(e.target.value)}
        disabled={worker !== null}
        required
        helper={worker ? "A worker's employee link can't change." : "From the HR console's employee record."}
        {...((employeeIdError ?? save.fieldErrors.employee_id) !== undefined
          ? { error: employeeIdError ?? save.fieldErrors.employee_id }
          : {})}
      />
      <Select
        label="Warehouse"
        value={warehouseId}
        onChange={(e) => setWarehouseId(e.target.value)}
        options={warehouseOptions}
        placeholder="Choose a warehouse"
        required
        disabled={warehousesState.status !== "success"}
        {...((warehouseIdError ?? save.fieldErrors.warehouse_id) !== undefined
          ? { error: warehouseIdError ?? save.fieldErrors.warehouse_id }
          : {})}
      />
      <TextField
        label="Display name"
        value={displayName}
        onChange={(e) => setDisplayName(e.target.value)}
        optional
        helper="Shown on the task board and the worker roster."
        {...(save.fieldErrors.display_name !== undefined ? { error: save.fieldErrors.display_name } : {})}
      />
      <TextField
        label="Skills"
        value={skillsText}
        onChange={(e) => setSkillsText(e.target.value)}
        optional
        helper="Comma-separated — forklift, cold_storage."
        {...(save.fieldErrors.skills !== undefined ? { error: save.fieldErrors.skills } : {})}
      />
      <Switch
        checked={isActive}
        onChange={setIsActive}
        label="Active"
        description={isActive ? "Can be offered pick and pack tasks." : "Cannot be offered new tasks."}
      />
    </FormDialog>
  );
}
