import { useRef, useState } from "react";

import { createContact, setContactPhone, updateContact } from "@/api/client";
import type { Company, Contact, ContactStatus, Owner } from "@/api/types";
import { maybe } from "@/lib/optional";
import { ApiError, normalisePhone, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, Select, TextField, useFormDirty } from "@opsgrids/ui";
import { OwnerPicker } from "./OwnerPicker";

const STATUS_OPTIONS: { value: ContactStatus; label: string }[] = [
  { value: "lead", label: "Lead" },
  { value: "qualified", label: "Qualified" },
  { value: "customer", label: "Customer" },
  { value: "dormant", label: "Dormant" },
];

interface ContactFormDialogProps {
  /** `null` creates. */
  contact: Contact | null;
  companies: Company[];
  owners: Owner[];
  onOwnerCreated: (owner: Owner) => void;
  /** Prefills the company on create — from the CompanyDetail "New contact" card. */
  defaultCompanyId?: string;
  onClose: () => void;
  onSaved: (contact: Contact) => void;
}

/**
 * Create or edit a contact — `POST /contacts` / `PATCH /contacts/{id}`. The phone number is
 * never part of either body: `ContactOut.phone` is always a mask, so this form never prefills an
 * input from it. Create takes an optional `raw_phone`; edit shows the current mask as read-only
 * text beside an empty "Replace phone number" input that only calls `PUT /contacts/{id}/phone`
 * when the caller actually typed something.
 */
export function ContactFormDialog({
  contact,
  companies,
  owners,
  onOwnerCreated,
  defaultCompanyId,
  onClose,
  onSaved,
}: ContactFormDialogProps) {
  const [fullName, setFullName] = useState(contact?.full_name ?? "");
  const [email, setEmail] = useState(contact?.email ?? "");
  const [companyId, setCompanyId] = useState(contact?.company_id ?? defaultCompanyId ?? "");
  const [ownerId, setOwnerId] = useState(contact?.owner_id ?? "");
  const [title, setTitle] = useState(contact?.title ?? "");
  const [status, setStatus] = useState<ContactStatus>(contact?.status ?? "lead");
  // Never seeded from `contact.phone` — that's a mask, not a value this form may resend.
  const [newPhone, setNewPhone] = useState("");

  const [initial] = useState({ fullName, email, companyId, ownerId, title, status, newPhone });
  const dirty = useFormDirty(initial, { fullName, email, companyId, ownerId, title, status, newPhone });

  const [fullNameError, setFullNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [companyError, setCompanyError] = useState<string | undefined>(undefined);
  const [ownerError, setOwnerError] = useState<string | undefined>(undefined);
  const [phoneError, setPhoneError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  // What the number is sent as: E.164. A bare Indian mobile is completed to +91 for an Indian
  // organization (`normalisePhone`); anything else needs its country code.
  const phoneE164 = newPhone.trim() ? normalisePhone(newPhone) : null;

  const save = useMutation(
    async (): Promise<Contact> => {
      if (contact) {
        let saved = await updateContact(contact.id, {
          full_name: fullName.trim(),
          title: title.trim(),
          // A phone-only contact has no address; the service cannot clear one, so a blank field
          // means "unchanged" (submit() refuses a blank where one was already saved).
          ...(email.trim() ? { email: email.trim() } : {}),
          company_id: companyId,
          owner_id: ownerId,
          status,
        });
        if (newPhone.trim()) {
          saved = await setContactPhone(contact.id, { raw_phone: phoneE164 ?? newPhone.trim() });
        }
        return saved;
      }
      return createContact({
        full_name: fullName.trim(),
        // Optional (ADR-0038 D-14): a person reachable by phone alone is a contact.
        ...(email.trim() ? { email: email.trim() } : {}),
        company_id: companyId,
        owner_id: ownerId,
        title: title.trim(),
        status,
        ...(phoneE164 !== null ? { raw_phone: phoneE164 } : {}),
      });
    },
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setFullNameError(undefined);
    setEmailError(undefined);
    setCompanyError(undefined);
    setOwnerError(undefined);
    setPhoneError(undefined);

    const trimmedName = fullName.trim();
    if (trimmedName.length < 1) {
      setFullNameError("Enter their full name.");
      nameRef.current?.focus();
      return;
    }
    if (email.trim() === "" && contact?.email) {
      setEmailError("An email address can't be removed once saved, only replaced.");
      return;
    }
    if (email.trim() !== "" && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim())) {
      setEmailError("Enter a valid email address, or leave it blank for a contact reached by phone.");
      return;
    }
    if (!contact && email.trim() === "" && newPhone.trim() === "") {
      setEmailError("Add an email or a phone number so you can reach them.");
      return;
    }
    if (companyId === "") {
      setCompanyError("Choose a company.");
      return;
    }
    if (ownerId === "") {
      setOwnerError("Choose an owner.");
      return;
    }
    if (newPhone.trim() && phoneE164 === null) {
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
      title={contact ? `Edit ${contact.full_name}` : "New contact"}
      submitLabel={contact ? "Save changes" : "Create contact"}
      pendingLabel={contact ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      size="lg"
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
        optional
        {...maybe("error", emailError ?? save.fieldErrors.email)}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Company"
          value={companyId}
          onChange={(e) => setCompanyId(e.target.value)}
          placeholder="Choose a company"
          options={companies.map((c) => ({ value: c.id, label: c.name }))}
          required
          {...maybe("error", companyError ?? save.fieldErrors.company_id)}
        />
        <TextField
          label="Title"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.title)}
        />
      </div>

      <OwnerPicker
        owners={owners}
        value={ownerId}
        onChange={setOwnerId}
        onOwnerCreated={(owner) => {
          onOwnerCreated(owner);
          setOwnerId(owner.id);
        }}
        {...maybe("error", ownerError ?? save.fieldErrors.owner_id)}
      />

      <Select
        label="Status"
        value={status}
        onChange={(e) => setStatus(e.target.value as ContactStatus)}
        options={STATUS_OPTIONS}
        {...maybe("error", save.fieldErrors.status)}
      />

      {contact ? (
        <div className="flex flex-col gap-2">
          <InlineAlert tone="info" live="off" title="Current phone number">
            <span className="tnum">{contact.phone}</span> — masked. It can only be replaced, never
            read back here.
          </InlineAlert>
          <TextField
            label="Replace phone number"
            value={newPhone}
            onChange={(e) => setNewPhone(e.target.value)}
            type="tel"
            autoComplete="tel"
            inputMode="tel"
            optional
            placeholder="+91 98765 43210"
            helper="A 10-digit mobile, or with country code. Leave blank to keep the number on file."
            {...maybe("error", phoneError)}
          />
        </div>
      ) : (
        <TextField
          label="Phone"
          value={newPhone}
          onChange={(e) => setNewPhone(e.target.value)}
          type="tel"
          autoComplete="tel"
          inputMode="tel"
          optional
          placeholder="+91 98765 43210"
          helper="A 10-digit mobile, or with country code, e.g. +91 98765 43210. A contact needs an email or a phone."
          {...maybe("error", phoneError)}
        />
      )}
    </FormDialog>
  );
}
