import { useRef, useState } from "react";

import { useCurrentUser } from "@opsgrids/client";
import { FormDialog, TextField } from "@opsgrids/ui";

import type { CheckoutCustomer } from "@/api/payment";
import { normalisePhone } from "@/lib/checkout/phone";

/**
 * Asked for only when the server answers `phone_required` — the provider needs a contact number
 * for this payment. The signed-in person's own name and email travel with it, and the dialog says
 * so. Nothing typed here is stored by this page.
 *
 * It stays up, busy, while the number is tried, so a number the provider turns down comes back
 * with the number still in the field and the server's reason under it.
 */
export function PhoneDialog({
  open,
  label,
  pending,
  serverError,
  onSubmit,
  onClose,
}: {
  open: boolean;
  /** "Invoice INV-0042". */
  label: string;
  /** The number is being tried: the dialog stays up and cannot be submitted or dismissed. */
  pending: boolean;
  serverError?: string | undefined;
  onSubmit: (customer: CheckoutCustomer) => void;
  onClose: () => void;
}) {
  const user = useCurrentUser();
  const fieldRef = useRef<HTMLInputElement>(null);
  const [phone, setPhone] = useState("");
  const [error, setError] = useState<string | undefined>(undefined);
  // The server's reason is about the number it was given; once that number is edited, it is not.
  // A new answer (it clears while the number is tried) shows again.
  const [editedSinceServer, setEditedSinceServer] = useState(false);
  const [answered, setAnswered] = useState(serverError);
  if (answered !== serverError) {
    setAnswered(serverError);
    setEditedSinceServer(false);
  }

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
      pendingLabel="Opening payment…"
      pending={pending}
      initialFocusRef={fieldRef}
      size="sm"
    >
      <TextField
        ref={fieldRef}
        label="Mobile number"
        type="tel"
        inputMode="tel"
        autoComplete="tel"
        required
        value={phone}
        onChange={(e) => {
          setPhone(e.target.value);
          setError(undefined);
          setEditedSinceServer(true);
        }}
        error={error ?? (editedSinceServer ? undefined : serverError)}
      />
    </FormDialog>
  );
}
