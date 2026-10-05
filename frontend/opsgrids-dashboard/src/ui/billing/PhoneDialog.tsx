import { useState } from "react";

import { useCurrentUser } from "@opsgrids/client";
import { FormDialog, TextField } from "@opsgrids/ui";

import type { CheckoutCustomer } from "@/api/payment";
import { normalisePhone } from "@/lib/checkout/phone";

/**
 * Asked for only when the server answers `phone_required` — the provider needs a contact number
 * for this payment. The signed-in person's own name and email travel with it, and the dialog says
 * so. Nothing typed here is stored by this page.
 */
export function PhoneDialog({
  open,
  label,
  serverError,
  onSubmit,
  onClose,
}: {
  open: boolean;
  /** "Invoice INV-0042". */
  label: string;
  serverError?: string | undefined;
  onSubmit: (customer: CheckoutCustomer) => void;
  onClose: () => void;
}) {
  const user = useCurrentUser();
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);

  function submit() {
    const normalised = normalisePhone(phone);
    if (!normalised) {
      setError("Enter a 10-digit mobile number, like 98200 11122.");
      return;
    }
    setError(undefined);
    onSubmit({ name: user?.name ?? "", email: user?.email ?? "", phone: normalised });
  }

  return (
    <FormDialog
      open={open}
      onClose={onClose}
      onSubmit={submit}
      title="Add a mobile number"
      description={`The payment provider needs a mobile number to take payment for ${label}. Your name and email are sent with it, for this payment only.`}
      submitLabel="Continue to payment"
      size="sm"
    >
      <TextField
        label="Mobile number"
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        required
        value={phone}
        onChange={(e) => {
          setPhone(e.target.value);
          setError(undefined);
        }}
        error={error ?? serverError}
      />
    </FormDialog>
  );
}
