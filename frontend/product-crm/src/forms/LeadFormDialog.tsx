import { useRef, useState } from "react";

import { createLead } from "@/api/client";
import type { Lead, LeadSource, Owner } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, normalisePhone, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField, useFormDirty } from "@opsgrids/ui";

interface LeadFormDialogProps {
  owners: Owner[];
  onClose: () => void;
  onSaved: (lead: Lead) => void;
  /**
   * The signed-in login's rep record, putting them on the roster first when they may
   * (`useMyRep().ensure`). On "Auto-assign" it is sent as `fallback_owner_id`: the lead is theirs
   * when no round-robin picks anyone, as in a one-person organization -- a lead with no owner
   * cannot be converted.
   */
  ensureMyRep?: () => Promise<Owner | undefined>;
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
 * rule `services.create_lead` documents. When no rotation picks anyone, the lead goes to the
 * person capturing it (`ensureMyRep`).
 */
export function LeadFormDialog({ owners, onClose, onSaved, ensureMyRep }: LeadFormDialogProps) {
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
  const [phoneError, setPhoneError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  // Sent as E.164: a bare Indian mobile is completed to +91 for an Indian organization.
  const phoneE164 = phone.trim() ? normalisePhone(phone) : null;

  const save = useMutation(
    async () => {
      const mine = ownerId === "" && ensureMyRep !== undefined ? await ensureMyRep() : undefined;
      return createLead({
        full_name: fullName.trim(),
        company: company.trim() ? company.trim() : null,
        email: email.trim() ? email.trim() : null,
        phone: phoneE164,
        source,
        ...(ownerId ? { owner_id: ownerId } : {}),
        ...(mine !== undefined ? { fallback_owner_id: mine.id } : {}),
        notes: notes.trim(),
      });
    },
    { onSuccess: (lead) => onSaved(lead) },
  );

  function submit() {
    setFullNameError(undefined);
    setPhoneError(undefined);
    const trimmedName = fullName.trim();
    if (trimmedName.length < 1) {
      setFullNameError("Enter their full name.");
      nameRef.current?.focus();
      return;
    }
    if (phone.trim() && phoneE164 === null) {
      setPhoneError("Enter a 10-digit mobile, or the number with its country code, like +91 98765 43210.");
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
        type="tel"
        autoComplete="tel"
        inputMode="tel"
        optional
        placeholder="+91 98765 43210"
        helper="A 10-digit mobile, or with country code."
        {...maybe("error", phoneError ?? save.fieldErrors.phone)}
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
        helper="With no rotation set up, the lead is yours."
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
