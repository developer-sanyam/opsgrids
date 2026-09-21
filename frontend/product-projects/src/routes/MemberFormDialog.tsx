import { useRef, useState } from "react";

import { createMember, updateMember } from "@/api/client";
import type { MemberOut } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { maybe } from "@/lib/optional";
import { FormDialog, TextField } from "@opsgrids/ui";

const REF_PATTERN = /^[A-Za-z0-9_-]{1,40}$/;

interface FormErrors {
  ref?: string;
  name?: string;
}

/**
 * Add or edit a delivery-org member. `ref` is the stable handle every owner/assignee picker in
 * the product submits (`owner_ref` / `assignee_ref`); it is set once at create and never
 * editable after.
 */
export function MemberFormDialog({
  member,
  onClose,
  onSaved,
}: {
  /** `null` creates. */
  member: MemberOut | null;
  onClose: () => void;
  onSaved: (member: MemberOut, message: string) => void;
}) {
  const editing = member !== null;
  const [ref, setRef] = useState(member?.id ?? "");
  const [name, setName] = useState(member?.name ?? "");
  const [role, setRole] = useState(member?.role ?? "");
  const [errors, setErrors] = useState<FormErrors>({});
  const refRef = useRef<HTMLInputElement>(null);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      editing
        ? updateMember(member.id, { name: name.trim(), role: role.trim() })
        : createMember({ ref: ref.trim(), name: name.trim(), role: role.trim() }),
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, editing ? `${saved.name} saved.` : `${saved.name} added to the team.`);
      },
    },
  );

  function submit() {
    const found: FormErrors = {};
    if (!editing && !REF_PATTERN.test(ref.trim())) {
      found.ref = "Use up to 40 letters, numbers, hyphens or underscores.";
    }
    if (name.trim().length === 0) found.name = "Give this person a name.";
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
      title={editing ? `Edit ${member.name}` : "Add a team member"}
      submitLabel={editing ? "Save changes" : "Add member"}
      pendingLabel={editing ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={editing ? nameRef : refRef}
      {...maybe("error", nonFieldError)}
      {...maybe("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      {!editing ? (
        <TextField
          ref={refRef}
          label="Reference"
          value={ref}
          onChange={(e) => setRef(e.target.value)}
          placeholder="p-11"
          helper="The stable handle every owner and assignee picker joins on. Cannot be changed later."
          {...maybe("error", errors.ref ?? save.fieldErrors.ref)}
        />
      ) : (
        <TextField label="Reference" value={member.id} disabled helper="References can't be changed." />
      )}
      <TextField
        ref={nameRef}
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        {...maybe("error", errors.name ?? save.fieldErrors.name)}
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
