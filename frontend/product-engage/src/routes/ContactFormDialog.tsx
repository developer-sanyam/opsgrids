import { useEffect, useState } from "react";

import { useUnsavedChangesWarning } from "@opsgrids/ui";

import { addContact, editContact } from "@/api/client";
import type { ContactOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, ConfirmDialog, Dialog, InlineAlert, TextField } from "@/ui";

/**
 * Add or edit one contact.
 *
 * `attributes` is not a field on this form: it is free-form JSON the service stores verbatim, and
 * this screen has no editor for arbitrary key/value data yet — the import path is what carries it
 * today. Editing an existing contact never touches it: `editContact` only ever sends `email` and
 * `name`, and the service's `ContactPatch` leaves an absent field alone.
 *
 * **`values.email` is only ever what was typed here.** The dialog never seeds itself from
 * `contact.email` on the edit path, because that field is the mask (`p•••@domain.com`) and
 * submitting it back would try to set the address to its own mask — a contact should keep its
 * real address until someone types a new one on purpose.
 *
 * **`values.name` does seed from `contact.name` on the edit path**, because the name the service
 * returns is the real value, not a mask. `name` is sent on submit only when it differs from the
 * seeded value — the service treats a present `name` as "set it", including to empty, so sending
 * it unconditionally would blank the name on every edit that only touched the address.
 */

interface Values {
  email: string;
  name: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { email: "", name: "" };

function validate(values: Values, requireEmail: boolean): Errors {
  const errors: Errors = {};
  if (requireEmail && values.email.trim() === "") {
    errors.email = "The address this contact is reached at.";
  }
  return errors;
}

export function ContactFormDialog({
  open,
  contact,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** `null` to add a contact; a contact to edit it. */
  contact: ContactOut | null;
  onClose: () => void;
  onSaved: (contact: ContactOut, message: string) => void;
}) {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);
  const [confirmingClose, setConfirmingClose] = useState(false);

  const editing = contact !== null;
  // Email always seeds blank (it's the mask, never the real address) so anything typed there is
  // new; name seeds from the contact's own real value.
  const dirty = values.email !== "" || values.name !== (contact === null ? "" : contact.name);
  useUnsavedChangesWarning(open && dirty);

  // The dialog instance for a row is reused across opens (`Contacts` swaps `contact`, not the
  // component), so seeding has to happen here rather than in `useState`'s initializer. Email
  // always starts blank — see the note above — but name starts as the contact's real value so an
  // edit that never touches the name doesn't look, from the form's perspective, like a request to
  // clear it.
  useEffect(() => {
    if (!open) return;
    setValues({ email: "", name: contact === null ? "" : contact.name });
    setErrors({});
    setFailure(null);
    setConfirmingClose(false);
  }, [open, contact]);

  function close() {
    setValues(EMPTY);
    setErrors({});
    setFailure(null);
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
    const found = validate(values, !editing);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const email = values.email.trim();
      const name = values.name.trim();
      const saved =
        contact === null
          ? await addContact({ email, ...(name === "" ? {} : { name }) })
          : await editContact(contact.id, {
              ...(email === "" ? {} : { email }),
              ...(name === contact.name ? {} : { name }),
            });
      const verb = contact === null ? "added" : "saved";
      setValues(EMPTY);
      onSaved(saved, `${saved.name === "" ? saved.email : saved.name} ${verb}.`);
    } catch (caught) {
      // Everything typed stays exactly where it is — a rejected address is usually one typed
      // nearly right, and retyping it from scratch costs the customer the work twice.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={requestClose}
      title={editing ? "Edit contact" : "Add a contact"}
      description={
        editing
          ? "Addresses are sealed as soon as they're saved, so this always starts blank — type a new one only if it changed."
          : "A person Engage may reach. The address is sealed at rest and shown masked everywhere after this."
      }
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={requestClose} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : editing ? "Save changes" : "Add contact"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That contact wasn't saved" className="mb-4">
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
        className="flex flex-col gap-5"
        noValidate
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
          placeholder="priya@vaayu-logistics.example.com"
          autoComplete="off"
          spellCheck={false}
          {...(editing ? { optional: true } : { required: true })}
          helper={editing ? "Leave blank to keep the address on file." : "Sealed as soon as it's saved."}
          disabled={submitting}
          {...(errors.email !== undefined ? { error: errors.email } : {})}
        />
        <TextField
          label="Name"
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          placeholder="Priya Raghavan"
          optional
          disabled={submitting}
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
        body={`What you've entered in “${editing ? "Edit contact" : "Add a contact"}” hasn't been saved and will be lost.`}
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
      />
    </Dialog>
  );
}
