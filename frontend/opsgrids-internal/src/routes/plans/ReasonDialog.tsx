import { useRef, useState } from "react";
import type { ReactNode } from "react";

import { FormDialog, InlineAlert, TextArea } from "@opsgrids/ui";
import type { InlineAlertTone } from "@opsgrids/ui";

import { useCatalogWrite } from "./useCatalogWrite";
import { ProblemList } from "./ProblemList";

export interface ReasonDialogProps {
  title: string;
  /** The consequence, before it happens. */
  description: ReactNode;
  submitLabel: string;
  pendingLabel: string;
  /** A standing warning above the reason — publish's "this is permanent". */
  warning?: { tone: InlineAlertTone; title: string; body?: ReactNode | undefined } | undefined;
  /** Extra controls between the warning and the reason (publish's zero-amount acknowledgement). */
  children?: ReactNode | undefined;
  onClose: () => void;
  /** Perform the write with this reason and the dialog's idempotency key. */
  submit: (reason: string, idempotencyKey: string) => Promise<unknown>;
  /** Called once the write succeeded; the screen closes the dialog and announces the result. */
  onDone: () => void;
}

/**
 * Every catalogue write that needs nothing but a justification goes through this: publish,
 * withdraw, delete draft, retire, reactivate. The reason is required (3–500 characters, the
 * server's own bounds) and lands on the audit row. Mount it only while open — each mount is one
 * dialog open and one `Idempotency-Key`.
 */
export function ReasonDialog({
  title,
  description,
  submitLabel,
  pendingLabel,
  warning,
  children,
  onClose,
  submit,
  onDone,
}: ReasonDialogProps) {
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const reasonRef = useRef<HTMLTextAreaElement>(null);
  const write = useCatalogWrite();

  const trimmed = reason.trim();
  const reasonError =
    trimmed.length < 3
      ? "Say why, in at least 3 characters."
      : trimmed.length > 500
        ? "Keep the reason under 500 characters."
        : undefined;

  async function handleSubmit() {
    setTouched(true);
    if (reasonError !== undefined) {
      reasonRef.current?.focus();
      return;
    }
    const done = await write.run(async (key) => {
      await submit(trimmed, key);
      return true;
    });
    if (done) onDone();
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void handleSubmit()}
      title={title}
      description={description}
      submitLabel={submitLabel}
      pendingLabel={pendingLabel}
      pending={write.pending}
      error={write.error}
      reference={write.reference}
      initialFocusRef={reasonRef}
      dirty={reason !== ""}
    >
      {warning ? (
        <InlineAlert tone={warning.tone} title={warning.title} live="off">
          {warning.body}
        </InlineAlert>
      ) : null}
      {children}
      <TextArea
        ref={reasonRef}
        label="Reason"
        required
        rows={3}
        maxLength={500}
        counterFrom={400}
        helper="Kept on the audit record with your name."
        value={reason}
        onChange={(event) => setReason(event.target.value)}
        onBlur={() => setTouched(true)}
        error={touched ? reasonError : undefined}
      />
      <ProblemList problems={write.fieldErrors} />
    </FormDialog>
  );
}
