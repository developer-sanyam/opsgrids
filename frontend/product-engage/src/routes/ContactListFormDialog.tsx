import { useEffect, useState } from "react";

import { createList, editList } from "@/api/client";
import type { ContactListOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, TextArea, TextField } from "@/ui";

/**
 * Create or rename a contact list.
 *
 * A list's contacts are never part of this form — `member_count` is folded server-side from
 * `contact_list_members`, and adding or removing members is its own screen with its own picker,
 * the same reason `ContactFormDialog` never offers `attributes`.
 */

interface Values {
  name: string;
  description: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { name: "", description: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.name.trim() === "") errors.name = "Every list needs a name to pick it out by.";
  return errors;
}

export function ContactListFormDialog({
  open,
  list,
  onClose,
  onSaved,
}: {
  open: boolean;
  /** `null` to create a list; a list to rename or re-describe it. */
  list: ContactListOut | null;
  onClose: () => void;
  onSaved: (list: ContactListOut, message: string) => void;
}) {
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  const editing = list !== null;

  useEffect(() => {
    if (!open) return;
    setValues(list === null ? EMPTY : { name: list.name, description: list.description });
    setErrors({});
    setFailure(null);
  }, [open, list]);

  function close() {
    setValues(EMPTY);
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
      const name = values.name.trim();
      const description = values.description.trim();
      const saved =
        list === null
          ? await createList({ name, description })
          : await editList(list.id, {
              ...(name === list.name ? {} : { name }),
              ...(description === list.description ? {} : { description }),
            });
      setValues(EMPTY);
      onSaved(saved, `${saved.name} ${list === null ? "created" : "saved"}.`);
    } catch (caught) {
      // Everything typed stays exactly where it is — a refused rename is worth trying again
      // with the same words, not from a blank form.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Dialog
      open={open}
      onClose={close}
      title={editing ? "Rename list" : "Create a list"}
      description={
        editing
          ? "The name and description shown wherever this list is picked."
          : "A named slice of your audience a campaign can be addressed to."
      }
      size="md"
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={submitting}>
            {submitting ? "Saving…" : editing ? "Save changes" : "Create list"}
          </Button>
        </>
      }
    >
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert tone="danger" title="That list wasn't saved" className="mb-4">
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
        className="flex flex-col gap-6"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <TextField
          label="Name"
          value={values.name}
          onChange={(event) => set("name", event.target.value)}
          placeholder="Enterprise accounts"
          disabled={submitting}
          {...(errors.name !== undefined ? { error: errors.name } : {})}
        />
        <TextArea
          label="Description"
          value={values.description}
          onChange={(event) => set("description", event.target.value)}
          placeholder="What this list is for, and who is on it."
          rows={3}
          optional
          disabled={submitting}
        />
      </form>
    </Dialog>
  );
}
