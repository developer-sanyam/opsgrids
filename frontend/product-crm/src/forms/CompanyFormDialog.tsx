import { useRef, useState } from "react";

import { createCompany, updateCompany } from "@/api/client";
import type { Company, CompanyStatus, Owner } from "@/api/types";
import type { CompanyIn, CompanyPatch } from "@/api/wire";
import { minorToInput, parseMinor } from "@/lib/money";
import { maybe } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";
import { OwnerPicker } from "./OwnerPicker";

const STATUS_OPTIONS: { value: CompanyStatus; label: string }[] = [
  { value: "prospect", label: "Prospect" },
  { value: "customer", label: "Customer" },
  { value: "lead", label: "Lead" },
  { value: "churned", label: "Churned" },
];

const SIZE_BANDS = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1000+"];

interface CompanyFormDialogProps {
  /** `null` creates. */
  company: Company | null;
  owners: Owner[];
  onOwnerCreated: (owner: Owner) => void;
  onClose: () => void;
  onSaved: (company: Company) => void;
}

/**
 * Create or edit a company — `POST /customers` / `PATCH /customers/{id}`. One component for
 * both: mounted only while its dialog is open, so closing it is the reset.
 */
export function CompanyFormDialog({
  company,
  owners,
  onOwnerCreated,
  onClose,
  onSaved,
}: CompanyFormDialogProps) {
  const [name, setName] = useState(company?.name ?? "");
  const [ownerId, setOwnerId] = useState(company?.owner_id ?? "");
  const [domain, setDomain] = useState(company?.domain ?? "");
  const [industry, setIndustry] = useState(company?.industry ?? "");
  const [sizeBand, setSizeBand] = useState(company?.size_band ?? "");
  const [city, setCity] = useState(company?.city ?? "");
  const [country, setCountry] = useState(company?.country ?? "");
  const [status, setStatus] = useState<CompanyStatus>(company?.status ?? "prospect");
  const [revenue, setRevenue] = useState(
    company ? minorToInput(company.annual_revenue_minor) : "",
  );
  const [currency, setCurrency] = useState(company?.currency ?? "INR");

  const fields = { name, ownerId, domain, industry, sizeBand, city, country, status, revenue, currency };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [ownerError, setOwnerError] = useState<string | undefined>(undefined);
  const [revenueError, setRevenueError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: CompanyIn | CompanyPatch) =>
      company ? updateCompany(company.id, input as CompanyPatch) : createCompany(input as CompanyIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setNameError(undefined);
    setOwnerError(undefined);
    setRevenueError(undefined);

    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the company a name.");
      nameRef.current?.focus();
      return;
    }
    if (ownerId === "") {
      setOwnerError("Choose an owner.");
      return;
    }
    const parsed = parseMinor(revenue);
    if (!parsed.ok) {
      setRevenueError(parsed.message);
      return;
    }
    const currencyCode = currency.trim().toUpperCase() || "INR";

    void save.mutate({
      name: trimmedName,
      owner_id: ownerId,
      domain: domain.trim(),
      industry: industry.trim(),
      size_band: sizeBand,
      city: city.trim(),
      country: country.trim(),
      status,
      annual_revenue_minor: parsed.minor,
      currency: currencyCode,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={company ? `Edit ${company.name}` : "New company"}
      submitLabel={company ? "Save changes" : "Create company"}
      pendingLabel={company ? "Saving…" : "Creating…"}
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
        label="Company name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...maybe("error", nameError ?? save.fieldErrors.name)}
      />

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

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Domain"
          value={domain}
          onChange={(e) => setDomain(e.target.value)}
          optional
          placeholder="acme.com"
          {...maybe("error", save.fieldErrors.domain)}
        />
        <TextField
          label="Industry"
          value={industry}
          onChange={(e) => setIndustry(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.industry)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <Select
          label="Headcount"
          value={sizeBand}
          onChange={(e) => setSizeBand(e.target.value)}
          placeholder="Choose a band"
          options={SIZE_BANDS.map((band) => ({ value: band, label: band }))}
          {...maybe("error", save.fieldErrors.size_band)}
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as CompanyStatus)}
          options={STATUS_OPTIONS}
          {...maybe("error", save.fieldErrors.status)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="City"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.city)}
        />
        <TextField
          label="Country"
          value={country}
          onChange={(e) => setCountry(e.target.value)}
          optional
          {...maybe("error", save.fieldErrors.country)}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Annual revenue"
          inputMode="decimal"
          value={revenue}
          onChange={(e) => setRevenue(e.target.value)}
          optional
          helper="Major units, e.g. 84000000 for ₹8.4 crore."
          {...maybe("error", revenueError ?? save.fieldErrors.annual_revenue_minor)}
        />
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          helper="Three-letter ISO code."
          {...maybe("error", save.fieldErrors.currency)}
        />
      </div>
    </FormDialog>
  );
}
