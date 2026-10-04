import { useState } from "react";

import { setMaterialSubstitute } from "@/api/client";
import type {
  BomSubstituteOut,
  MaterialSubstituteIn,
  ProductionMaterialOut,
  ProductionOrderDetailOut,
} from "@/api/types";
import { formatQty } from "@/lib/quantity";
import { formDialogError } from "@/lib/formError";
import { useMutation } from "@opsgrids/client";
import { FormDialog, RadioGroup } from "@/ui";

interface SubstituteMaterialDialogProps {
  orderId: string;
  material: ProductionMaterialOut;
  /** The substitutes the pinned BOM version declares for this material's line. */
  substitutes: BomSubstituteOut[];
  onClose: () => void;
  onDone: (order: ProductionOrderDetailOut) => void;
}

const ORIGINAL = "original";

/**
 * `POST .../orders/{id}/materials/{materialId}/substitute` — `...production_order.write`. Switches a
 * material to one of its BOM line's declared substitutes, or back to the BOM's own item. Only a
 * draft or approved order offers it; the server refuses it once material has moved.
 */
export function SubstituteMaterialDialog({ orderId, material, substitutes, onClose, onDone }: SubstituteMaterialDialogProps) {
  const current = material.is_substitute ? material.item_id : ORIGINAL;
  const [choice, setChoice] = useState<string>(
    substitutes.find((s) => s.substitute_item_id === current)?.substitute_item_id ?? ORIGINAL,
  );

  const save = useMutation(
    (input: MaterialSubstituteIn) => setMaterialSubstitute(orderId, material.id, input),
    { onSuccess: (saved) => onDone(saved) },
  );

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={() => void save.mutate(choice === ORIGINAL ? {} : { substitute_item_id: choice })}
      title={`Use substitute for ${material.component_sku}`}
      description="Choose what this order draws for this line."
      submitLabel="Use selection"
      pendingLabel="Saving…"
      pending={save.pending}
      dirty={choice !== current}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <RadioGroup
        legend="Material"
        name="material-substitute"
        value={choice}
        onChange={setChoice}
        options={[
          { value: ORIGINAL, label: `${material.component_sku} (the BOM's own item)` },
          ...substitutes.map((s) => ({
            value: s.substitute_item_id,
            label: `${s.sku} — ${s.name}`,
            description: `Ratio ${formatQty(s.ratio)}, priority ${s.priority}.`,
          })),
        ]}
        error={save.fieldErrors.substitute_item_id}
      />
    </FormDialog>
  );
}
