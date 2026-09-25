import { useRef, useState } from "react";

import { changeIncidentStatus } from "@/api/client";
import type { Incident, IncidentStatusIn } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextArea, useFormDirty } from "@opsgrids/ui";

interface ResolveIncidentDialogProps {
  incident: Incident;
  onClose: () => void;
  onResolved: (incident: Incident) => void;
}

/** `POST /itsm/incidents/{number}/status` with `status: "resolved"` — the one transition the
 *  server requires a note for, so it becomes the resolution worklog entry. */
export function ResolveIncidentDialog({ incident, onClose, onResolved }: ResolveIncidentDialogProps) {
  const [note, setNote] = useState("");
  const [initial] = useState(note);
  const dirty = useFormDirty(initial, note);
  const [noteError, setNoteError] = useState<string | undefined>(undefined);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  const resolve = useMutation((input: IncidentStatusIn) => changeIncidentStatus(incident.id, input), {
    onSuccess: (updated) => onResolved(updated),
  });

  function submit() {
    setNoteError(undefined);
    const trimmed = note.trim();
    if (trimmed.length < 1) {
      setNoteError("Say how it was resolved.");
      noteRef.current?.focus();
      return;
    }
    void resolve.mutate({ status: "resolved", note: trimmed });
  }

  const hasTopLevelError = resolve.error && Object.keys(resolve.fieldErrors).length === 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Resolve ${incident.id}`}
      submitLabel="Resolve incident"
      pendingLabel="Resolving…"
      pending={resolve.pending}
      initialFocusRef={noteRef}
      dirty={dirty}
      error={hasTopLevelError ? problemMessage(resolve.error) : undefined}
      reference={hasTopLevelError ? (resolve.error as ApiError).problem?.request_id : undefined}
    >
      <TextArea
        ref={noteRef}
        label="Resolution"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={8000}
        rows={4}
        helper="Required — this becomes the resolution note on the activity thread."
        required
        error={noteError ?? resolve.fieldErrors.note}
      />
    </FormDialog>
  );
}
