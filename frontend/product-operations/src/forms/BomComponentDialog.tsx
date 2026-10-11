import { useRef, useState } from "react";

import { addBomComponent, updateBomComponent } from "@/api/client";
import type { BomComponentIn, BomComponentOut, BomComponentPatch, BomVersionDetailOut } from "@/api/types";
import { OrderItemPicker } from "@/forms/OrderItemPicker";
import { pctFieldError, qtyFieldError } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useMutation } from "@opsgrids/client";
import { FormDialog, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";

interface BomComponentDialogProps {
  bomId: string;
  versionId: string;
  /** Present when editing a line; absent when adding one. */
  component?: BomComponentOut;
  onClose: () => void;
  onSaved: (version: BomVersionDetailOut) => void;
}

/**
 * Add (`POST .../components`) or edit (`PATCH .../components/{id}`) a component line of a draft
 * version — `operations.manufacturing.bom.write`. The component's item cannot change on an
 * existing line (remove it and add another). Adding an item that would make the BOM contain
 * itself is a 409 the server words; it is shown verbatim as the dialog's message.
 */
export function BomComponentDialog({ bomId, versionId, component, onClose, onSaved }: BomComponentDialogProps) {
  const editing = component !== undefined;
  const [item, setItem] = useState<ComboboxOption | null>(null);
  const [quantity, setQuantity] = useState(component?.quantity ?? "");
  const [scrap, setScrap] = useState(component?.scrap_percent ?? "0");
  const [from, setFrom] = useState(component?.effective_from ?? "");
  const [to, setTo] = useState(component?.effective_to ?? "");
  const [notes, setNotes] = useState(component?.notes ?? "");
  const [errors, setErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const dirty = editing
    ? quantity !== component.quantity ||
      scrap !== component.scrap_percent ||
      from !== (component.effective_from ?? "") ||
      to !== (component.effective_to ?? "") ||
      notes !== component.notes
    : item !== null || quantity !== "" || scrap !== "0" || from !== "" || to !== "" || notes !== "";

  const save = useMutation(
    (input: { create: BomComponentIn } | { patch: BomComponentPatch }) =>
      "create" in input
        ? addBomComponent(bomId, versionId, input.create)
        : updateBomComponent(bomId, versionId, component?.id ?? "", input.patch),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    const next: Record<string, string> = {};
    if (!editing && item === null) next.component_item_id = "Choose the component item.";
    const qtyError = qtyFieldError(quantity);
    if (qtyError) next.quantity = qtyError;
    const scrapError = pctFieldError(scrap);
    if (scrapError) next.scrap_percent = scrapError;
    if (from !== "" && to !== "" && to < from) next.effective_to = "The end date can't be before the start date.";
    setErrors(next);
    if (Object.keys(next).length > 0) return;

    const common = {
      quantity: quantity.trim(),
      scrap_percent: scrap.trim(),
      effective_from: from === "" ? null : from,
      effective_to: to === "" ? null : to,
      notes: notes.trim(),
    };
    if (editing) void save.mutate({ patch: common });
    else if (item) void save.mutate({ create: { component_item_id: item.id, ...common } });
  }

  const fieldError = (field: string) => errors[field] ?? save.fieldErrors[field];

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={editing ? `Edit component ${component.sku}` : "Add component"}
      submitLabel={editing ? "Save component" : "Add component"}
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      {editing ? (
        <p className="text-sm text-ink">
          <span className="font-semibold">{component.sku}</span>
          <span className="text-muted"> · {component.name}</span>
        </p>
      ) : (
        <OrderItemPicker
          label="Component item"
          value={item}
          onChange={setItem}
          error={fieldError("component_item_id")}
        />
      )}
      <TextField
        ref={firstFieldRef}
        label="Quantity"
        inputMode="decimal"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        helper={`Per batch${editing ? `, in ${component.uom}` : ""}.`}
        required
        error={fieldError("quantity")}
      />
      <TextField
        label="Scrap percent"
        inputMode="decimal"
        value={scrap}
        onChange={(e) => setScrap(e.target.value)}
        helper="The allowance added on top for expected scrap, 0 to 100."
        required
        error={fieldError("scrap_percent")}
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
        rows={2}
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        optional
        maxLength={300}
        counterFrom={250}
        error={fieldError("notes")}
      />
    </FormDialog>
  );
}
