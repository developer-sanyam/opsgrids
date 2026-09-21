import { useRef, useState } from "react";

import { createPerson, updatePerson } from "@/api/client";
import type { Person } from "@/api/types";
import { withOptional } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextField } from "@opsgrids/ui";

const REF_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

interface FormErrors {
  ref?: string;
  name?: string;
  capacity_hours?: string;
}

function validate(ref: string, name: string, capacity: string, editing: boolean): FormErrors {
  const errors: FormErrors = {};
  if (!editing && !REF_PATTERN.test(ref.trim())) {
    errors.ref = "Use up to 40 letters, numbers, hyphens or underscores.";
  }
  if (name.trim().length === 0) {
    errors.name = "Give this person a name.";
  } else if (name.trim().length > 200) {
    errors.name = "Keep it to 200 characters or fewer.";
  }
  const capacityNumber = Number(capacity);
  if (capacity.trim() === "" || !Number.isFinite(capacityNumber) || capacityNumber < 0 || capacityNumber > 168) {
    errors.capacity_hours = "Enter hours per week between 0 and 168.";
  }
  return errors;
}

/**
 * Add or edit a roster person. `ref` is the stable handle the rest of the product joins on
 * (timesheets, standups, task assignment); it is set once at create and never editable after.
 */
export function PersonFormDialog({
  person,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  person: Person | null;
  onClose: () => void;
  onSaved: (person: Person, message: string) => void;
}) {
  const editing = person !== null;
  const [ref, setRef] = useState(person?.id ?? "");
  const [name, setName] = useState(person?.name ?? "");
  const [role, setRole] = useState(person?.role ?? "");
  const [location, setLocation] = useState(person?.location ?? "");
  const [capacity, setCapacity] = useState(String(person?.capacity_hours ?? 40));
  const [errors, setErrors] = useState<FormErrors>({});
  const refRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      editing
        ? updatePerson(person.id, {
            name: name.trim(),
            role: role.trim(),
            location: location.trim(),
            capacity_hours: Number(capacity),
          })
        : createPerson({
            ref: ref.trim(),
            name: name.trim(),
            role: role.trim(),
            location: location.trim(),
            capacity_hours: Number(capacity),
          }),
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, editing ? `${saved.name} saved.` : `${saved.name} added to the roster.`);
      },
    },
  );

  function submit() {
    const found = validate(ref, name, capacity, editing);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      if (found.ref) refRef.current?.focus();
      else if (found.name) nameRef.current?.focus();
      return;
    }
    void save.mutate(undefined);
  }

  const nonFieldError =
    save.error && Object.keys(save.fieldErrors).length === 0 ? problemMessage(save.error) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit ${person.name}` : "Add a person"}
      submitLabel={editing ? "Save changes" : "Add to roster"}
      pendingLabel={editing ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={editing ? nameRef : refRef}
      {...withOptional("error", nonFieldError)}
      {...withOptional("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      {!editing ? (
        <TextField
          ref={refRef}
          label="Reference"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="p13"
          helper="The stable handle timesheets, standups and task assignment join on. Cannot be changed later."
          {...withOptional("error", errors.ref ?? save.fieldErrors.ref)}
        />
      ) : null}
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        {...withOptional("error", errors.name ?? save.fieldErrors.name)}
      />
      <TextField
        label="Role"
        value={role}
        onChange={(e) => setRole(e.target.value)}
        optional
        {...withOptional("error", save.fieldErrors.role)}
      />
      <TextField
        label="Location"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        optional
        {...withOptional("error", save.fieldErrors.location)}
      />
      <TextField
        label="Capacity (hours per week)"
        inputMode="numeric"
        value={capacity}
        onChange={(e) => setCapacity(e.target.value)}
        helper="The billable-target hours for a standard week."
        {...withOptional("error", errors.capacity_hours ?? save.fieldErrors.capacity_hours)}
      />
    </FormDialog>
  );
}
