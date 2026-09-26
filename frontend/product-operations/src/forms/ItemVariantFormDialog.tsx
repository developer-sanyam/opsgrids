import { useRef, useState } from "react";

import { createItemVariant, updateItemVariant } from "@/api/client";
import type { ItemVariantIn, ItemVariantOut, ItemVariantPatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Switch, TextField } from "@/ui";

const SKU_PATTERN = /^[A-Za-z0-9._-]+$/;

interface ItemVariantFormDialogProps {
  itemId: string;
  /** `null` creates. */
  variant: ItemVariantOut | null;
  onClose: () => void;
  onSaved: (variant: ItemVariantOut) => void;
}

/**
 * Create or edit a purchasable/stockable variant of an item master row — `POST` /
 * `PATCH /operations/inventory/{id}/variants`. `sku` is create-only.
 */
export function ItemVariantFormDialog({
  itemId,
  variant,
  onClose,
  onSaved,
}: ItemVariantFormDialogProps) {
  const [sku, setSku] = useState(variant?.sku ?? "");
  const [name, setName] = useState(variant?.name ?? "");
  const [isActive, setIsActive] = useState(variant?.is_active ?? true);

  const [skuError, setSkuError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `variant` never changes under it.
  const dirty =
    sku !== (variant?.sku ?? "") ||
    name !== (variant?.name ?? "") ||
    isActive !== (variant?.is_active ?? true);

  const save = useMutation(
    (input: ItemVariantIn | ItemVariantPatch) =>
      variant
        ? updateItemVariant(itemId, variant.id, input as ItemVariantPatch)
        : createItemVariant(itemId, input as ItemVariantIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  function submit() {
    setSkuError(undefined);
    setNameError(undefined);

    const trimmedSku = sku.trim();
    if (!variant && (trimmedSku.length < 1 || !SKU_PATTERN.test(trimmedSku))) {
      setSkuError("Letters, numbers, dots, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the variant a name.");
      return;
    }

    if (variant) {
      void save.mutate({ name: trimmedName, is_active: isActive });
      return;
    }
    void save.mutate({ sku: trimmedSku, name: trimmedName });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={variant ? `Edit ${variant.sku}` : "Add variant"}
      submitLabel={variant ? "Save changes" : "Add variant"}
      pendingLabel={variant ? "Saving…" : "Adding…"}
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      dirty={dirty}
      {...formDialogError(save.error, save.fieldErrors)}
    >
      <TextField
        ref={firstFieldRef}
        label="SKU"
        value={sku}
        onChange={(e) => setSku(e.target.value)}
        disabled={variant !== null}
        required
        helper={variant ? "A SKU can't change once created." : "Letters, numbers, . - and _."}
        {...((skuError ?? save.fieldErrors.sku) !== undefined ? { error: skuError ?? save.fieldErrors.sku } : {})}
      />
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...((nameError ?? save.fieldErrors.name) !== undefined ? { error: nameError ?? save.fieldErrors.name } : {})}
      />
      {variant ? (
        <Switch
          checked={isActive}
          onChange={setIsActive}
          label="Active"
          description={isActive ? "Available to pick." : "Hidden, but its history is kept."}
        />
      ) : null}
    </FormDialog>
  );
}
