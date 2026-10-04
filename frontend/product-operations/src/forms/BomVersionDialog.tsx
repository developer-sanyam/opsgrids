import { useRef, useState } from "react";

import { createBomVersion } from "@/api/client";
import type { BomDetailOut, BomVersionDetailOut, BomVersionIn } from "@/api/types";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextArea, TextField } from "@/ui";

interface BomVersionDialogProps {
  bom: BomDetailOut;
  onClose: () => void;
  onCreated: (version: BomVersionDetailOut) => void;
}

const START_EMPTY = "";

/**
 * `POST /operations/manufacturing/boms/{id}/versions` — `operations.manufacturing.bom.write`. A new
 * draft, optionally copying an earlier version's components and substitutes. A BOM holds at most
 * one draft: the server's 409 when one exists is shown as the dialog's own message, verbatim.
 */
export function BomVersionDialog({ bom, onClose, onCreated }: BomVersionDialogProps) {
  const [copyFrom, setCopyFrom] = useState(START_EMPTY);
  const [outputQuantity, setOutputQuantity] = useState("1");
  const [notes, setNotes] = useState("");
  const [qtyError, setQtyError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLSelectElement>(null);
  const keyFor = useIdempotencyKeys();

  const save = useMutation(
    (input: BomVersionIn) => createBomVersion(bom.id, input, { idempotencyKey: keyFor(input) }),
    { onSuccess: (saved) => onCreated(saved) },
  );

  function chooseSource(id: string) {
    setCopyFrom(id);
    const source = bom.versions.find((v) => v.id === id);
    if (source) setOutputQuantity(source.output_quantity);
  }

  function submit() {
    const error = qtyFieldError(outputQuantity);
    setQtyError(error);
    if (error) return;
    void save.mutate({
      output_quantity: outputQuantity.trim(),
      notes: notes.trim(),
      ...(copyFrom !== START_EMPTY ? { copy_from_version_id: copyFrom } : {}),
    });
  }

  const sourceOptions = [
    { value: START_EMPTY, label: "Start empty" },
    ...bom.versions.map((v) => ({ value: v.id, label: `Version ${v.version_no} (${v.component_count} components)` })),
  ];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New version"
      description={`A new draft of ${bom.code}. Nothing changes for production until it is published.`}
      submitLabel="Create draft"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={copyFrom !== START_EMPTY || outputQuantity !== "1" || notes !== ""}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <Select
        ref={firstFieldRef}
        label="Copy from"
        value={copyFrom}
        onChange={(e) => chooseSource(e.target.value)}
        options={sourceOptions}
        helper="Copies the components and substitutes into the new draft."
        error={save.fieldErrors.copy_from_version_id}
      />
      <TextField
        label="Batch size"
        inputMode="decimal"
        value={outputQuantity}
        onChange={(e) => setOutputQuantity(e.target.value)}
        helper={`In ${bom.uom} of ${bom.sku}.`}
        required
        error={qtyError ?? save.fieldErrors.output_quantity}
      />
      <TextArea
        label="Notes"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={save.fieldErrors.notes}
      />
    </FormDialog>
  );
}
