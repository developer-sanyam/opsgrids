import { useRef, useState } from "react";

import { createWarehouse, updateWarehouse } from "@/api/client";
import type { WarehouseIn, WarehouseOut, WarehousePatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, TextField } from "@/ui";

const CODE_PATTERN = /^[A-Za-z0-9_-]+$/;

interface WarehouseFormDialogProps {
  /** `null` creates. */
  warehouse: WarehouseOut | null;
  onClose: () => void;
  onSaved: (warehouse: WarehouseOut) => void;
}

/**
 * Create or edit a warehouse — `POST /operations/warehouses` / `PATCH /operations/warehouses/{id}`.
 * `code` is create-only: it never appears on the patch, and renaming cascades to every inventory
 * item and purchase order that carries the warehouse's name.
 */
export function WarehouseFormDialog({ warehouse, onClose, onSaved }: WarehouseFormDialogProps) {
  const [code, setCode] = useState(warehouse?.code ?? "");
  const [name, setName] = useState(warehouse?.name ?? "");
  const [location, setLocation] = useState(warehouse?.location ?? "");

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: WarehouseIn | WarehousePatch) =>
      warehouse
        ? updateWarehouse(warehouse.id, input as WarehousePatch)
        : createWarehouse(input as WarehouseIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);

    const trimmedCode = code.trim();
    if (!warehouse && (trimmedCode.length < 1 || !CODE_PATTERN.test(trimmedCode))) {
      setCodeError("Letters, numbers, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the warehouse a name.");
      return;
    }

    if (warehouse) {
      void save.mutate({ name: trimmedName, location: location.trim() });
      return;
    }
    void save.mutate({ code: trimmedCode, name: trimmedName, location: location.trim() });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={warehouse ? `Edit ${warehouse.name}` : "New warehouse"}
      submitLabel={warehouse ? "Save changes" : "Create warehouse"}
      pendingLabel={warehouse ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        disabled={warehouse !== null}
        helper={warehouse ? "A warehouse's code can't change." : "Letters, numbers, - and _."}
        {...((codeError ?? save.fieldErrors.code) !== undefined ? { error: codeError ?? save.fieldErrors.code } : {})}
      />
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        helper="Shown everywhere the warehouse is referenced — inventory, purchase orders."
        {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
      />
      <TextField
        label="Location"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        optional
        {...((save.fieldErrors.location) !== undefined ? { error: save.fieldErrors.location } : {})}
      />
    </FormDialog>
  );
}
