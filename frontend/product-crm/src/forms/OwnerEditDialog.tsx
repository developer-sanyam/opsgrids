import { useRef, useState } from "react";

import { updateOwner } from "@/api/client";
import type { Owner } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextField, useFormDirty } from "@opsgrids/ui";

interface OwnerEditDialogProps {
  owner: Owner;
  onClose: () => void;
  onSaved: (owner: Owner) => void;
}

/** Edit a rep's plain fields — `PATCH /customers/owners/{id}`. Deactivating is its own
 *  confirm-first action on the Team screen, not a checkbox in this form. */
export function OwnerEditDialog({ owner, onClose, onSaved }: OwnerEditDialogProps) {
  const [fullName, setFullName] = useState(owner.full_name);
  const [email, setEmail] = useState(owner.email);
  const [role, setRole] = useState(owner.role);

  const fields = { fullName, email, role };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [fullNameError, setFullNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      updateOwner(owner.id, {
        full_name: fullName.trim(),
        email: email.trim(),
        role: role.trim(),
      }),
    { onSuccess: (updated) => onSaved(updated) },
  );

  function submit() {
    setFullNameError(undefined);
    setEmailError(undefined);
    const trimmedName = fullName.trim();
    const trimmedEmail = email.trim();
    if (trimmedName.length < 1) {
      setFullNameError("Enter their full name.");
      nameRef.current?.focus();
      return;
    }
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      setEmailError("Enter a valid email address.");
      return;
    }
    void save.mutate(undefined);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit ${owner.full_name}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={nameRef}
      dirty={dirty}
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...maybe("reference", (save.error as ApiError).problem?.request_id),
          }
        : {})}
    >
      <TextField
        ref={nameRef}
        label="Full name"
        value={fullName}
        onChange={(e) => setFullName(e.target.value)}
        required
        {...maybe("error", fullNameError ?? save.fieldErrors.full_name)}
      />
      <TextField
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        required
        {...maybe("error", emailError ?? save.fieldErrors.email)}
      />
      <TextField
        label="Role"
        value={role}
        onChange={(e) => setRole(e.target.value)}
        optional
        {...maybe("error", save.fieldErrors.role)}
      />
    </FormDialog>
  );
}
