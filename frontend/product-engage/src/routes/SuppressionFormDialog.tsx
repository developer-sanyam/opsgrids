import { useCallback, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { addSuppression, listContacts } from "@/api/client";
import type { SuppressionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import { Button, Dialog, InlineAlert, Select, TextArea } from "@/ui";
import type { SelectOption } from "@/ui";

/**
 * Manually suppress a contact.
 *
 * The reason is always `manual` — the other three (`unsubscribed`, `bounced`, `complained`) are
 * recorded by the public unsubscribe link and by provider feedback, and a form here posting one
 * of those would be an operator claiming to be an event that did not happen. The note is the
 * operator's own record of why, since `manual` alone says nothing.
 */
export function SuppressionFormDialog({
  open,
  onClose,
  onSaved,
}: {
  open: boolean;
  onClose: () => void;
  onSaved: (suppression: SuppressionOut, message: string) => void;
}) {
  const loader = useCallback(() => listContacts(), []);
  const contactsState = useAsync(loader, [open]);
  const contacts = contactsState.status === "success" ? contactsState.data.items : [];

  const [contactId, setContactId] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function close() {
    setContactId("");
    setNote("");
    setError(null);
    setFailure(null);
    onClose();
  }

  async function submit() {
    if (submitting) return;
    if (contactId === "") {
      setError("Which contact should stop receiving campaigns?");
      return;
    }
    setError(null);
    setSubmitting(true);
    setFailure(null);
    try {
      const suppression = await addSuppression({
        contact_id: contactId,
        reason: "manual",
        ...(note.trim() === "" ? {} : { note: note.trim() }),
      });
      const contact = contacts.find((c) => c.id === contactId);
      onSaved(suppression, `${contact?.email ?? "Contact"} suppressed.`);
      setContactId("");
      setNote("");
    } catch (caught) {
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  const options: SelectOption[] = contacts.map((c) => ({
    value: c.id,
    label: c.name === "" ? c.email : `${c.name} — ${c.email}`,
  }));

  return (
    <Dialog
      open={open}
      onClose={close}
      title="Suppress a contact"
      description="Stops this contact from being sent any Engage campaign. Recorded as a manual suppression, alongside self-service unsubscribes and provider bounces."
      footer={
        <>
          <Button variant="ghost" onClick={close} disabled={submitting}>
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
          <InlineAlert tone="danger" title="That contact wasn't suppressed" className="mb-4">
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && error !== null ? (
          <InlineAlert tone="warning" title="Check the field marked below" className="mb-4">
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
        <Select
          label="Contact"
          value={contactId}
          onChange={(event) => {
            setContactId(event.target.value);
            setError(null);
          }}
          options={options}
          placeholder={
            contactsState.status === "loading" ? "Loading contacts…" : "Choose a contact"
          }
          disabled={submitting || contactsState.status !== "success"}
          {...(error !== null ? { error } : {})}
        />
        <TextArea
          label="Note"
          value={note}
          onChange={(event) => setNote(event.target.value)}
          optional
          rows={3}
          placeholder="Why this contact is being suppressed."
          disabled={submitting}
        />
      </form>
    </Dialog>
  );
}
