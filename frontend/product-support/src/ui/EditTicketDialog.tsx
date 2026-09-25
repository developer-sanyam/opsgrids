import { useRef, useState } from "react";

import { updateTicket } from "@/api/client";
import type { Priority, Ticket, TicketUpdateIn } from "@/api/types";
import { problemMessage, useMutation } from "@opsgrids/client";
import type { ApiError } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";
import { errorProp } from "@/lib/optionalProp";

const PRIORITY_OPTIONS: { value: Priority; label: string }[] = [
  { value: "urgent", label: "Urgent" },
  { value: "high", label: "High" },
  { value: "normal", label: "Normal" },
  { value: "low", label: "Low" },
];

interface EditTicketDialogProps {
  ticket: Ticket;
  onClose: () => void;
  onSaved: (ticket: Ticket) => void;
}

/** `PATCH /support/tickets/{id}` — priority, subject and tags. Status and assignee live on their
 *  own controls in Properties, each with its own audit action. */
export function EditTicketDialog({ ticket, onClose, onSaved }: EditTicketDialogProps) {
  const [subject, setSubject] = useState(ticket.subject);
  const [priority, setPriority] = useState<Priority>(ticket.priority);
  const [tags, setTags] = useState(ticket.tags.join(", "));

  const fields = { subject, priority, tags };
  const [initial] = useState(fields);
  const dirty = useFormDirty(initial, fields);

  const [subjectError, setSubjectError] = useState<string | undefined>(undefined);
  const subjectRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: TicketUpdateIn) => updateTicket(ticket.id, input), {
    onSuccess: (updated) => {
      onClose();
      onSaved(updated);
    },
  });

  function submit() {
    setSubjectError(undefined);
    const trimmedSubject = subject.trim();
    if (trimmedSubject.length < 1 || trimmedSubject.length > 300) {
      setSubjectError("Give the ticket a subject, up to 300 characters.");
      subjectRef.current?.focus();
      return;
    }
    void save.mutate({
      subject: trimmedSubject,
      priority,
      tags: tags
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean),
    });
  }

  const topLevelError =
    save.error && Object.keys(save.fieldErrors).length === 0
      ? problemMessage(save.error)
      : undefined;
  const requestId =
    topLevelError !== undefined ? (save.error as ApiError).problem?.request_id : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit ${ticket.id}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={subjectRef}
      dirty={dirty}
      {...errorProp(topLevelError)}
      {...(requestId !== undefined ? { reference: requestId } : {})}
    >
      <TextField
        ref={subjectRef}
        label="Subject"
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        required
        {...errorProp(subjectError ?? save.fieldErrors.subject)}
        maxLength={300}
      />
      <Select
        label="Priority"
        value={priority}
        onChange={(e) => setPriority(e.target.value as Priority)}
        options={PRIORITY_OPTIONS}
        {...errorProp(save.fieldErrors.priority)}
      />
      <TextField
        label="Tags"
        optional
        helper="Comma-separated, e.g. billing, urgent."
        value={tags}
        onChange={(e) => setTags(e.target.value)}
        {...errorProp(save.fieldErrors.tags)}
      />
    </FormDialog>
  );
}
