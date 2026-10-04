import { useRef, useState } from "react";

import { createBom } from "@/api/client";
import type { BomDetailOut, BomIn } from "@/api/types";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useIdempotencyKeys } from "@/lib/useIdempotencyKeys";
import { useMutation } from "@opsgrids/client";
import { FormDialog, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface BomFormDialogProps {
  onClose: () => void;
  onCreated: (bom: BomDetailOut) => void;
}

const CODE = /^[A-Za-z0-9_.-]+$/;

/**
 * `POST /operations/manufacturing/boms` — `operations.manufacturing.bom.write`. Creates the BOM and
 * its first draft version in one call; the batch size entered here is the quantity the draft's
 * component quantities are stated for. Client checks mirror the contract (code pattern, lengths,
 * a decimal quantity with at most three decimals); the server's own answer still wins and lands on
 * the field it names.
 */
export function BomFormDialog({ onClose, onCreated }: BomFormDialogProps) {
  const [code, setCode] = useState("");
  const [name, setName] = useState("");
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [description, setDescription] = useState("");
  const [outputQuantity, setOutputQuantity] = useState("1");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);
  const keyFor = useIdempotencyKeys();

  const dirty = code !== "" || name !== "" || item !== null || description !== "" || outputQuantity !== "1";

  const save = useMutation((input: BomIn) => createBom(input, { idempotencyKey: keyFor(input) }), {
    onSuccess: (saved) => onCreated(saved),
  });

  function submit() {
    const next: Record<string, string> = {};
    const trimmedCode = code.trim();
    if (trimmedCode === "") next.code = "Enter a code for this BOM.";
    else if (trimmedCode.length > 40) next.code = "Use 40 characters or fewer.";
    else if (!CODE.test(trimmedCode)) next.code = "Use letters, digits, dots, dashes and underscores only.";
    if (name.trim() === "") next.name = "Enter a name for this BOM.";
    else if (name.trim().length > 200) next.name = "Use 200 characters or fewer.";
    if (item === null) next.item_id = "Choose the item this BOM produces.";
    const qtyError = qtyFieldError(outputQuantity);
    if (qtyError) next.output_quantity = qtyError;
    setErrors(next);
    if (Object.keys(next).length > 0 || item === null) {
      if (next.code) firstFieldRef.current?.focus();
      return;
    }
    void save.mutate({
      code: trimmedCode,
      name: name.trim(),
      item_id: item.id,
      description: description.trim(),
      output_quantity: outputQuantity.trim(),
    });
  }

  const fieldError = (field: string) => errors[field] ?? save.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New BOM"
      description="A bill of materials names what goes into a produced item. It starts with a draft version you can fill in."
      submitLabel="Create BOM"
      pendingLabel="Creating…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="Code"
        value={code}
        onChange={(e) => setCode(e.target.value)}
        maxLength={40}
        required
        error={fieldError("code")}
      />
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        maxLength={200}
        required
        error={fieldError("name")}
      />
      <OrderItemPicker
        label="Produced item"
        value={item}
        onChange={setItem}
        error={fieldError("item_id")}
      />
      <TextField
        label="Output quantity"
        inputMode="decimal"
        value={outputQuantity}
        onChange={(e) => setOutputQuantity(e.target.value)}
        helper="The batch size the component quantities are stated for."
        required
        error={fieldError("output_quantity")}
      />
      <TextArea
        label="Description"
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        error={fieldError("description")}
      />
    </FormDialog>
  );
}
