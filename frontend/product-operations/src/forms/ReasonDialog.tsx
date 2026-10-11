import { useRef, useState } from "react";

import { formDialogError } from "@/lib/formError";
import { FormDialog, TextArea } from "@/ui";

interface ReasonDialogProps {
  title: string;
  description: string;
  /** The field's label: "Reason", "Why is it being rejected?". */
  label: string;
  submitLabel: string;
  pendingLabel: string;
  /** The sentence shown when a required reason is left empty. Omit for an optional reason. */
  requiredMessage?: string;
  pending: boolean;
  error: Error | null;
  /** The server's message for the reason field, if it had one. */
  fieldError?: string | undefined;
  onSubmit: (reason: string) => void;
  onClose: () => void;
}

/**
 * One free-text reason, asked for before an action that is hard to take back: cancelling or
 * rejecting a transfer, withdrawing an adjustment. What is typed survives a failed attempt — the
 * dialog stays open with the server's sentence above the buttons.
 */
export function ReasonDialog({
  title,
  description,
  label,
  submitLabel,
  pendingLabel,
  requiredMessage,
  pending,
  error,
  fieldError,
  onSubmit,
  onClose,
}: ReasonDialogProps) {
  const [reason, setReason] = useState("");
  const [missing, setMissing] = useState<string | undefined>(undefined);
  const reasonRef = useRef<HTMLTextAreaElement>(null);

  function submit() {
    const trimmed = reason.trim();
    if (requiredMessage !== undefined && trimmed === "") {
      setMissing(requiredMessage);
      reasonRef.current?.focus();
      return;
    }
    setMissing(undefined);
    onSubmit(trimmed);
  }

  const shown = missing ?? fieldError;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={title}
      description={description}
      submitLabel={submitLabel}
      pendingLabel={pendingLabel}
      pending={pending}
      initialFocusRef={reasonRef}
      dirty={reason !== ""}
      {...formDialogError(error, fieldError === undefined ? {} : { reason: fieldError })}
    >
      <TextArea
        ref={reasonRef}
        label={label}
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        maxLength={500}
        counterFrom={400}
        required={requiredMessage !== undefined}
        optional={requiredMessage === undefined}
        {...(shown !== undefined ? { error: shown } : {})}
      />
    </FormDialog>
  );
}
