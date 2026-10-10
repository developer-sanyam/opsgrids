import { useState } from "react";

import { disposeInspection, rejectInspection, releaseInspection, stepInspection } from "@/api/client";
import type { InspectionStep } from "@/api/client";
import type {
  DispositionAction,
  InspectionOut,
  RejectBucket,
} from "@/api/types";
import { problemMessage, useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { isPositiveDecimal, useIdempotencyKey } from "@/lib/quality";
import { qtyToMilli } from "@/lib/quantity";
import { ConfirmDialog, FormDialog, RadioGroup, Select, TextArea, TextField } from "@/ui";

interface DialogProps {
  inspection: InspectionOut;
  onClose: () => void;
  onDone: (inspection: InspectionOut) => void;
}

const BUCKET_OPTIONS: { value: RejectBucket; label: string }[] = [
  { value: "REJECTED", label: "Rejected" },
  { value: "QUARANTINE", label: "Quarantine" },
];

/* ------------------------------ hold / rework / reinspect ------------------------------ */

const STEP_COPY: Record<
  InspectionStep,
  { title: string; body: string; confirm: string; pending: string }
> = {
  hold: {
    title: "Keep this on hold?",
    body: "The stock stays in Quality hold while it gets a second look. You can still record readings and decide later.",
    confirm: "Put on hold",
    pending: "Saving…",
  },
  rework: {
    title: "Send for rework?",
    body: "The stock stays in Quality hold while it is corrected. Re-inspect it when it is ready.",
    confirm: "Send for rework",
    pending: "Saving…",
  },
  reinspect: {
    title: "Re-inspect this stock?",
    body: "Back to pending, with the earlier readings cleared — they described the stock before rework.",
    confirm: "Re-inspect",
    pending: "Saving…",
  },
};

export function StepDialog({ step, inspection, onClose, onDone }: DialogProps & { step: InspectionStep }) {
  const [note, setNote] = useState("");
  const key = useIdempotencyKey(`${step}:${note}`);
  const run = useMutation(() => stepInspection(inspection.id, step, note.trim(), key), {
    onSuccess: onDone,
  });
  const copy = STEP_COPY[step];
  return (
    <ConfirmDialog
      open
      onClose={() => {
        run.reset();
        onClose();
      }}
      onConfirm={() => void run.mutate(undefined)}
      title={copy.title}
      body={copy.body}
      confirmLabel={copy.confirm}
      pendingLabel={copy.pending}
      tone="primary"
      pending={run.pending}
      {...(run.error ? { error: problemMessage(run.error) } : {})}
    >
      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} optional />
    </ConfirmDialog>
  );
}

/* ------------------------------------- release ------------------------------------- */

/**
 * `POST …/release` — `operations.quality.inspection.approve:ORG` (dangerous). The quantity is
 * typed as text and compared as exact milli-units; releasing everything sends no `accepted` at
 * all, anything less is a partial pass whose remainder goes to the bucket chosen here.
 */
export function ReleaseDialog({ inspection, onClose, onDone }: DialogProps) {
  const [accepted, setAccepted] = useState(inspection.quantity);
  const [bucket, setBucket] = useState<RejectBucket>("REJECTED");
  const [note, setNote] = useState("");
  const [localError, setLocalError] = useState<string | undefined>(undefined);

  const valid = isPositiveDecimal(accepted);
  const all = valid && qtyToMilli(accepted.trim()) === qtyToMilli(inspection.quantity);
  const over = valid && qtyToMilli(accepted.trim()) > qtyToMilli(inspection.quantity);
  const partial = valid && !all && !over;
  const anyFailed = inspection.results.some((r) => !r.passed);

  const payload = {
    ...(partial ? { accepted: accepted.trim(), rejected_bucket: bucket } : {}),
    ...(note.trim() ? { note: note.trim() } : {}),
  };
  const key = useIdempotencyKey(JSON.stringify(payload));
  const run = useMutation(() => releaseInspection(inspection.id, payload, key), { onSuccess: onDone });

  function submit() {
    setLocalError(undefined);
    if (!valid) return setLocalError("Enter a quantity above zero, with at most three decimals.");
    if (over) return setLocalError(`Only ${inspection.quantity} ${inspection.uom} was inspected.`);
    if (anyFailed && note.trim() === "") {
      return setLocalError("A reading failed — say why this stock is being released anyway.");
    }
    void run.mutate(undefined);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Release stock"
      description={`Moves ${all ? "all of it" : "the accepted part"} from Quality hold to Available. This changes recorded stock.`}
      submitLabel={partial ? "Release part" : "Release all"}
      pendingLabel="Releasing…"
      pending={run.pending}
      {...formDialogError(run.error, run.fieldErrors)}
    >
      <TextField
        label={`Quantity to release (${inspection.uom}) of ${inspection.quantity}`}
        value={accepted}
        onChange={(e) => setAccepted(e.target.value)}
        inputMode="decimal"
        required
      />
      {partial ? (
        <Select
          label="Send the remainder to"
          value={bucket}
          onChange={(e) => setBucket(e.target.value as RejectBucket)}
          options={BUCKET_OPTIONS}
          helper="The part not released is rejected, and can then be returned to the vendor or scrapped."
        />
      ) : null}
      <TextArea
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        maxLength={500}
        {...(anyFailed ? { required: true } : { optional: true })}
      />
      {localError ? (
        <p role="alert" className="text-sm text-danger">
          {localError}
        </p>
      ) : null}
    </FormDialog>
  );
}

/* -------------------------------------- reject -------------------------------------- */

export function RejectDialog({ inspection, onClose, onDone }: DialogProps) {
  const [reason, setReason] = useState("");
  const [bucket, setBucket] = useState<RejectBucket>("REJECTED");
  const [localError, setLocalError] = useState<string | undefined>(undefined);
  const key = useIdempotencyKey(JSON.stringify({ reason, bucket }));
  const run = useMutation(
    () => rejectInspection(inspection.id, { reason: reason.trim(), bucket }, key),
    { onSuccess: onDone },
  );

  function submit() {
    setLocalError(undefined);
    if (reason.trim() === "") return setLocalError("Say why this stock is being rejected.");
    void run.mutate(undefined);
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Reject stock"
      description={`Moves all ${inspection.quantity} ${inspection.uom} from Quality hold to ${bucket === "REJECTED" ? "Rejected" : "Quarantine"}. Return it to the vendor or scrap it afterwards.`}
      submitLabel="Reject stock"
      pendingLabel="Rejecting…"
      pending={run.pending}
      {...formDialogError(run.error, run.fieldErrors)}
    >
      <TextArea
        label="Reason"
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        maxLength={500}
        required
        {...(localError ? { error: localError } : {})}
      />
      <Select
        label="Hold the rejected stock in"
        value={bucket}
        onChange={(e) => setBucket(e.target.value as RejectBucket)}
        options={BUCKET_OPTIONS}
      />
    </FormDialog>
  );
}

/* ------------------------------------ disposition ------------------------------------ */

const ACTION_OPTIONS: { value: DispositionAction; label: string; description: string }[] = [
  {
    value: "RETURN_TO_VENDOR",
    label: "Return to vendor",
    description: "The stock leaves your books as an issue back to the supplier.",
  },
  { value: "SCRAP", label: "Scrap", description: "The stock is written off as an adjustment." },
];

export function DispositionDialog({ inspection, onClose, onDone }: DialogProps) {
  const [action, setAction] = useState<DispositionAction>("RETURN_TO_VENDOR");
  const [reference, setReference] = useState("");
  const [note, setNote] = useState("");
  const payload = { action, reference: reference.trim(), note: note.trim() };
  const key = useIdempotencyKey(JSON.stringify(payload));
  const run = useMutation(() => disposeInspection(inspection.id, payload, key), { onSuccess: onDone });

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void run.mutate(undefined)}
      title="Dispose of rejected stock"
      description={`Takes ${inspection.rejected ?? "0"} ${inspection.uom} out of ${inspection.rejected_bucket === "QUARANTINE" ? "Quarantine" : "Rejected"}. This is done once and cannot be undone.`}
      submitLabel={action === "SCRAP" ? "Scrap stock" : "Return to vendor"}
      pendingLabel="Saving…"
      pending={run.pending}
      {...formDialogError(run.error, run.fieldErrors)}
    >
      <RadioGroup
        legend="What happens to it"
        name="disposition-action"
        value={action}
        onChange={(v) => setAction(v as DispositionAction)}
        options={ACTION_OPTIONS}
      />
      <TextField
        label="Reference"
        value={reference}
        onChange={(e) => setReference(e.target.value)}
        maxLength={80}
        placeholder="Debit note, RMA or gate-pass number"
        optional
      />
      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} optional />
    </FormDialog>
  );
}
