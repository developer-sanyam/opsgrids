import { useRef, useState } from "react";

import { createSupplier, updateSupplier } from "@/api/client";
import type { Category, SupplierIn, SupplierOut, SupplierPatch, SupplierStatus } from "@/api/types";
import { parseInt0 } from "@/lib/money";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextField } from "@/ui";

const CATEGORY_OPTIONS: { value: Category; label: string }[] = [
  { value: "raw_materials", label: "Raw materials" },
  { value: "packaging", label: "Packaging" },
  { value: "components", label: "Components" },
  { value: "mro", label: "MRO" },
  { value: "logistics", label: "Logistics" },
  { value: "services", label: "Services" },
];

const STATUS_OPTIONS: { value: SupplierStatus; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "on_hold", label: "On hold" },
  { value: "pending", label: "Pending" },
];

const CODE_PATTERN = /^[A-Za-z0-9_-]+$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface SupplierFormDialogProps {
  /** `null` creates. */
  supplier: SupplierOut | null;
  onClose: () => void;
  onSaved: (supplier: SupplierOut) => void;
}

/**
 * Create or edit a supplier — `POST /operations/suppliers` / `PATCH /operations/suppliers/{id}`.
 * One component for both; mounted only while its dialog is open, so closing it is the reset.
 * `code` and `currency` are create-only — the server refuses them on a patch.
 */
export function SupplierFormDialog({ supplier, onClose, onSaved }: SupplierFormDialogProps) {
  const [code, setCode] = useState(supplier?.code ?? "");
  const [name, setName] = useState(supplier?.name ?? "");
  const [category, setCategory] = useState<Category | "">(supplier?.category ?? "");
  const [status, setStatus] = useState<SupplierStatus>(supplier?.status ?? "active");
  const [leadTime, setLeadTime] = useState(String(supplier?.lead_time_days ?? 0));
  const [currency, setCurrency] = useState(supplier?.currency ?? "INR");
  const [contactName, setContactName] = useState(supplier?.contact_name ?? "");
  const [contactEmail, setContactEmail] = useState(supplier?.contact_email ?? "");
  const [location, setLocation] = useState(supplier?.location ?? "");

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [categoryError, setCategoryError] = useState<string | undefined>(undefined);
  const [leadTimeError, setLeadTimeError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `supplier` never changes under it.
  const dirty =
    code !== (supplier?.code ?? "") ||
    name !== (supplier?.name ?? "") ||
    category !== (supplier?.category ?? "") ||
    status !== (supplier?.status ?? "active") ||
    leadTime !== String(supplier?.lead_time_days ?? 0) ||
    currency !== (supplier?.currency ?? "INR") ||
    contactName !== (supplier?.contact_name ?? "") ||
    contactEmail !== (supplier?.contact_email ?? "") ||
    location !== (supplier?.location ?? "");

  const save = useMutation(
    (input: SupplierIn | SupplierPatch) =>
      supplier
        ? updateSupplier(supplier.id, input as SupplierPatch)
        : createSupplier(input as SupplierIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);
    setCategoryError(undefined);
    setLeadTimeError(undefined);
    setEmailError(undefined);

    const trimmedCode = code.trim();
    if (!supplier && (trimmedCode.length < 1 || !CODE_PATTERN.test(trimmedCode))) {
      setCodeError("Letters, numbers, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the supplier a name.");
      return;
    }
    if (category === "") {
      setCategoryError("Choose a category.");
      return;
    }
    const lead = parseInt0(leadTime);
    if (!lead.ok) {
      setLeadTimeError(lead.message);
      return;
    }
    const trimmedEmail = contactEmail.trim();
    if (trimmedEmail !== "" && !EMAIL_PATTERN.test(trimmedEmail)) {
      setEmailError("Enter a valid email address, or leave it blank.");
      return;
    }

    if (supplier) {
      const patch: SupplierPatch = {
        name: trimmedName,
        category,
        status,
        lead_time_days: lead.value,
        contact_name: contactName.trim(),
        contact_email: trimmedEmail,
        location: location.trim(),
      };
      void save.mutate(patch);
      return;
    }

    const input: SupplierIn = {
      code: trimmedCode,
      name: trimmedName,
      category,
      status,
      lead_time_days: lead.value,
      currency: currency.trim().toUpperCase() || "INR",
      contact_name: contactName.trim(),
      contact_email: trimmedEmail,
      location: location.trim(),
    };
    void save.mutate(input);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={supplier ? `Edit ${supplier.name}` : "New supplier"}
      submitLabel={supplier ? "Save changes" : "Create supplier"}
      pendingLabel={supplier ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          ref={firstFieldRef}
          label="Code"
          value={code}
          onChange={(e) => setCode(e.target.value)}
          disabled={supplier !== null}
          required
          helper={supplier ? "A supplier's code can't change." : "Letters, numbers, - and _."}
          {...((codeError ?? save.fieldErrors.code) !== undefined ? { error: codeError ?? save.fieldErrors.code } : {})}
        />
        <TextField
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          required
          {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Select
          label="Category"
          value={category}
          onChange={(e) => setCategory(e.target.value as Category)}
          placeholder="Choose a category"
          options={CATEGORY_OPTIONS}
          required
          {...((categoryError ?? save.fieldErrors.category) !== undefined ? { error: categoryError ?? save.fieldErrors.category } : {})}
        />
        <Select
          label="Status"
          value={status}
          onChange={(e) => setStatus(e.target.value as SupplierStatus)}
          options={STATUS_OPTIONS}
          {...((save.fieldErrors.status) !== undefined ? { error: save.fieldErrors.status } : {})}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Lead time (days)"
          inputMode="numeric"
          value={leadTime}
          onChange={(e) => setLeadTime(e.target.value)}
          {...((leadTimeError ?? save.fieldErrors.lead_time_days) !== undefined ? { error: leadTimeError ?? save.fieldErrors.lead_time_days } : {})}
        />
        <TextField
          label="Currency"
          value={currency}
          onChange={(e) => setCurrency(e.target.value.toUpperCase())}
          maxLength={3}
          disabled={supplier !== null}
          helper={supplier ? "A supplier's currency can't change." : "Three-letter ISO code."}
          {...((save.fieldErrors.currency) !== undefined ? { error: save.fieldErrors.currency } : {})}
        />
      </div>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Contact name"
          value={contactName}
          onChange={(e) => setContactName(e.target.value)}
          optional
          {...((save.fieldErrors.contact_name) !== undefined ? { error: save.fieldErrors.contact_name } : {})}
        />
        <TextField
          label="Contact email"
          type="email"
          value={contactEmail}
          onChange={(e) => setContactEmail(e.target.value)}
          optional
          {...((emailError ?? save.fieldErrors.contact_email) !== undefined ? { error: emailError ?? save.fieldErrors.contact_email } : {})}
        />
      </div>

      <TextField
        label="Location"
        value={location}
        onChange={(e) => setLocation(e.target.value)}
        optional
        {...((save.fieldErrors.location) !== undefined ? { error: save.fieldErrors.location } : {})}
      />
    </FormDialog>
  );
}
