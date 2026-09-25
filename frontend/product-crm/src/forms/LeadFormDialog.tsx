import { useRef, useState } from "react";

import { createLead } from "@/api/client";
import type { Lead, LeadSource, Owner } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField, useFormDirty } from "@opsgrids/ui";

interface LeadFormDialogProps {
  owners: Owner[];
  onClose: () => void;
  onSaved: (lead: Lead) => void;
}

const SOURCE_OPTIONS: { value: LeadSource; label: string }[] = [
  { value: "manual", label: "Manual" },
  { value: "form", label: "Web form" },
  { value: "import", label: "Import" },
];

/**
 * Capture a lead — `POST /leads`. `owner_id` left on "Auto-assign" omits the field entirely,
 * so the service assigns from the organization's round-robin the same way an inbound web-form
 * submission does; naming a rep here bypasses the rotation on purpose, the same "explicit wins"
 * rule `services.create_lead` documents.
 */
export function LeadFormDialog({ owners, onClose, onSaved }: LeadFormDialogProps) {
  const [fullName, setFullName] = useState("");
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [phone, setPhone] = useState("");
  const [source, setSource] = useState<LeadSource>("manual");
  const [ownerId, setOwnerId] = useState("");
  const [notes, setNotes] = useState("");

  const fields = { fullName, company, email, phone, source, ownerId, notes };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [fullNameError, setFullNameError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    () =>
      createLead({
        full_name: fullName.trim(),
        company: company.trim() ? company.trim() : null,
        email: email.trim() ? email.trim() : null,
        phone: phone.trim() ? phone.trim() : null,
        source,
        ...(ownerId ? { owner_id: ownerId } : {}),
        notes: notes.trim(),
      }),
    { onSuccess: (lead) => onSaved(lead) },
  );

  function submit() {
    setFullNameError(undefined);
    const trimmedName = fullName.trim();
    if (trimmedName.length < 1) {
      setFullNameError("Enter their full name.");
      nameRef.current?.focus();
      return;
    }
    void save.mutate(undefined);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Capture a lead"
      description="From a web form, an import, or typed in by hand — the intake ahead of the pipeline."
      submitLabel="Add lead"
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
        label="Company"
        value={company}
        onChange={(e) => setCompany(e.target.value)}
        optional
        {...maybe("error", save.fieldErrors.company)}
      />
      <TextField
        label="Email"
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        optional
        helper="Sealed and masked on save — never stored or shown in the clear."
        {...maybe("error", save.fieldErrors.email)}
      />
      <TextField
        label="Phone"
        value={phone}
        onChange={(e) => setPhone(e.target.value)}
        optional
        {...maybe("error", save.fieldErrors.phone)}
      />
      <Select
        label="Source"
        value={source}
        onChange={(e) => setSource(e.target.value as LeadSource)}
        options={SOURCE_OPTIONS}
      />
      <Select
        label="Owner"
        value={ownerId}
        onChange={(e) => setOwnerId(e.target.value)}
        options={[
          { value: "", label: "Auto-assign (round-robin)" },
          ...owners.filter((o) => o.is_active).map((o) => ({ value: o.id, label: o.full_name })),
        ]}
      />
      <TextArea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        optional
        rows={3}
        {...maybe("error", save.fieldErrors.notes)}
      />
    </FormDialog>
  );
}
