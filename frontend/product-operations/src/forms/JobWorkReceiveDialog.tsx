import { useState } from "react";

import { receiveJobWorkOrder } from "@/api/client";
import type { JobWorkOrderDetailOut, JobWorkReceiveIn } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { outstandingAfter, validateReceipt } from "@/lib/jobwork";
import type { ReceiptDraftLine } from "@/lib/jobwork";
import { formatQty } from "@/lib/quantity";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { FormDialog, TextArea, TextField } from "@/ui";

interface JobWorkReceiveDialogProps {
  order: JobWorkOrderDetailOut;
  onClose: () => void;
  onReceived: (order: JobWorkOrderDetailOut) => void;
}

const BLANK: ReceiptDraftLine = { received: "", rejected: "", shortage: "" };

/**
 * `POST …:receive` — `operations.jobwork.order.receive:ORG`. Takes material back from the vendor,
 * in whole or in part: per material, how much came back good, how much came back unusable, and
 * how much the vendor reports lost. The sum may not exceed what is still at the vendor — checked
 * here for a helpful message (`lib/jobwork.ts`), and again by the server under a lock.
 *
 * Quantities stay strings; "left at vendor" is computed in milli-units, never in floats.
 */
export function JobWorkReceiveDialog({ order, onClose, onReceived }: JobWorkReceiveDialogProps) {
  const open = order.materials.filter((m) => m.outstanding !== "0");
  const [draft, setDraft] = useState<Record<string, ReceiptDraftLine>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [receivedOn, setReceivedOn] = useState("");
  const [note, setNote] = useState("");
  const keyFor = useSubmissionKeys();

  const dirty = receivedOn !== "" || note !== "" || Object.values(draft).some((r) => r.received || r.rejected || r.shortage);

  const receive = useMutation(
    (input: JobWorkReceiveIn) => receiveJobWorkOrder(order.id, input, keyFor(input)),
    { onSuccess: (updated) => onReceived(updated) },
  );

  function patch(materialId: string, field: keyof ReceiptDraftLine, value: string) {
    setDraft((current) => ({
      ...current,
      [materialId]: { ...(current[materialId] ?? BLANK), [field]: value },
    }));
    setErrors((current) => {
      if (!(materialId in current)) return current;
      const { [materialId]: _gone, ...rest } = current;
      void _gone;
      return rest;
    });
  }

  function submit() {
    setFormError(undefined);
    const result = validateReceipt(open, draft, () => undefined);
    setErrors(result.errors);
    if (result.formError) {
      setFormError(result.formError);
      return;
    }
    if (Object.keys(result.errors).length > 0) return;
    void receive.mutate({
      lines: result.lines,
      ...(receivedOn ? { received_on: receivedOn } : {}),
      ...(note.trim() ? { note: note.trim() } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Receive material on ${order.number}`}
      description={
        order.qc_required
          ? "Good quantities go to Quality hold and a QC request is raised. Rejected goods go to Rejected."
          : "Good quantities go to Available. Rejected goods go to Rejected. Shortage is written off the vendor's stock."
      }
      submitLabel="Record receipt"
      pendingLabel="Recording…"
      pending={receive.pending}
      dirty={dirty}
      size="lg"
      {...formDialogError(receive.error, receive.fieldErrors)}
    >
      <div className="flex flex-col gap-4">
        {open.map((m) => {
          const row = draft[m.id] ?? BLANK;
          const left = outstandingAfter(m, row);
          const over = left.startsWith("-");
          return (
            <fieldset key={m.id} className="flex flex-col gap-3 rounded-tile border border-line p-3">
              <legend className="px-1 text-sm font-semibold text-ink">
                {m.sku}{" "}
                <span className="font-normal text-muted">
                  · {formatQty(m.outstanding)} {m.uom} still at the vendor
                </span>
              </legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <TextField
                  label={`Received good (${m.uom})`}
                  inputMode="decimal"
                  value={row.received}
                  onChange={(e) => patch(m.id, "received", e.target.value)}
                />
                <TextField
                  label={`Rejected (${m.uom})`}
                  inputMode="decimal"
                  value={row.rejected}
                  onChange={(e) => patch(m.id, "rejected", e.target.value)}
                />
                <TextField
                  label={`Shortage (${m.uom})`}
                  inputMode="decimal"
                  value={row.shortage}
                  onChange={(e) => patch(m.id, "shortage", e.target.value)}
                />
              </div>
              <p className={over ? "text-sm font-medium text-danger" : "text-sm text-muted"}>
                Left at vendor after this receipt:{" "}
                <span className="tnum">
                  {formatQty(left)} {m.uom}
                </span>
              </p>
              {errors[m.id] ? (
                <p role="alert" className="text-sm text-danger">
                  {errors[m.id]}
                </p>
              ) : null}
            </fieldset>
          );
        })}
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <TextField
          label="Received on"
          type="date"
          value={receivedOn}
          onChange={(e) => setReceivedOn(e.target.value)}
        />
      </div>
      <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} />
      {formError ? (
        <p role="alert" className="text-sm text-danger">
          {formError}
        </p>
      ) : null}
    </FormDialog>
  );
}
