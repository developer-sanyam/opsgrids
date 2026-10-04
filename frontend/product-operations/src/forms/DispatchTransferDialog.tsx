import { useRef, useState } from "react";

import { dispatchTransfer } from "@/api/movements";
import type { TransferDispatchIn, TransferOut } from "@/api/movementTypes";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { stockRefusals } from "@/lib/stockRefusal";
import { useSubmissionKeys } from "@/lib/useSubmissionKeys";
import { errorProp } from "@/movements/errorProp";
import { StockRefusalAlert } from "@/movements/StockRefusalAlert";
import { FormDialog, TextArea, TextField } from "@/ui";

interface DispatchTransferDialogProps {
  transfer: TransferOut;
  onClose: () => void;
  onDispatched: (transfer: TransferOut) => void;
}

/**
 * `POST /operations/inventory/transfers/{id}:dispatch` — `operations.inventory.transfer.dispatch`.
 * Stock leaves the source warehouse and is held in transit until the destination receives it. If
 * the source no longer has it the refusal names the numbers. One `Idempotency-Key` per distinct
 * request, so a double click or a retry after a lost answer dispatches once.
 */
export function DispatchTransferDialog({ transfer, onClose, onDispatched }: DispatchTransferDialogProps) {
  const [note, setNote] = useState("");
  const [expectedOn, setExpectedOn] = useState(transfer.expected_on ?? "");
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const keyFor = useSubmissionKeys();

  const dispatch = useMutation(
    (input: TransferDispatchIn) => dispatchTransfer(transfer.id, input, keyFor(input)),
    { onSuccess: (saved) => onDispatched(saved) },
  );
  const refusals = stockRefusals(dispatch.error);

  function submit() {
    void dispatch.mutate({
      ...(note.trim() !== "" ? { note: note.trim() } : {}),
      ...(expectedOn !== "" ? { expected_on: expectedOn } : {}),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Dispatch ${transfer.number}`}
      description={`Stock leaves ${transfer.from_warehouse.name} now and is held in transit until ${transfer.to_warehouse.name} receives it.`}
      submitLabel="Dispatch transfer"
      pendingLabel="Dispatching…"
      pending={dispatch.pending}
      initialFocusRef={noteRef}
      dirty={note !== ""}
      {...(refusals.length > 0 ? {} : formDialogError(dispatch.error, dispatch.fieldErrors))}
    >
      <TextField
        label="Expected arrival"
        type="date"
        value={expectedOn}
        onChange={(e) => setExpectedOn(e.target.value)}
        optional
        {...errorProp(dispatch.fieldErrors.expected_on)}
      />
      <TextArea
        ref={noteRef}
        label="Note"
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={500}
        counterFrom={400}
        {...errorProp(dispatch.fieldErrors.note)}
      />
      <StockRefusalAlert refusals={refusals} action="dispatch this transfer" />
    </FormDialog>
  );
}
