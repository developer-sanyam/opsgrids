import { useState } from "react";

import { useUnsavedChangesWarning } from "@opsgrids/ui";

import { addSuppression } from "@/api/client";
import type { SuppressionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, ConfirmDialog, Dialog, InlineAlert, TextField } from "@/ui";

/**
 * Suppress an address by hand.
 *
 * There is no reason field here because there is nothing to choose: `SuppressionIn` carries only
 * `email`, and the service stamps every row this route writes as `reason: "manual"` — the other
 * three reasons are reports from a recipient or their mailbox provider, and offering them here
 * would let this screen forge one.
 */

const EMPTY = "";

function validate(email: string): string | null {
  if (email.trim() === "") return "The address this suppression applies to.";
  return null;
}

export function SuppressionFormDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (suppression: SuppressionOut, message: string) => void;
}) {
  const [email, setEmail] = useState(EMPTY);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmingClose, setConfirmingClose] = useState(false);

  const dirty = email !== EMPTY;
  useUnsavedChangesWarning(open && dirty);

  function close() {
    setEmail(EMPTY);
    setError(null);
    setFailure(null);
    setConfirmingClose(false);
    onClose();
  }

  // Cancel, Escape and the close control ask first when there's something to lose; a successful
  // save closes through `onSaved` → the caller's own `open`, which never asks.
  function requestClose() {
    if (submitting) return;
    if (dirty) {
      setConfirmingClose(true);
      return;
    }
    close();
  }

  async function submit() {
    if (submitting) return;
    const found = validate(email);
    setError(found);
    if (found !== null) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const saved = await addSuppression({ email: email.trim() });
      setEmail(EMPTY);
      onSaved(saved, `${saved.email} suppressed. It cannot be mailed until this is lifted.`);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={requestClose}
      title="Suppress an address"
      description="Nothing Engage sends will go to this address until the suppression is lifted."
      size="sm"
      footer={
        <>
          <Button variant="ghost" onClick={requestClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Suppressing…" : "Suppress"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That address wasn't suppressed" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
      </div>

      <form
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          label="Email"
          type="email"
          value={email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (error !== null) setError(null);
          }}
          placeholder="priya@vaayu-logistics.example.com"
          autoComplete="off"
          spellCheck={false}
          required
          disabled={submitting}
          {...(error !== null ? { error } : {})}
        />
      </form>

      <ConfirmDialog
        open={open && confirmingClose}
        onClose={() => setConfirmingClose(false)}
        onConfirm={() => {
          setConfirmingClose(false);
          close();
        }}
        title="Discard unsaved changes?"
        body="What you've entered in “Suppress an address” hasn't been saved and will be lost."
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
      />
    </Dialog>
  );
}
