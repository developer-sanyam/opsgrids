import { useRef, useState } from "react";

import { updateBomVersion } from "@/api/client";
import type { BomVersionDetailOut, BomVersionPatch } from "@/api/types";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useMutation } from "@opsgrids/client";
import { FormDialog, TextArea, TextField } from "@/ui";

interface BomVersionEditDialogProps {
  version: BomVersionDetailOut;
  /** The produced item's unit of measure, for the batch-size helper. */
  outputUnit: string;
  onClose: () => void;
  onSaved: (version: BomVersionDetailOut) => void;
}

/**
 * `PATCH /operations/manufacturing/boms/{id}/versions/{versionId}` — `operations.manufacturing.bom.write`.
 * A draft's batch size, effective dates and notes. A published or retired version is immutable and
 * is never offered this dialog; the server refuses the call regardless.
 */
export function BomVersionEditDialog({ version, outputUnit, onClose, onSaved }: BomVersionEditDialogProps) {
  const [outputQuantity, setOutputQuantity] = useState(version.output_quantity);
  const [from, setFrom] = useState(version.effective_from ?? "");
  const [to, setTo] = useState(version.effective_to ?? "");
  const [notes, setNotes] = useState(version.notes);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const dirty =
    outputQuantity !== version.output_quantity ||
    from !== (version.effective_from ?? "") ||
    to !== (version.effective_to ?? "") ||
    notes !== version.notes;

  const save = useMutation(
    (patch: BomVersionPatch) => updateBomVersion(version.bom_id, version.id, patch),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    const next: Record<string, string> = {};
    const qtyError = qtyFieldError(outputQuantity);
    if (qtyError) next.output_quantity = qtyError;
    if (from !== "" && to !== "" && to < from) next.effective_to = "The end date can't be before the start date.";
    setErrors(next);
    if (Object.keys(next).length > 0) return;
    void save.mutate({
      output_quantity: outputQuantity.trim(),
      effective_from: from === "" ? null : from,
      effective_to: to === "" ? null : to,
      notes: notes.trim(),
    });
  }

  const fieldError = (field: string) => errors[field] ?? save.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Edit version ${version.version_no}`}
      submitLabel="Save changes"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Batch size"
        inputMode="decimal"
        value={outputQuantity}
        onChange={(e) => setOutputQuantity(e.target.value)}
        helper={`Component quantities are stated per this much ${outputUnit}.`}
        required
        error={fieldError("output_quantity")}
      />
      <TextField
        label="Effective from"
        type="date"
        value={from}
        onChange={(e) => setFrom(e.target.value)}
        optional
        error={fieldError("effective_from")}
      />
      <TextField
        label="Effective to"
        type="date"
        value={to}
        onChange={(e) => setTo(e.target.value)}
        optional
        error={fieldError("effective_to")}
      />
      <TextArea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={fieldError("notes")}
      />
    </FormDialog>
  );
}
