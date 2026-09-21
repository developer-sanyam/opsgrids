import { useState } from "react";

import { addContactList } from "@/api/client";
import type { ContactListIn, ContactListOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, TextArea, TextField } from "@/ui";

interface Values {
  name: string;
  description: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { name: "", description: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.name.trim() === "") errors.name = "What this list is for, e.g. “Newsletter”.";
  return errors;
}

function toInput(values: Values): ContactListIn {
  return {
    name: values.name.trim(),
    ...(values.description.trim() === "" ? {} : { description: values.description.trim() }),
  };
}

export function ContactListFormDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (list: ContactListOut, message: string) => void;
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
      const list = await addContactList(toInput(values));
      setValues(EMPTY);
      onSaved(list, `${list.name} created.`);
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
      title="Create a list"
      description="A named group a campaign can target — build it from the contact book, or from an import."
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Creating…" : "Create list"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That list wasn't created" className="mb-4">
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
          label="Name"
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          placeholder="Newsletter"
          disabled={submitting}
          {...(errors.name !== undefined ? { error: errors.name } : {})}
        />
        <TextArea
          label="Description"
          value={values.description}
          onChange={(event) => set("description", event.target.value)}
          optional
          rows={3}
          placeholder="What this list is for, and who belongs on it."
          disabled={submitting}
        />
      </form>
    </Dialog>
  );
}
