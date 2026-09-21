import { useRef, useState } from "react";

import { createCustomer, updateCustomer } from "@/api/client";
import type { Customer, CustomerCreate, CustomerUpdate } from "@/api/types";
import { INDIAN_STATE_OPTIONS } from "@/data/gstStates";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, InlineAlert, Select, Switch, TextField } from "@/ui";

const GSTIN_PATTERN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;
const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

interface CustomerFormDialogProps {
  /** `null` creates. */
  customer: Customer | null;
  onClose: () => void;
  onSaved: (customer: Customer) => void;
}

/**
 * Create or edit a customer — `POST /finance/customers` / `PATCH /finance/customers/{id}`. One
 * component for both; mounted only while its dialog is open, so closing it is the reset.
 *
 * The three withholding flags change what a **future** invoice assesses; a document already
 * finalized keeps what it assessed at issue time, which is why the form says so rather than
 * implying an edit here touches anything already issued.
 */
export function CustomerFormDialog({ customer, onClose, onSaved }: CustomerFormDialogProps) {
  const [name, setName] = useState(customer?.name ?? "");
  const [contactName, setContactName] = useState(customer?.contact_name ?? "");
  const [email, setEmail] = useState(customer?.email ?? "");
  const [gstin, setGstin] = useState(customer?.gstin ?? "");
  const [city, setCity] = useState(customer?.city ?? "");
  const [state, setState] = useState(customer?.state ?? "");
  const [isTdsDeductor, setIsTdsDeductor] = useState(customer?.is_tds_deductor ?? false);
  const [deducts194q, setDeducts194q] = useState(customer?.deducts_tds_194q ?? false);
  const [tcsApplicable, setTcsApplicable] = useState(customer?.tcs_applicable ?? false);

  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const [emailError, setEmailError] = useState<string | undefined>(undefined);
  const [gstinError, setGstinError] = useState<string | undefined>(undefined);
  const [mutualError, setMutualError] = useState<string | undefined>(undefined);
  const nameRef = useRef<HTMLInputElement>(null);

  const save = useMutation(
    (input: CustomerCreate | CustomerUpdate) =>
      customer ? updateCustomer(customer.id, input) : createCustomer(input as CustomerCreate),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setNameError(undefined);
    setEmailError(undefined);
    setGstinError(undefined);
    setMutualError(undefined);

    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the customer a name.");
      nameRef.current?.focus();
      return;
    }
    const trimmedEmail = email.trim();
    if (trimmedEmail !== "" && !EMAIL_PATTERN.test(trimmedEmail)) {
      setEmailError("Enter a valid email address, or leave it blank.");
      return;
    }
    const trimmedGstin = gstin.trim().toUpperCase();
    if (trimmedGstin !== "" && !GSTIN_PATTERN.test(trimmedGstin)) {
      setGstinError("Enter a valid 15-character GSTIN, or leave it blank for an unregistered buyer.");
      return;
    }
    if (deducts194q && tcsApplicable) {
      setMutualError("A buyer is either liable under s.194Q or has TCS collected under s.206C(1H) — never both.");
      return;
    }

    const payload = {
      name: trimmedName,
      contact_name: contactName.trim(),
      email: trimmedEmail,
      gstin: trimmedGstin === "" ? null : trimmedGstin,
      city: city.trim(),
      state: state.trim(),
      is_tds_deductor: isTdsDeductor,
      deducts_tds_194q: deducts194q,
      tcs_applicable: tcsApplicable,
    };
    void save.mutate(payload);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={customer ? `Edit ${customer.name}` : "Add customer"}
      submitLabel={customer ? "Save changes" : "Create customer"}
      pendingLabel={customer ? "Saving…" : "Creating…"}
      pending={save.pending}
      initialFocusRef={nameRef}
      size="lg"
      {...(save.error && Object.keys(save.fieldErrors).length === 0
        ? {
            error: problemMessage(save.error),
            ...(save.error instanceof ApiError && save.error.problem?.request_id
              ? { reference: save.error.problem.request_id }
              : {}),
          }
        : {})}
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          ref={nameRef}
          label="Name"
          value={name}
          onChange={(e) => setName(e.target.value)}
          error={nameError ?? save.fieldErrors.name}
        />
        <TextField
          label="Contact name"
          value={contactName}
          onChange={(e) => setContactName(e.target.value)}
          optional
          error={save.fieldErrors.contact_name}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          optional
          error={emailError ?? save.fieldErrors.email}
        />
        <TextField
          label="GSTIN"
          value={gstin}
          onChange={(e) => setGstin(e.target.value.toUpperCase())}
          maxLength={15}
          optional
          helper="Leave blank for an unregistered (B2C) buyer."
          error={gstinError ?? save.fieldErrors.gstin}
        />
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="City"
          value={city}
          onChange={(e) => setCity(e.target.value)}
          optional
          error={save.fieldErrors.city}
        />
        <Select
          label="State"
          value={state}
          onChange={(e) => setState(e.target.value)}
          placeholder="Choose a state"
          options={[...INDIAN_STATE_OPTIONS]}
          error={save.fieldErrors.state}
        />
      </div>

      <div className="flex flex-col gap-3 rounded-tile bg-ground p-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-faint">
          Withholding — applies to future invoices only
        </p>
        <Switch
          checked={isTdsDeductor}
          onChange={setIsTdsDeductor}
          label="Notified under GST s.51 (TDS deductor)"
          description="This buyer deducts 2% GST TDS on supplies above the threshold."
        />
        <Switch
          checked={deducts194q}
          onChange={(v) => {
            setDeducts194q(v);
            if (v) setTcsApplicable(false);
          }}
          label="Deducts income tax under s.194Q"
        />
        <Switch
          checked={tcsApplicable}
          onChange={(v) => {
            setTcsApplicable(v);
            if (v) setDeducts194q(false);
          }}
          label="TCS collected under s.206C(1H)"
        />
        {mutualError ? <InlineAlert tone="danger" title={mutualError} /> : null}
      </div>
    </FormDialog>
  );
}
