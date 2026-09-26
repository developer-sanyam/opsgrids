import { useRef, useState } from "react";

import { createItemCategory, updateItemCategory } from "@/api/client";
import type { ItemCategoryIn, ItemCategoryOut, ItemCategoryPatch } from "@/api/types";
import { useMutation } from "@opsgrids/client";
import { formDialogError } from "@/lib/formError";
import { FormDialog, Select, TextField } from "@/ui";

const CODE_PATTERN = /^[A-Za-z0-9._-]+$/;

interface ItemCategoryFormDialogProps {
  /** `null` creates. */
  category: ItemCategoryOut | null;
  /** Every other live category, for the parent picker — excludes `category` itself and (on
   *  edit) anything that is already its descendant, which the server would refuse as a cycle. */
  candidates: ItemCategoryOut[];
  onClose: () => void;
  onSaved: (category: ItemCategoryOut) => void;
}

/**
 * True if `candidateId` sits under `ancestorId` somewhere in the tree. `seen` bounds the walk to
 * at most one pass over the whole set: if the data already contains a cycle (it should not, but
 * a bug elsewhere, or data restored from before this check existed, could still produce one),
 * revisiting a node is the signal to stop and treat it as a match rather than loop forever.
 */
function isDescendant(all: ItemCategoryOut[], ancestorId: string, candidateId: string): boolean {
  const seen = new Set<string>();
  let current = all.find((c) => c.id === candidateId);
  while (current?.parent_id) {
    if (current.parent_id === ancestorId) return true;
    if (seen.has(current.id)) return true; // already a cycle; do not walk it forever
    seen.add(current.id);
    current = all.find((c) => c.id === current?.parent_id);
  }
  return false;
}

/**
 * Create or edit an item master category — `POST` / `PATCH /operations/inventory/categories`.
 * `code` is create-only, like a warehouse's; `parent_id` is validated against a cycle server-side
 * too, but the picker excludes the obviously-invalid choices so most people never see that error.
 */
export function ItemCategoryFormDialog({
  category,
  candidates,
  onClose,
  onSaved,
}: ItemCategoryFormDialogProps) {
  const [code, setCode] = useState(category?.code ?? "");
  const [name, setName] = useState(category?.name ?? "");
  const [parentId, setParentId] = useState(category?.parent_id ?? "");

  const [codeError, setCodeError] = useState<string | undefined>(undefined);
  const [nameError, setNameError] = useState<string | undefined>(undefined);
  const firstFieldRef = useRef<HTMLInputElement>(null);

  // Compared against the same seed the fields above initialised from — this form unmounts on
  // close (FormDialog's resetting-is-structural note), so `category` never changes under it.
  const dirty =
    code !== (category?.code ?? "") ||
    name !== (category?.name ?? "") ||
    parentId !== (category?.parent_id ?? "");

  const save = useMutation(
    (input: ItemCategoryIn | ItemCategoryPatch) =>
      category
        ? updateItemCategory(category.id, input as ItemCategoryPatch)
        : createItemCategory(input as ItemCategoryIn),
    { onSuccess: (saved) => onSaved(saved) },
  );

  const parentOptions = candidates
    .filter((c) => c.id !== category?.id)
    .filter((c) => !category || !isDescendant(candidates, category.id, c.id))
    .map((c) => ({ value: c.id, label: c.name }));

  function submit() {
    setCodeError(undefined);
    setNameError(undefined);

    const trimmedCode = code.trim();
    if (!category && (trimmedCode.length < 1 || !CODE_PATTERN.test(trimmedCode))) {
      setCodeError("Letters, numbers, dots, hyphens and underscores only.");
      firstFieldRef.current?.focus();
      return;
    }
    const trimmedName = name.trim();
    if (trimmedName.length < 1) {
      setNameError("Give the category a name.");
      return;
    }

    if (category) {
      void save.mutate({ name: trimmedName, parent_id: parentId === "" ? null : parentId });
      return;
    }
    void save.mutate({
      code: trimmedCode,
      name: trimmedName,
      ...(parentId === "" ? {} : { parent_id: parentId }),
    });
  }

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={category ? `Edit ${category.name}` : "New category"}
      submitLabel={category ? "Save changes" : "Create category"}
      pendingLabel={category ? "Saving…" : "Creating…"}
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
        disabled={category !== null}
        required
        helper={category ? "A category's code can't change." : "Letters, numbers, . - and _."}
        {...((codeError ?? save.fieldErrors.code) !== undefined
          ? { error: codeError ?? save.fieldErrors.code }
          : {})}
      />
      <TextField
        label="Name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        required
        {...((nameError ?? save.fieldErrors.name) !== undefined
          ? { error: nameError ?? save.fieldErrors.name }
          : {})}
      />
      <Select
        label="Parent category"
        value={parentId}
        onChange={(e) => setParentId(e.target.value)}
        placeholder="No parent — a top-level category"
        options={parentOptions}
        {...((save.fieldErrors.parent_id) !== undefined ? { error: save.fieldErrors.parent_id } : {})}
      />
    </FormDialog>
  );
}
