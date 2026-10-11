import { useState } from "react";

import { returnProductionMaterials } from "@/api/client";
import type { ProductionActionOut, ProductionOrderDetailOut, ReturnIn, ReturnLineIn } from "@/api/types";
import { useLocationOptions } from "@/data/useLocationOptions";
import { useOrderAction } from "@/forms/production/useOrderAction";
import { formatQty, qtyFieldError, qtyToMilli, isQtyInput } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { FormDialog, TextField } from "@/ui";

interface ReturnDialogProps {
  order: ProductionOrderDetailOut;
  onClose: () => void;
  onDone: (result: ProductionActionOut) => void;
}

/**
 * `POST .../orders/{id}/returns` — `...production_order.issue`. Puts unused WIP material back into
 * stock, per pick (a pick is one material from one location, lot or roll). Each pick starts on all
 * of its WIP; clear a quantity to leave that pick alone. The server's 409 (more than is in WIP) is
 * shown as the dialog's message.
 */
export function ReturnDialog({ order, onClose, onDone }: ReturnDialogProps) {
  const picks = order.picks.filter((p) => qtyToMilli(p.wip) > 0);
  const [quantities, setQuantities] = useState<Record<string, string>>(() =>
    Object.fromEntries(picks.map((p) => [p.id, p.wip])),
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [formError, setFormError] = useState<string | undefined>(undefined);
  const locations = useLocationOptions();

  const give = useOrderAction<ReturnIn>((input, options) => returnProductionMaterials(order.id, input, options), onDone);

  function submit() {
    const next: Record<string, string> = {};
    const lines: ReturnLineIn[] = [];
    for (const pick of picks) {
      const raw = (quantities[pick.id] ?? "").trim();
      if (raw === "") continue;
      const error = qtyFieldError(raw);
      if (error) {
        next[pick.id] = error;
        continue;
      }
      if (isQtyInput(raw) && qtyToMilli(raw) > qtyToMilli(pick.wip)) {
        next[pick.id] = `Only ${formatQty(pick.wip)} is in WIP.`;
        continue;
      }
      lines.push({ pick_id: pick.id, quantity: raw });
    }
    setErrors(next);
    setFormError(lines.length === 0 && Object.keys(next).length === 0 ? "Enter a quantity for at least one pick." : undefined);
    if (Object.keys(next).length > 0 || lines.length === 0) return;
    void give.mutate({ lines });
  }

  const skuOf = (materialId: string) => order.materials.find((m) => m.id === materialId)?.sku ?? "Material";
  const dirty = picks.some((p) => quantities[p.id] !== p.wip);

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title="Return material to stock"
      description="Unused material in work in progress goes back to where it came from."
      submitLabel="Return material"
      pendingLabel="Returning…"
      pending={give.pending}
      dirty={dirty}
      {...(formError ? { error: formError } : formDialogError(give.error, give.fieldErrors))}
    >
      {picks.length === 0 ? <p className="text-sm text-muted">Nothing is in work in progress.</p> : null}
      {picks.map((pick) => {
        const material = order.materials.find((m) => m.id === pick.material_id);
        return (
          <TextField
            key={pick.id}
            label={`${skuOf(pick.material_id)} from ${locations.label(pick.source_location_id)}`}
            inputMode="decimal"
            value={quantities[pick.id] ?? ""}
            onChange={(e) => setQuantities((current) => ({ ...current, [pick.id]: e.target.value }))}
            helper={`${formatQty(pick.wip)} ${material?.uom ?? ""} in WIP.`}
            optional
            error={errors[pick.id]}
          />
        );
      })}
    </FormDialog>
  );
}
