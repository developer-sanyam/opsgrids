import { useRef, useState } from "react";

import { adjustStock } from "@/api/client";
import type { InventoryItemOut, StockAdjustmentIn, StockMovementReason } from "@/api/types";
import { today } from "@/lib/now";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, RadioGroup, Select, TextArea, TextField } from "@/ui";

type Mode = "delta" | "count";

const REASON_OPTIONS: { value: StockMovementReason; label: string }[] = [
  { value: "damaged", label: "Damaged" },
  { value: "lost", label: "Lost" },
  { value: "found", label: "Found" },
  { value: "consumed", label: "Consumed" },
  { value: "returned", label: "Returned" },
  { value: "other", label: "Other" },
];

interface StockAdjustmentDialogProps {
  item: InventoryItemOut;
  onClose: () => void;
  onSaved: (item: InventoryItemOut) => void;
}

/**
 * `POST /operations/inventory/{id}/adjustments`. One dialog, two modes: "Add / remove" sends a
 * signed `quantity_delta` with a required reason; "Set counted quantity" sends an absolute
 * `counted_on_hand`, where a reason makes no sense — it's a fact, not a cause. Both preview the
 * resulting on-hand before submit.
 */
export function StockAdjustmentDialog({ item, onClose, onSaved }: StockAdjustmentDialogProps) {
  const [mode, setMode] = useState<Mode>("delta");
  const [delta, setDelta] = useState("");
  const [counted, setCounted] = useState(String(item.on_hand));
  const [reason, setReason] = useState<StockMovementReason | "">("");
  const [note, setNote] = useState("");
  const [occurredOn, setOccurredOn] = useState(today());

  const [deltaError, setDeltaError] = useState<string | undefined>(undefined);
  const [countedError, setCountedError] = useState<string | undefined>(undefined);
  const [reasonError, setReasonError] = useState<string | undefined>(undefined);
  const [dateError, setDateError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  const save = useMutation((input: StockAdjustmentIn) => adjustStock(item.id, input), {
    onSuccess: (saved) => onSaved(saved),
  });

  const deltaValue = Number.parseInt(delta, 10);
  const countedValue = Number.parseInt(counted, 10);
  const preview =
    mode === "delta"
      ? Number.isFinite(deltaValue)
        ? item.on_hand + deltaValue
        : item.on_hand
      : Number.isFinite(countedValue)
        ? countedValue
        : item.on_hand;

  function submit() {
    setDeltaError(undefined);
    setCountedError(undefined);
    setReasonError(undefined);
    setDateError(undefined);

    if (occurredOn > today()) {
      setDateError("The date can't be in the future.");
      return;
    }

    if (mode === "delta") {
      if (delta.trim() === "" || !/^-?\d+$/.test(delta.trim()) || Number(delta) === 0) {
        setDeltaError("Enter a non-zero whole number — negative to remove stock.");
        firstFieldRef.current?.focus();
        return;
      }
      if (reason === "") {
        setReasonError("Choose a reason.");
        return;
      }
      void save.mutate({
        quantity_delta: Number(delta),
        reason,
        note: note.trim(),
        occurred_on: occurredOn,
      });
      return;
    }

    if (counted.trim() === "" || !/^\d+$/.test(counted.trim())) {
      setCountedError("Enter a whole number, zero or more.");
      firstFieldRef.current?.focus();
      return;
    }
    void save.mutate({
      counted_on_hand: Number(counted),
      note: note.trim(),
      occurred_on: occurredOn,
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Adjust stock — ${item.name}`}
      description={`${item.sku} · ${item.warehouse}`}
      submitLabel="Record adjustment"
      pendingLabel="Recording…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <RadioGroup
        legend="Adjustment type"
        name="adjustment-mode"
        value={mode}
        onChange={(v) => setMode(v as Mode)}
        options={[
          { value: "delta", label: "Add / remove", description: "Move the quantity by an amount, with a reason." },
          { value: "count", label: "Set counted quantity", description: "Record what a physical count found." },
        ]}
      />

      {mode === "delta" ? (
        <>
          <TextField
            ref={firstFieldRef}
            label="Quantity change"
            inputMode="numeric"
            value={delta}
            onChange={(e) => setDelta(e.target.value)}
            helper={`Positive adds, negative removes ${item.uom}.`}
            {...((deltaError ?? save.fieldErrors.quantity_delta) !== undefined ? { error: deltaError ?? save.fieldErrors.quantity_delta } : {})}
          />
          <Select
            label="Reason"
            value={reason}
            onChange={(e) => setReason(e.target.value as StockMovementReason)}
            placeholder="Choose a reason"
            options={REASON_OPTIONS}
            {...((reasonError ?? save.fieldErrors.reason) !== undefined ? { error: reasonError ?? save.fieldErrors.reason } : {})}
          />
        </>
      ) : (
        <TextField
          ref={firstFieldRef}
          label="Counted quantity"
          inputMode="numeric"
          value={counted}
          onChange={(e) => setCounted(e.target.value)}
          helper={`In ${item.uom}.`}
          {...((countedError ?? save.fieldErrors.counted_on_hand) !== undefined ? { error: countedError ?? save.fieldErrors.counted_on_hand } : {})}
        />
      )}

      <TextField
        label="Date"
        type="date"
        value={occurredOn}
        max={today()}
        onChange={(e) => setOccurredOn(e.target.value)}
        {...((dateError ?? save.fieldErrors.occurred_on) !== undefined ? { error: dateError ?? save.fieldErrors.occurred_on } : {})}
      />

      <TextArea
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        {...((save.fieldErrors.note) !== undefined ? { error: save.fieldErrors.note } : {})}
      />

      <p className="tnum rounded-tile bg-ground px-4 py-3 text-sm font-semibold text-ink" aria-live="polite">
        New on hand: {preview} {item.uom}
        {preview < 0 ? (
          <span className="ml-2 font-normal text-negative">This would go below zero.</span>
        ) : null}
      </p>
    </FormDialog>
  );
}
