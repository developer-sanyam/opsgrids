import { useRef, useState } from "react";

import { createWarehouseFloor, updateWarehouseFloor } from "@/api/client";
import type { WarehouseFloorIn, WarehouseFloorOut, WarehouseFloorPatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, TextField } from "@/ui";

interface WarehouseFloorFormDialogProps {
  warehouseId: string;
  /** `null` creates. */
  floor: WarehouseFloorOut | null;
  onClose: () => void;
  onSaved: (floor: WarehouseFloorOut) => void;
}

/**
 * Create or edit a warehouse floor (Fulfilment plan §2/F7) — `POST
 * /operations/warehouses/{warehouse_id}/floors` / `PATCH /operations/warehouse-floors/{id}`.
 * `floor_number` is create-only in this dialog (basements and mezzanines are ordinary integers,
 * not necessarily positive or contiguous) — the server allows editing it too, but renumbering a
 * floor with a published layout is not a routine edit, so this dialog does not offer it once one
 * exists to keep the common "rename" case a single field.
 */
export function WarehouseFloorFormDialog({
  warehouseId,
  floor,
  onClose,
  onSaved,
}: WarehouseFloorFormDialogProps) {
  const [floorNumber, setFloorNumber] = useState(floor ? String(floor.floor_number) : "");
  const [name, setName] = useState(floor?.name ?? "");
  const [floorNumberError, setFloorNumberError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const dirty = floorNumber !== (floor ? String(floor.floor_number) : "") || name !== (floor?.name ?? "");

  const save = useMutation(
    (input: WarehouseFloorIn | WarehouseFloorPatch) =>
      floor
        ? updateWarehouseFloor(floor.id, input as WarehouseFloorPatch)
        : createWarehouseFloor(warehouseId, input as WarehouseFloorIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setFloorNumberError(undefined);
    const trimmedName = name.trim();

    if (floor) {
      void save.mutate({ name: trimmedName });
      return;
    }

    const trimmed = floorNumber.trim();
    if (!/^-?\d+$/.test(trimmed)) {
      setFloorNumberError("Enter a whole number.");
      firstFieldRef.current?.focus();
      return;
    }
    void save.mutate({ floor_number: Number(trimmed), name: trimmedName });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={floor ? `Edit floor ${floor.floor_number}` : "New floor"}
      submitLabel={floor ? "Save changes" : "Create floor"}
      pendingLabel={floor ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      {floor ? null : (
        <TextField
          ref={firstFieldRef}
          label="Floor number"
          inputMode="numeric"
          value={floorNumber}
          onChange={(e) => setFloorNumber(e.target.value)}
          required
          helper="Ground is usually 0 or 1; a basement can be negative."
          {...((floorNumberError ?? save.fieldErrors.floor_number) !== undefined
            ? { error: floorNumberError ?? save.fieldErrors.floor_number }
            : {})}
        />
      )}
      <TextField
        ref={floor ? firstFieldRef : undefined}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        optional
        helper="Ground Floor, Mezzanine, Cold Storage — whatever the team calls it."
        {...(save.fieldErrors.name !== undefined ? { error: save.fieldErrors.name } : {})}
      />
    </FormDialog>
  );
}
