import { useRef, useState } from "react";

import { createOwner } from "@/api/client";
import type { Owner } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextField, useFormDirty } from "@opsgrids/ui";

interface OwnerFormDialogProps {
  onClose: () => void;
  onSaved: (owner: Owner) => void;
}

/**
 * Add a rep to the directory — `POST /customers/owners`. The prerequisite write: a production
 * org starts with zero owners, and every company / contact / deal create needs one. There is no
 * edit or deactivate — the backend doesn't offer it, so this dialog only ever creates.
 */
export function OwnerFormDialog({ onClose, onSaved }: OwnerFormDialogProps) {
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [role, setRole] = useState("");

  const fields = { fullName, email, role };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [fullNameError, setFullNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      createOwner({
        full_name: fullName.trim(),
        email: email.trim(),
        ...(role.trim() ? { role: role.trim() } : {}),
      }),
    { onSuccess: (owner) => onSaved(owner) },
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
      title="Add a team member"
      description="Every company, contact and deal needs an owner — this is the rep directory they're chosen from."
      submitLabel="Add team member"
      pendingLabel="Adding…"
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
        helper="e.g. Account Executive, Sales Development Rep."
        {...maybe("error", save.fieldErrors.role)}
      />
    </FormDialog>
  );
}
