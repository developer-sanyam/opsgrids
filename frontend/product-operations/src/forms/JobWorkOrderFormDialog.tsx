import { useState } from "react";

import { createJobWorkOrder } from "@/api/client";
import type { InventoryItemOut, JobWorkOrderDetailOut, JobWorkOrderIn } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { SkuPicker } from "@/forms/SkuPicker";
import { SupplierPicker } from "@/forms/SupplierPicker";
import { formDialogError } from "@/lib/formError";
import { parseQtyInput } from "@/lib/jobwork";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { Button, FormDialog, Switch, TextArea, TextField } from "@/ui";
import type { ComboboxOption } from "@/ui";
import { CloseIcon, PlusIcon } from "@opsgrids/ui";

interface JobWorkOrderFormDialogProps {
  onClose: () => void;
  onCreated: (order: JobWorkOrderDetailOut) => void;
}

interface Row {
  /** Stable across removals, so deleting an earlier row does not hand its state to the next. */
  key: string;
  option: ComboboxOption | null;
  item: InventoryItemOut | null;
  quantity: string;
  error?: string;
}

function blankRow(): Row {
  return { key: crypto.randomUUID(), option: null, item: null, quantity: "" };
}

/**
 * `POST /operations/jobwork/orders` — `operations.jobwork.order.write:ORG`. Drafts an order; no
 * stock moves until it is approved and issued. Quantities stay strings from the keyboard to the
 * wire (`lib/jobwork.ts`), validated against the item's unit — a fraction of a piece is refused
 * here the same way the server refuses it.
 */
export function JobWorkOrderFormDialog({ onClose, onCreated }: JobWorkOrderFormDialogProps) {
  const [supplier, setSupplier] = useState<ComboboxOption | null>(null);
  const [process, setProcess] = useState("");
  const [dueDate, setDueDate] = useState("");
  const [qcRequired, setQcRequired] = useState(false);
  const [notes, setNotes] = useState("");
  const [rows, setRows] = useState<Row[]>(() => [blankRow()]);
  const [supplierError, setSupplierError] = useState<string | undefined>(undefined);
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const keyFor = useSubmissionKeys();

  const dirty =
    supplier !== null ||
    process !== "" ||
    dueDate !== "" ||
    qcRequired ||
    notes !== "" ||
    rows.length > 1 ||
    rows.some((r) => r.item !== null || r.quantity !== "");

  const create = useMutation(
    (input: JobWorkOrderIn) => createJobWorkOrder(input, keyFor(input)),
    { onSuccess: (order) => onCreated(order) },
  );

  function patchRow(key: string, patch: Partial<Row>) {
    setRows((current) => current.map((r) => (r.key === key ? { ...r, ...patch } : r)));
  }

  function submit() {
    setSupplierError(undefined);
    setFormError(undefined);
    if (!supplier) {
      setSupplierError("Choose the vendor who will process the material.");
      return;
    }
    let failed = false;
    const materials: JobWorkOrderIn["materials"] = [];
    const checked = rows.map((row): Row => {
      if (!row.item) {
        failed = true;
        return { ...row, error: "Choose the material to send." };
      }
      const parsed = parseQtyInput(row.quantity, row.item.uom);
      if (!parsed.ok) {
        failed = true;
        return { ...row, error: parsed.error };
      }
      if (parsed.milli <= 0) {
        failed = true;
        return { ...row, error: "Quantity must be greater than zero." };
      }
      materials.push({ item_id: row.item.id, quantity: parsed.qty });
      return { key: row.key, option: row.option, item: row.item, quantity: row.quantity };
    });
    const chosen = materials.map((m) => m.item_id);
    if (!failed && new Set(chosen).size !== chosen.length) {
      setFormError("Each material can appear once. Merge the repeated lines.");
      failed = true;
    }
    setRows(checked);
    if (failed) return;

    void create.mutate({
      supplier_id: supplier.id,
      process: process.trim(),
      due_date: dueDate === "" ? null : dueDate,
      qc_required: qcRequired,
      notes: notes.trim(),
      materials,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="New job-work order"
      description="Material you will send to an outside processor. Nothing leaves stock until the order is approved and issued."
      submitLabel="Create draft"
      pendingLabel="Creating…"
      pending={create.pending}
      dirty={dirty}
      size="lg"
      {...formDialogError(create.error, create.fieldErrors)}
    >
      <SupplierPicker
        value={supplier}
        onChange={setSupplier}
        onPick={() => undefined}
        {...(supplierError !== undefined ? { error: supplierError } : {})}
      />
      <div className="grid gap-4 sm:grid-cols-2">
        <TextField
          label="Process"
          value={process}
          onChange={(e) => setProcess(e.target.value)}
          placeholder="e.g. Dyeing"
          maxLength={120}
        />
        <TextField
          label="Due back by"
          type="date"
          value={dueDate}
          onChange={(e) => setDueDate(e.target.value)}
        />
      </div>

      <fieldset className="flex flex-col gap-3">
        <legend className="text-sm font-semibold text-ink">Material to send</legend>
        {rows.map((row, index) => (
          <div key={row.key} className="flex flex-col gap-2 rounded-tile border border-line p-3">
            <div className="grid gap-3 sm:grid-cols-3 sm:items-end">
              <SkuPicker
                value={row.option}
                onChange={(option) => patchRow(row.key, { option, error: "" })}
                onPick={(item) => patchRow(row.key, { item })}
              />
              <TextField
                label={row.item ? `Quantity (${row.item.uom})` : "Quantity"}
                inputMode="decimal"
                value={row.quantity}
                onChange={(e) => patchRow(row.key, { quantity: e.target.value, error: "" })}
                placeholder="82.5"
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`Remove material line ${index + 1}`}
                disabled={rows.length === 1}
                onClick={() => setRows((current) => current.filter((r) => r.key !== row.key))}
              >
                <CloseIcon className="size-4" />
              </Button>
            </div>
            {row.error ? <p className="text-sm text-danger">{row.error}</p> : null}
          </div>
        ))}
        <Button
          type="button"
          variant="secondary"
          size="sm"
          className="w-fit"
          onClick={() => setRows((current) => [...current, blankRow()])}
        >
          <PlusIcon className="size-4" />
          Add material
        </Button>
      </fieldset>

      <Switch
        label="Quality check on return"
        description={
          qcRequired
            ? "Returned goods go to Quality hold and a QC request is raised."
            : "Returned goods go straight to Available."
        }
        checked={qcRequired}
        onChange={setQcRequired}
      />
      <TextArea label="Notes" value={notes} onChange={(e) => setNotes(e.target.value)} maxLength={1000} />
      {formError ? <p className="text-sm text-danger">{formError}</p> : null}
    </FormDialog>
  );
}
