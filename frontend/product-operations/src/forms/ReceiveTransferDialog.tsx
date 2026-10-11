import { useRef, useState } from "react";

import { receiveTransfer } from "@/api/movements";
import type { TransferOut, TransferReceiveIn } from "@/api/movementTypes";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { lineFieldErrors } from "@/lib/lineErrors";
import { isZeroQty, parseQty, subtractQty } from "@/lib/movementQty";
import { defaultReceiveValues, receivableLines, validateReceive } from "@/lib/receiveForm";
import type { ReceiveLineErrors, ReceiveLineValues } from "@/lib/receiveForm";
import { formatQty } from "@/lib/quantity";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { errorProp } from "@/movements/errorProp";
import { useDiscreteUoms, useLocationChoices } from "@/movements/useMovementReference";
import { FormDialog, InlineAlert, Select, TextArea, TextField } from "@/ui";

interface ReceiveTransferDialogProps {
  transfer: TransferOut;
  onClose: () => void;
  onReceived: (transfer: TransferOut) => void;
}

/** What is left in transit on a line if this receipt goes through — empty while a figure is not a
 *  valid number yet, so the hint never shows a half-typed result. */
function remainingAfter(inTransit: string, received: string, shortage: string, whole: boolean): string | null {
  const r = parseQty(received === "" ? "0" : received, { allowZero: true, whole });
  const s = parseQty(shortage === "" ? "0" : shortage, { allowZero: true, whole });
  if (!r.ok || !s.ok) return null;
  const left = subtractQty(subtractQty(inTransit, r.value), s.value);
  return left.startsWith("-") ? null : left;
}

/**
 * `POST /operations/inventory/transfers/{id}:receive` — `operations.inventory.transfer.receive`.
 *
 * Per line: what arrived, what is short, and the bin it goes into (the destination warehouse's
 * default bin unless changed). Leaving a line at zero/zero receives nothing on it, which is how a
 * partial receipt works: whatever is not accounted for stays in transit for the next receipt.
 * A shortage is declared lost in transit and **written off**; the dialog says so beside the figure.
 * One `Idempotency-Key` per distinct request, so a retry after a lost answer receives once.
 */
export function ReceiveTransferDialog({ transfer, onClose, onReceived }: ReceiveTransferDialogProps) {
  const lines = receivableLines(transfer.lines);
  const [values, setValues] = useState<ReceiveLineValues[]>(() => defaultReceiveValues(transfer.lines, ""));
  const [note, setNote] = useState("");
  const [errors, setErrors] = useState<Record<string, ReceiveLineErrors>>({});
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const [sentLines, setSentLines] = useState<string[]>([]);
  const firstRef = useRef<HTMLInputElement>(null);

  const bins = useLocationChoices(transfer.to_warehouse.id);
  const discrete = useDiscreteUoms();
  const keyFor = useSubmissionKeys();

  const save = useMutation(
    (input: TransferReceiveIn) => receiveTransfer(transfer.id, input, keyFor(input)),
    { onSuccess: (saved) => onReceived(saved) },
  );

  function update(lineId: string, patch: Partial<ReceiveLineValues>) {
    setValues((previous) => previous.map((v) => (v.lineId === lineId ? { ...v, ...patch } : v)));
  }

  // What the server said about a line, found through the order the lines were sent in.
  function serverErrorsFor(lineId: string): Record<string, string> {
    const index = sentLines.indexOf(lineId);
    return index === -1 ? {} : lineFieldErrors(save.fieldErrors, "lines", index);
  }

  const anyShortage = values.some((v) => {
    const parsed = parseQty(v.shortage === "" ? "0" : v.shortage, { allowZero: true });
    return parsed.ok && !isZeroQty(parsed.value);
  });

  function submit() {
    const effective = values.map((v) => ({ ...v, toLocationId: v.toLocationId || bins.defaultId }));
    const result = validateReceive(transfer.lines, effective, discrete, note);
    if (!result.ok) {
      setErrors(result.errors);
      setFormError(result.formError);
      firstRef.current?.focus();
      return;
    }
    setErrors({});
    setFormError(undefined);
    setSentLines(result.payload.lines.map((l) => l.line_id));
    void save.mutate(result.payload);
  }

  // A field error the screen has no control for must still be said somewhere.
  const unplaced =
    Object.keys(save.fieldErrors).length > 0 &&
    save.fieldErrors.note === undefined &&
    lines.every((line) => Object.keys(serverErrorsFor(line.id)).length === 0)
      ? Object.values(save.fieldErrors)[0]
      : undefined;

  const dirty = note !== "" || values.some((v) => v.shortage !== "0");

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Receive ${transfer.number}`}
      description={`Record what arrived at ${transfer.to_warehouse.name}. You can receive a transfer in several parts.`}
      submitLabel="Record receipt"
      pendingLabel="Recording…"
      pending={save.pending}
      initialFocusRef={firstRef}
      size="lg"
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      {lines.map((line, index) => {
        const value = values.find((v) => v.lineId === line.id);
        if (!value) return null;
        const local = errors[line.id] ?? {};
        const server = serverErrorsFor(line.id);
        const whole = discrete.has(line.uom);
        const left = remainingAfter(line.in_transit, value.received, value.shortage, whole);
        return (
          <fieldset key={line.id} className="flex flex-col gap-3 rounded-tile border border-line p-4">
            <legend className="px-1 text-sm font-semibold text-ink">
              {line.sku} · {line.item_name}
            </legend>
            <p className="tnum text-sm text-muted">
              Still in transit: {formatQty(line.in_transit)} {line.uom} (sent {formatQty(line.dispatched)}, received{" "}
              {formatQty(line.received)}, short {formatQty(line.shortage)})
            </p>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <TextField
                ref={index === 0 ? firstRef : undefined}
                label={`Received — ${line.sku}`}
                inputMode="decimal"
                value={value.received}
                onChange={(e) => update(line.id, { received: e.target.value })}
                helper={`In ${line.uom}, up to 3 decimals.`}
                {...errorProp(local.received, server.received)}
              />
              <TextField
                label={`Short — ${line.sku}`}
                inputMode="decimal"
                value={value.shortage}
                onChange={(e) => update(line.id, { shortage: e.target.value })}
                helper="Lost or never arrived. Written off."
                {...errorProp(local.shortage, server.short, server.shortage)}
              />
            </div>
            <Select
              label={`Destination bin — ${line.sku}`}
              value={value.toLocationId || bins.defaultId}
              onChange={(e) => update(line.id, { toLocationId: e.target.value })}
              options={bins.options}
              placeholder={bins.status === "loading" ? "Loading bins…" : "Choose a bin"}
              disabled={bins.status !== "success"}
              {...errorProp(local.location, server.to_location_id)}
            />
            {local.line !== undefined || server.line !== undefined ? (
              <p role="alert" className="text-sm text-negative">
                {local.line ?? server.line}
              </p>
            ) : null}
            {left !== null ? (
              <p className="tnum text-sm font-semibold text-ink" aria-live="polite">
                {isZeroQty(left)
                  ? "Nothing will be left in transit on this line."
                  : `${formatQty(left)} ${line.uom} will stay in transit.`}
              </p>
            ) : null}
          </fieldset>
        );
      })}

      {anyShortage ? (
        <InlineAlert tone="warning" title="A shortage is written off">
          Stock you mark as short is declared lost in transit and removed from your stock. It can't be received
          later.
        </InlineAlert>
      ) : null}

      <TextArea
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        {...errorProp(save.fieldErrors.note)}
      />

      {unplaced !== undefined ? (
        <p role="alert" className="text-sm text-negative">
          {unplaced}
        </p>
      ) : null}

      {formError !== undefined ? (
        <p role="alert" className="text-sm text-negative">
          {formError}
        </p>
      ) : null}
    </FormDialog>
  );
}
