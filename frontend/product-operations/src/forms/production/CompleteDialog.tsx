import { useState } from "react";

import { completeProductionOrder } from "@/api/client";
import type { CompleteIn, ProductionActionOut, ProductionOrderDetailOut } from "@/api/types";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { hasLeftoverWip } from "@/lib/productionOrderActions";
import { formDialogError } from "@/lib/formError";
import { FormDialog, RadioGroup, TextArea } from "@/ui";

interface CompleteDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

type Leftover = "return" | "scrap";

/**
 * `POST .../orders/{id}/complete` — `...production_order.complete`. When material is still in WIP
 * the user must say what becomes of it: back to stock, or written off as wastage. The server
 * enforces that too (a 409 with its own sentence, shown here verbatim) — this only asks up front.
 */
export function CompleteDialog({ order, onClose, onDone }: CompleteDialogProps) {
  const leftover = hasLeftoverWip(order);
  const [choice, setChoice] = useState<Leftover | null>(null);
  const [note, setNote] = useState("");
  const [choiceError, setChoiceError] = useState<string | undefined>(undefined);

  const complete = useOrderAction<CompleteIn>((input, options) => completeProductionOrder(order.id, input, options), onDone);

  function submit() {
    if (leftover && choice === null) {
      setChoiceError("Choose what happens to the material left in WIP.");
      return;
    }
    setChoiceError(undefined);
    void complete.mutate({ note: note.trim(), ...(leftover && choice ? { leftover: choice } : {}) });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`Complete ${order.number}?`}
      description={`Produced ${order.produced_quantity} of ${order.planned_quantity} ${order.uom}. A completed order can't be changed.`}
      submitLabel="Complete order"
      pendingLabel="Completing…"
      pending={complete.pending}
      dirty={choice !== null || note !== ""}
      {...formDialogError(complete.error, complete.fieldErrors)}
    >
      {leftover ? (
        <RadioGroup
          legend="Material left in WIP"
          name="leftover"
          value={choice}
          onChange={(v) => setChoice(v as Leftover)}
          options={[
            { value: "return", label: "Return to stock", description: "Puts the leftover back where it was issued from." },
            { value: "scrap", label: "Scrap it", description: "Writes the leftover off as wastage." },
          ]}
          error={choiceError ?? complete.fieldErrors.leftover}
        />
      ) : null}
      <TextArea
        label="Note"
        rows={2}
        value={note}
        onChange={(e) => setNote(e.target.value)}
        optional
        maxLength={300}
        counterFrom={250}
        error={complete.fieldErrors.note}
      />
    </FormDialog>
  );
}
