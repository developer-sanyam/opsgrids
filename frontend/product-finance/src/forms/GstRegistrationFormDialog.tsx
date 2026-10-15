import { useRef, useState } from "react";

import { setGstRegistration } from "@/api/client";
import type { GstRegistration, GstRegistrationType, GstRegistrationUpsert } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { ConfirmDialog, FormDialog, Select, Switch, TextField, useFormDirty } from "@/ui";

const GSTIN_PATTERN = /^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$/;

const TYPE_OPTIONS: { value: GstRegistrationType; label: string }[] = [
  { value: "regular", label: "Regular" },
  { value: "composition", label: "Composition" },
  { value: "casual", label: "Casual" },
];

interface GstRegistrationFormDialogProps {
  registration: GstRegistration | null;
  onClose: () => void;
  onSaved: (registration: GstRegistration) => void;
}

/**
 * Set this organization's GST registration — `PUT /finance/gst/registration`. Dangerous: every
 * future invoice is raised under whichever GSTIN stands here, so a second confirmation names that
 * consequence before the write happens. Issued invoices are unaffected — the GSTIN is a snapshot
 * frozen onto each of them at the time, never a live lookup.
 */
export function GstRegistrationFormDialog({
  registration,
  onClose,
  onSaved,
}: GstRegistrationFormDialogProps) {
  const [gstin, setGstin] = useState(registration?.gstin ?? "");
  const [legalName, setLegalName] = useState(registration?.legal_name ?? "");
  const [tradeName, setTradeName] = useState(registration?.trade_name ?? "");
  const [registrationType, setRegistrationType] = useState<GstRegistrationType>(
    (registration?.registration_type as GstRegistrationType | undefined) ?? "regular",
  );
  const [isPrimary, setIsPrimary] = useState(registration?.is_primary ?? true);
  const [addressLine1, setAddressLine1] = useState(registration?.address_line1 ?? "");
  const [addressLine2, setAddressLine2] = useState(registration?.address_line2 ?? "");
  const [city, setCity] = useState(registration?.city ?? "");
  const [pincode, setPincode] = useState(registration?.pincode ?? "");

  const fields = { gstin, legalName, tradeName, registrationType, isPrimary, addressLine1, addressLine2, city, pincode };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [gstinError, setGstinError] = useState<string | undefined>(undefined);
  const [legalNameError, setLegalNameError] = useState<string | undefined>(undefined);
  const [addressError, setAddressError] = useState<string | undefined>(undefined);
  const [cityError, setCityError] = useState<string | undefined>(undefined);
  const [pincodeError, setPincodeError] = useState<string | undefined>(undefined);
  const [confirming, setConfirming] = useState(false);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: GstRegistrationUpsert) => setGstRegistration(input), {
    onSuccess: (saved) => onSaved(saved),
  });

  function validate(): boolean {
    setGstinError(undefined);
    setLegalNameError(undefined);
    setAddressError(undefined);
    setCityError(undefined);
    setPincodeError(undefined);
    const cleanedGstin = gstin.trim().toUpperCase();
    if (!GSTIN_PATTERN.test(cleanedGstin)) {
      setGstinError("Enter a valid 15-character GSTIN, like 27AABCA9012D1Z5.");
      firstFieldRef.current?.focus();
      return false;
    }
    if (legalName.trim().length < 1) {
      setLegalNameError("Give the legal name on the certificate.");
      return false;
    }
    if (addressLine1.trim() === "") {
      setAddressError("Give the principal place of business — it prints on every tax invoice.");
      return false;
    }
    if (city.trim() === "") {
      setCityError("Give the city.");
      return false;
    }
    if (!/^[1-9]\d{5}$/.test(pincode.trim())) {
      setPincodeError("A PIN code is six numerals and does not start with 0.");
      return false;
    }
    return true;
  }

  function submit() {
    if (!validate()) return;
    setConfirming(true);
  }

  function confirmSave() {
    void save.mutate({
      gstin: gstin.trim().toUpperCase(),
      legal_name: legalName.trim(),
      trade_name: tradeName.trim(),
      registration_type: registrationType,
      is_primary: isPrimary,
      address_line1: addressLine1.trim(),
      address_line2: addressLine2.trim(),
      city: city.trim(),
      pincode: pincode.trim(),
    });
  }

  const derivedStateCode = gstin.trim().slice(0, 2);

  return (
    <>
      <FormDialog
        open={!confirming}
        onClose={onClose}
        onSubmit={submit}
        title={registration ? "Edit GST registration" : "Set up GST registration"}
        description="Without a registration, invoices carry no seller GSTIN, GST returns can't be generated and purchases are refused."
        submitLabel="Continue"
        initialFocusRef={firstFieldRef}
        dirty={dirty}
      >
        <TextField
          ref={firstFieldRef}
          label="GSTIN"
          value={gstin}
          onChange={(e) => setGstin(e.target.value.toUpperCase())}
          maxLength={15}
          helper={derivedStateCode.length === 2 ? `Derived state code ${derivedStateCode}` : "15 characters."}
          required
          error={gstinError}
        />
        <TextField
          label="Legal name"
          value={legalName}
          onChange={(e) => setLegalName(e.target.value)}
          required
          error={legalNameError}
        />
        <TextField
          label="Trade name"
          value={tradeName}
          onChange={(e) => setTradeName(e.target.value)}
          optional
        />
        <TextField
          label="Address"
          value={addressLine1}
          onChange={(e) => setAddressLine1(e.target.value)}
          maxLength={200}
          required
          helper="The principal place of business on the registration certificate."
          error={addressError ?? save.fieldErrors.address_line1}
        />
        <TextField
          label="Address line 2"
          value={addressLine2}
          onChange={(e) => setAddressLine2(e.target.value)}
          maxLength={200}
          optional
        />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="City"
            value={city}
            onChange={(e) => setCity(e.target.value)}
            maxLength={120}
            required
            error={cityError ?? save.fieldErrors.city}
          />
          <TextField
            label="PIN code"
            value={pincode}
            inputMode="numeric"
            onChange={(e) => setPincode(e.target.value.replace(/\D/g, ""))}
            maxLength={6}
            required
            error={pincodeError ?? save.fieldErrors.pincode}
          />
        </div>
        <Select
          label="Registration type"
          value={registrationType}
          onChange={(e) => setRegistrationType(e.target.value as GstRegistrationType)}
          options={TYPE_OPTIONS}
        />
        <Switch
          checked={isPrimary}
          onChange={setIsPrimary}
          label="Primary registration"
          description="Future invoices and GST returns use the primary registration by default."
        />
      </FormDialog>

      <ConfirmDialog
        open={confirming}
        onClose={() => {
          setConfirming(false);
          save.reset();
        }}
        onConfirm={confirmSave}
        title="Set this GST registration?"
        body="Every invoice issued from now on will be raised under this GSTIN. Invoices already issued keep the GSTIN they were raised under — this never rewrites a filed document."
        confirmLabel="Set registration"
        pendingLabel="Saving…"
        pending={save.pending}
        {...(save.error
          ? {
              error:
                save.fieldErrors.gstin ?? problemMessage(save.error),
              ...(save.error instanceof ApiError && save.error.problem?.request_id
                ? { reference: save.error.problem.request_id }
                : {}),
            }
          : {})}
      />
    </>
  );
}
