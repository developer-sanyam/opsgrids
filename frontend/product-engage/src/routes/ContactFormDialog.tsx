import { useState } from "react";

import { addContact } from "@/api/client";
import type { ContactIn, ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, TextField } from "@/ui";

/**
 * Add one person to the audience.
 *
 * A duplicate address is refused by the service with a sentence naming the address, rather than
 * checked here first — whether a contact already exists is a fact about rows, and the writer
 * already answers it precisely. Importing many contacts at once, where a duplicate is the normal
 * case rather than a mistake, is `ContactImportDialog` — a different screen for a different
 * promise (idempotent on email, not refused).
 */

interface Values {
  email: string;
  name: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { email: "", name: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.email.trim() === "") errors.email = "The address this contact receives mail at.";
  return errors;
}

function toInput(values: Values): ContactIn {
  return {
    email: values.email.trim(),
    ...(values.name.trim() === "" ? {} : { name: values.name.trim() }),
  };
}

export function ContactFormDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (contact: ContactOut, message: string) => void;
}) {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function close() {
    setErrors({});
    setFailure(null);
    onClose();
  }

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const contact = await addContact(toInput(values));
      setValues(EMPTY);
      onSaved(contact, `${contact.email} added to the contact book.`);
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Add a contact"
      description="One person in the audience. Their email is their identity here — editing it later means removing this contact and adding a new one."
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Adding…" : "Add contact"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That contact wasn't added" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below" className="mb-4">
            Nothing has been sent yet.
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          label="Email"
          type="email"
          value={values.email}
          onChange={(event) => set("email", event.target.value)}
          placeholder="jane@customer.example.com"
          autoComplete="off"
          spellCheck={false}
          disabled={submitting}
          {...(errors.email !== undefined ? { error: errors.email } : {})}
        />
        <TextField
          label="Name"
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          optional
          placeholder="Jane Doe"
          disabled={submitting}
        />
      </form>
    </Dialog>
  );
}
