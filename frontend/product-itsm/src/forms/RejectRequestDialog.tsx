import { useRef, useState } from "react";

import { decideRequest } from "@/api/client";
import type { RequestDecisionIn, ServiceRequest } from "@/api/types";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, TextArea } from "@opsgrids/ui";

interface RejectRequestDialogProps {
  request: ServiceRequest;
  onClose: () => void;
  onDecided: (request: ServiceRequest) => void;
}

/** `POST /itsm/service-requests/{number}/decision` with `decision: "reject"` — the server
 *  requires a note here, which becomes `decision_note`. */
export function RejectRequestDialog({ request, onClose, onDecided }: RejectRequestDialogProps) {
  const [note, setNote] = useState("");
  const [noteError, setNoteError] = useState<string | undefined>(undefined);
  const noteRef = useRef<HTMLTextAreaElement>(null);

  const decide = useMutation((input: RequestDecisionIn) => decideRequest(request.id, input), {
    onSuccess: (updated) => onDecided(updated),
  });

  function submit() {
    setNoteError(undefined);
    const trimmed = note.trim();
    if (trimmed.length < 1) {
      setNoteError("Say why this is being turned down.");
      noteRef.current?.focus();
      return;
    }
    void decide.mutate({ decision: "reject", note: trimmed });
  }

  const hasTopLevelError = decide.error && Object.keys(decide.fieldErrors).length === 0;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Reject ${request.id}?`}
      submitLabel="Reject request"
      pendingLabel="Rejecting…"
      pending={decide.pending}
      initialFocusRef={noteRef}
      error={hasTopLevelError ? problemMessage(decide.error) : undefined}
      reference={hasTopLevelError ? (decide.error as ApiError).problem?.request_id : undefined}
    >
      <TextArea
        ref={noteRef}
        label="Reason"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={2000}
        rows={4}
        helper="Required — shown to the requester as the decision note."
        error={noteError ?? decide.fieldErrors.note}
      />
    </FormDialog>
  );
}
