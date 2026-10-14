import { useMemo, useRef, useState } from "react";

import { createDeal, updateDeal } from "@/api/client";
import type { Company, Contact, Deal, DealStage, Owner } from "@/api/types";
import { STAGES } from "@/data/stages";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, minorToInput, parseMinor, Select, TextField, useFormDirty } from "@opsgrids/ui";
import { OwnerPicker } from "./OwnerPicker";

interface DealFormDialogProps {
  /** `null` creates. */
  deal: Deal | null;
  companies: Company[];
  contacts: Contact[];
  owners: Owner[];
  onOwnerCreated: (owner: Owner) => void;
  /** Prefills the company on create — from the CompanyDetail "New deal" card. */
  defaultCompanyId?: string;
  onClose: () => void;
  onSaved: (deal: Deal) => void;
}

/** The currencies a deal is quoted in; a deal already in another one keeps it as a choice. */
const CURRENCIES = ["INR", "USD", "EUR", "GBP", "AED", "SGD"];

function currencyOptions(current: string): { value: string; label: string }[] {
  const codes = CURRENCIES.includes(current) ? CURRENCIES : [current, ...CURRENCIES];
  return codes.map((code) => ({ value: code, label: code }));
}

/**
 * Create or edit a deal — `POST /deals` / `PATCH /deals/{id}`. `PATCH` carries no `stage`: a
 * stage move only ever goes through `POST /deals/{id}/stage` (the kanban drag, or the stage
 * `Select` on the deal header), so this form never offers one while editing.
 */
export function DealFormDialog({
  deal,
  companies,
  contacts,
  owners,
  onOwnerCreated,
  defaultCompanyId,
  onClose,
  onSaved,
}: DealFormDialogProps) {
  const [name, setName] = useState(deal?.name ?? "");
  const [companyId, setCompanyId] = useState(deal?.company_id ?? defaultCompanyId ?? "");
  const [primaryContactId, setPrimaryContactId] = useState(deal?.primary_contact_id ?? "");
  const [ownerId, setOwnerId] = useState(deal?.owner_id ?? "");
  const [value, setValue] = useState(deal ? minorToInput(deal.value_minor, deal.currency) : "");
  const [currency, setCurrency] = useState(deal?.currency ?? "INR");
  const [closeDate, setCloseDate] = useState(deal?.close_date ?? "");
  const [stage, setStage] = useState<DealStage>(deal?.stage ?? "lead");
  const [source, setSource] = useState(deal?.source ?? "");

  const fields = { name, companyId, primaryContactId, ownerId, value, currency, closeDate, stage, source };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [companyError, setCompanyError] = useState<string | undefined>(undefined);
  const [ownerError, setOwnerError] = useState<string | undefined>(undefined);
  const [valueError, setValueError] = useState<string | undefined>(undefined);
  const [closeDateError, setCloseDateError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const contactOptions = useMemo(
    () => contacts.filter((c) => c.company_id === companyId),
    [contacts, companyId],
  );

  const save = useMutation(
    (input: { value_minor: number }) =>
      deal
        ? updateDeal(deal.id, {
            name: name.trim(),
            company_id: companyId,
            primary_contact_id: primaryContactId === "" ? null : primaryContactId,
            owner_id: ownerId,
            value_minor: input.value_minor,
            currency,
            close_date: closeDate,
            source: source.trim(),
          })
        : createDeal({
            name: name.trim(),
            company_id: companyId,
            owner_id: ownerId,
            value_minor: input.value_minor,
            close_date: closeDate,
            currency,
            stage,
            ...(primaryContactId !== "" ? { primary_contact_id: primaryContactId } : {}),
            source: source.trim(),
          }),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setNameError(undefined);
    setCompanyError(undefined);
    setOwnerError(undefined);
    setValueError(undefined);
    setCloseDateError(undefined);

    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the deal a name.");
      nameRef.current?.focus();
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
    const parsed = parseMinor(value, currency.trim().toUpperCase() || "INR");
    if (!parsed.ok) {
      setValueError(parsed.message);
      return;
    }
    if (!closeDate) {
      setCloseDateError("Choose an expected close date.");
      return;
    }
    void save.mutate({ value_minor: parsed.minor });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={deal ? `Edit ${deal.name}` : "New deal"}
      submitLabel={deal ? "Save changes" : "Create deal"}
      pendingLabel={deal ? "Saving…" : "Creating…"}
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
        label="Deal name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...maybe("error", nameError ?? save.fieldErrors.name)}
      />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Company"
          value={companyId}
          onChange={(e) => {
            setCompanyId(e.target.value);
            setPrimaryContactId("");
          }}
          placeholder="Choose a company"
          options={companies.map((c) => ({ value: c.id, label: c.name }))}
          required
          {...maybe("error", companyError ?? save.fieldErrors.company_id)}
        />
        <Select
          label="Primary contact"
          value={primaryContactId}
          onChange={(e) => setPrimaryContactId(e.target.value)}
          placeholder={
            companyId === "" ? "Choose a company first" : "None"
          }
          disabled={companyId === ""}
          options={contactOptions.map((c) => ({ value: c.id, label: c.full_name }))}
          {...maybe("error", save.fieldErrors.primary_contact_id)}
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

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <TextField
          label="Value"
          inputMode="decimal"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          helper="Major units, e.g. 3200000."
          required
          {...maybe("error", valueError ?? save.fieldErrors.value_minor)}
        />
        <Select
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value)}
          options={currencyOptions(currency)}
          {...maybe("error", save.fieldErrors.currency)}
        />
        <TextField
          label="Close date"
          type="date"
          value={closeDate}
          onChange={(e) => setCloseDate(e.target.value)}
          required
          {...maybe("error", closeDateError ?? save.fieldErrors.close_date)}
        />
      </div>

      {deal === null ? (
        <Select
          label="Stage"
          value={stage}
          onChange={(e) => setStage(e.target.value as DealStage)}
          options={STAGES.map((s) => ({ value: s.stage, label: s.label }))}
          {...maybe("error", save.fieldErrors.stage)}
        />
      ) : null}

      <TextField
        label="Source"
        value={source}
        onChange={(e) => setSource(e.target.value)}
        optional
        placeholder="Referral, Outbound, Inbound…"
        {...maybe("error", save.fieldErrors.source)}
      />
    </FormDialog>
  );
}
